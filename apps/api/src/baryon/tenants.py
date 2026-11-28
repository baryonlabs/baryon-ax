"""Tenant registry, quotas, and usage metering for pooled multi-tenancy.

``TENANCY_MODE=single`` (default) keeps today's behaviour: the registry is not
consulted, nothing is metered, and no file under ``TENANT_STORE`` is written.

``TENANCY_MODE=pooled`` runs one shared API for many customer organizations.
A tenant is an organization id (the OIDC organization claim) that a platform
administrator registered. Requests from an organization that is not an
*active* registered tenant are rejected before they reach an endpoint.

Storage follows the other Basic Core stores: small JSON files under one
directory, written atomically (temp file + ``os.replace``). Counters live in
process memory behind a lock and are flushed to disk at most every
``USAGE_FLUSH_SECONDS``. This is safe for one API process. Running several API
replicas needs the registry and the counters in shared storage (Redis or
PostgreSQL); see ``docs/multi-tenancy.md``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("baryon.ax.tenants")

TENANT_ID_PATTERN = re.compile(r"[a-z0-9-]{2,40}")
TENANT_STATUSES = ("active", "suspended", "deleting")
QUOTA_KEYS = ("max_documents", "max_storage_bytes", "chat_requests_per_day", "tokens_per_month")
USAGE_FIELDS = ("chat_requests", "prompt_tokens", "completion_tokens", "uploads", "upload_bytes")
DAILY_HISTORY_DAYS = 62
MONTHLY_HISTORY = 13
# Rough characters-per-token for mixed Korean/English text when the model
# server does not return ``usage``. Korean tokenizes denser than English, so 3
# over-counts English slightly and under-counts Korean slightly; it is an
# estimate for quota purposes, not a billing figure.
ESTIMATED_CHARS_PER_TOKEN = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def estimate_tokens(text: str) -> int:
    """Documented fallback when the upstream model does not report ``usage``."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / ESTIMATED_CHARS_PER_TOKEN))


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON so readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read %s; starting empty", path)
        return default


# --------------------------------------------------------------------------- settings


@dataclass
class TenancySettings:
    mode: str = "single"

    @classmethod
    def from_env(cls) -> "TenancySettings":
        mode = os.getenv("TENANCY_MODE", "single").strip().lower() or "single"
        if mode not in {"single", "pooled"}:
            raise ValueError("TENANCY_MODE must be 'single' or 'pooled'")
        return cls(mode=mode)

    @property
    def pooled(self) -> bool:
        return self.mode == "pooled"


def tenant_store_root() -> Path:
    return Path(os.getenv("TENANT_STORE", "/data/tenants"))


# --------------------------------------------------------------------------- registry


class TenantError(ValueError):
    """Invalid tenant input (HTTP 422)."""


class TenantExists(TenantError):
    """A tenant with this id already exists (HTTP 409)."""


def validate_tenant_id(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise TenantError("tenant id must match [a-z0-9-]{2,40}")
    return tenant_id


def normalize_quotas(quotas: dict[str, Any] | None, base: dict[str, Any] | None = None) -> dict[str, int | None]:
    """Merge quota updates; ``None`` means unlimited. Unknown keys are rejected."""
    result: dict[str, int | None] = {key: None for key in QUOTA_KEYS}
    if base:
        result.update({key: base.get(key) for key in QUOTA_KEYS})
    for key, value in (quotas or {}).items():
        if key not in QUOTA_KEYS:
            raise TenantError(f"unknown quota '{key}'")
        if value is None:
            result[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TenantError(f"quota '{key}' must be a non-negative integer or null")
        result[key] = value
    return result


@dataclass
class Tenant:
    id: str
    name: str
    status: str = "active"
    plan: str = "standard"
    quotas: dict[str, int | None] = field(default_factory=lambda: {key: None for key in QUOTA_KEYS})
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    notes: str = ""
    purged_at: str | None = None
    purge_summary: dict[str, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def active(self) -> bool:
        return self.status == "active"


class TenantRegistry:
    """JSON-backed registry. Reads are lock-free dict lookups; writes are atomic."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else tenant_store_root()
        self.path = self.root / "tenants.json"
        self._lock = threading.Lock()
        self._tenants: dict[str, Tenant] = {}
        self._load()

    def _load(self) -> None:
        payload = _read_json(self.path, {"tenants": []})
        for item in payload.get("tenants", []) if isinstance(payload, dict) else []:
            try:
                tenant = Tenant(**item)
                validate_tenant_id(tenant.id)
            except (TypeError, TenantError):
                continue
            tenant.quotas = normalize_quotas(tenant.quotas)
            self._tenants[tenant.id] = tenant

    def _save(self) -> None:
        atomic_write_json(self.path, {"schema_version": 1, "tenants": [tenant.as_dict() for tenant in self._tenants.values()]})

    def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def list(self, status: str | None = None) -> list[Tenant]:
        items = [tenant for tenant in self._tenants.values() if status is None or tenant.status == status]
        return sorted(items, key=lambda tenant: tenant.id)

    def create(
        self, tenant_id: str, name: str, *, plan: str = "standard",
        quotas: dict[str, Any] | None = None, notes: str = "",
    ) -> Tenant:
        validate_tenant_id(tenant_id)
        name = (name or "").strip()
        if not name:
            raise TenantError("tenant name is required")
        tenant = Tenant(
            id=tenant_id, name=name[:120], plan=(plan or "standard").strip()[:40] or "standard",
            quotas=normalize_quotas(quotas), notes=(notes or "")[:2000],
        )
        with self._lock:
            if tenant_id in self._tenants:
                raise TenantExists(f"tenant '{tenant_id}' already exists")
            self._tenants[tenant_id] = tenant
            self._save()
        return tenant

    def update(
        self, tenant_id: str, *, name: str | None = None, plan: str | None = None,
        quotas: dict[str, Any] | None = None, status: str | None = None, notes: str | None = None,
    ) -> Tenant | None:
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return None
            if status is not None and status not in TENANT_STATUSES:
                raise TenantError(f"status must be one of {', '.join(TENANT_STATUSES)}")
            if tenant.status == "deleting" and status not in (None, "deleting"):
                raise TenantError("a deleted tenant cannot be reactivated")
            if name is not None:
                if not name.strip():
                    raise TenantError("tenant name is required")
                tenant.name = name.strip()[:120]
            if plan is not None:
                tenant.plan = plan.strip()[:40] or tenant.plan
            if quotas is not None:
                tenant.quotas = normalize_quotas(quotas, tenant.quotas)
            if notes is not None:
                tenant.notes = notes[:2000]
            if status is not None:
                tenant.status = status
            tenant.updated_at = iso_now()
            self._save()
            return tenant

    def mark_purged(self, tenant_id: str, summary: dict[str, int]) -> Tenant | None:
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if not tenant:
                return None
            tenant.status = "deleting"
            tenant.purged_at = iso_now()
            tenant.purge_summary = dict(summary)
            tenant.updated_at = tenant.purged_at
            self._save()
            return tenant

    def remove(self, tenant_id: str) -> bool:
        """Hard-delete a registry record. Only for signup compensation.

        Normal offboarding goes through :meth:`mark_purged`, which keeps the
        record so the organization id cannot be reused by accident. A tenant
        that was created moments ago by a failed signup has no data or audit
        history, so removing it is safe and lets the person retry the same code.
        """
        with self._lock:
            if self._tenants.pop(tenant_id, None) is None:
                return False
            self._save()
            return True


# --------------------------------------------------------------------------- usage


def _day_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def _month_key(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def seconds_until_next_day(moment: datetime | None = None) -> int:
    moment = moment or utcnow()
    tomorrow = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - moment).total_seconds()))


def seconds_until_next_month(moment: datetime | None = None) -> int:
    moment = moment or utcnow()
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    start = moment.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((start - moment).total_seconds()))


def _empty_bucket() -> dict[str, int]:
    return {name: 0 for name in USAGE_FIELDS}


class UsageMeter:
    """Per-tenant daily and monthly counters (UTC buckets).

    Check-and-increment happens under one lock, so concurrent requests in the
    same process cannot both pass a limit. Disk writes are debounced: the file
    is rewritten atomically at most every ``flush_seconds``; ``flush()`` forces
    it (called on shutdown and after a purge).
    """

    def __init__(self, root: str | Path | None = None, flush_seconds: float | None = None, clock=utcnow) -> None:
        self.root = Path(root) if root else tenant_store_root()
        self.path = self.root / "usage.json"
        self.flush_seconds = float(os.getenv("USAGE_FLUSH_SECONDS", "5") if flush_seconds is None else flush_seconds)
        self.clock = clock
        self._lock = threading.Lock()
        self._dirty = False
        self._last_flush = 0.0
        payload = _read_json(self.path, {})
        self._data: dict[str, dict[str, dict[str, dict[str, int]]]] = payload.get("tenants", {}) if isinstance(payload, dict) else {}

    def _buckets(self, tenant_id: str, moment: datetime) -> tuple[dict[str, int], dict[str, int]]:
        tenant = self._data.setdefault(tenant_id, {"days": {}, "months": {}})
        day = tenant["days"].setdefault(_day_key(moment), _empty_bucket())
        month = tenant["months"].setdefault(_month_key(moment), _empty_bucket())
        for bucket in (day, month):
            for name in USAGE_FIELDS:
                bucket.setdefault(name, 0)
        return day, month

    def _prune(self, tenant_id: str, moment: datetime) -> None:
        tenant = self._data.get(tenant_id)
        if not tenant:
            return
        cutoff = _day_key(moment - timedelta(days=DAILY_HISTORY_DAYS))
        for key in [key for key in tenant["days"] if key < cutoff]:
            del tenant["days"][key]
        months = sorted(tenant["months"])
        for key in months[:-MONTHLY_HISTORY]:
            del tenant["months"][key]

    def _changed(self, force: bool = False) -> None:
        self._dirty = True
        if force or time.monotonic() - self._last_flush >= self.flush_seconds:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._dirty:
            return
        try:
            atomic_write_json(self.path, {"schema_version": 1, "tenants": self._data})
            self._dirty = False
            self._last_flush = time.monotonic()
        except OSError:
            logger.exception("Could not persist tenant usage counters")

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def admit_chat(self, tenant_id: str, requests_per_day: int | None) -> tuple[bool, int]:
        """Atomically count one chat request unless today's limit is reached."""
        with self._lock:
            moment = self.clock()
            day, month = self._buckets(tenant_id, moment)
            used = day["chat_requests"]
            if requests_per_day is not None and used >= requests_per_day:
                return False, used
            day["chat_requests"] += 1
            month["chat_requests"] += 1
            self._prune(tenant_id, moment)
            self._changed()
            return True, used + 1

    def tokens_this_month(self, tenant_id: str) -> int:
        with self._lock:
            month = self._data.get(tenant_id, {}).get("months", {}).get(_month_key(self.clock()), {})
            return int(month.get("prompt_tokens", 0)) + int(month.get("completion_tokens", 0))

    def add(self, tenant_id: str, **counts: int) -> None:
        unknown = set(counts) - set(USAGE_FIELDS)
        if unknown:
            raise ValueError(f"unknown usage fields: {sorted(unknown)}")
        with self._lock:
            moment = self.clock()
            day, month = self._buckets(tenant_id, moment)
            for name, value in counts.items():
                value = max(0, int(value or 0))
                day[name] += value
                month[name] += value
            self._changed()

    def usage(self, tenant_id: str, days: int = 31) -> dict[str, Any]:
        with self._lock:
            moment = self.clock()
            tenant = self._data.get(tenant_id, {"days": {}, "months": {}})
            today = {**_empty_bucket(), **tenant["days"].get(_day_key(moment), {})}
            month = {**_empty_bucket(), **tenant["months"].get(_month_key(moment), {})}
            history = [
                {"date": key, **{**_empty_bucket(), **value}}
                for key, value in sorted(tenant["days"].items(), reverse=True)[:days]
            ]
            return {
                "day": _day_key(moment), "month": _month_key(moment),
                "today": today, "this_month": month, "daily": history,
            }

    def forget(self, tenant_id: str) -> None:
        with self._lock:
            if self._data.pop(tenant_id, None) is not None:
                self._changed(force=True)


class UploadReservations:
    """In-flight upload reservations so concurrent uploads cannot overshoot quotas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, list[int]] = {}

    def reserve(
        self, tenant_id: str, size: int, *, current_documents: int, current_bytes: int,
        max_documents: int | None, max_storage_bytes: int | None,
    ) -> tuple[str | None, int, int | None]:
        """Return ``(None, used, limit)`` and hold a slot, or ``(quota, used, limit)`` when exceeded."""
        with self._lock:
            documents, size_pending = self._pending.get(tenant_id, [0, 0])
            if max_documents is not None and current_documents + documents + 1 > max_documents:
                return "max_documents", current_documents + documents, max_documents
            if max_storage_bytes is not None and current_bytes + size_pending + size > max_storage_bytes:
                return "max_storage_bytes", current_bytes + size_pending, max_storage_bytes
            self._pending[tenant_id] = [documents + 1, size_pending + size]
            return None, current_documents + documents + 1, None

    def release(self, tenant_id: str, size: int) -> None:
        with self._lock:
            documents, size_pending = self._pending.get(tenant_id, [0, 0])
            documents, size_pending = max(0, documents - 1), max(0, size_pending - size)
            if documents or size_pending:
                self._pending[tenant_id] = [documents, size_pending]
            else:
                self._pending.pop(tenant_id, None)


class OrgUserIndex:
    """Remembers which (namespaced) user ids hold Mem0 memories per organization.

    Mem0 deletes by user id, so a tenant purge needs the list of user ids that
    ever stored a memory for that organization.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else tenant_store_root()
        self.path = self.root / "memory_users.json"
        self._lock = threading.Lock()
        payload = _read_json(self.path, {})
        self._users: dict[str, list[str]] = payload.get("organizations", {}) if isinstance(payload, dict) else {}

    def record(self, organization_id: str, user_key: str) -> None:
        with self._lock:
            users = self._users.setdefault(organization_id, [])
            if user_key in users:
                return
            users.append(user_key)
            try:
                atomic_write_json(self.path, {"schema_version": 1, "organizations": self._users})
            except OSError:
                logger.exception("Could not persist memory user index")

    def users(self, organization_id: str) -> list[str]:
        with self._lock:
            return list(self._users.get(organization_id, []))

    def forget(self, organization_id: str, keys: Iterable[str] | None = None) -> None:
        with self._lock:
            if keys is None:
                self._users.pop(organization_id, None)
            else:
                remaining = [key for key in self._users.get(organization_id, []) if key not in set(keys)]
                self._users[organization_id] = remaining
            try:
                atomic_write_json(self.path, {"schema_version": 1, "organizations": self._users})
            except OSError:
                logger.exception("Could not persist memory user index")
