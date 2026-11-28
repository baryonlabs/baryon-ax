"""Invite codes for self-service signup, and the limiter in front of them.

An invite is a one-time bearer secret. The store keeps only a SHA-256 hash of
the normalized code, so a leaked ``invites.json`` cannot be replayed. The raw
code is returned exactly once, by the platform-admin endpoint that issues it.

Redemption is a claim/complete handshake:

1. ``claim`` atomically claims an unused, unexpired, unrevoked code for one
   e-mail address. Concurrent requests cannot both claim it.
2. The caller creates the tenant and prepares the IdP.
3. ``complete`` records when and by whom it was used.

If provisioning fails, ``release`` returns the code to the pool, so the person
can try again. A second request with the same e-mail after ``complete`` is
idempotent: the endpoint re-sends the sign-in mail instead of creating a second
tenant. A different e-mail on a used code is rejected with the same generic
message as a wrong code, so the response never says whether a code or an
account exists.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .tenants import TenantError, atomic_write_json, tenant_store_root, validate_tenant_id

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Base32 without I/O/0/1, so a code read out loud or copied by hand is unambiguous.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUPS = 4
CODE_GROUP_SIZE = 4
INVITE_STATUSES = ("pending", "used", "expired", "revoked")
DEFAULT_EXPIRES_DAYS = 7
MAX_EXPIRES_DAYS = 365


class InviteError(ValueError):
    """Invalid invite input (HTTP 422)."""


class InviteInvalid(Exception):
    """Wrong, expired, revoked or already-used-by-someone-else code."""


class InviteAlreadyUsed(InviteError):
    """Revoking a redeemed code is refused (HTTP 409)."""


class InviteNotFound(InviteError):
    """No invite with this id (HTTP 404)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if len(value) > 254 or not EMAIL_PATTERN.fullmatch(value):
        raise InviteError("a valid email address is required")
    return value


def normalize_code(code: str) -> str:
    """Case-insensitive and forgiving about spaces and dashes when pasted."""
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper()


def code_digest(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()


def generate_code() -> str:
    groups = [
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP_SIZE))
        for _ in range(CODE_GROUPS)
    ]
    return "AX-" + "-".join(groups)


@dataclass
class Invite:
    id: str
    organization_id: str
    email: str
    code_hash: str
    created_at: str
    expires_at: str
    created_by: str = ""
    used_at: str | None = None
    used_by: str | None = None
    revoked_at: str | None = None
    claimed_at: str | None = None
    claim_token: str | None = None
    notes: str = ""

    def status(self, now: datetime | None = None) -> str:
        if self.revoked_at:
            return "revoked"
        if self.used_at:
            return "used"
        moment = now or utcnow()
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return "expired"
        return "expired" if expires <= moment else "pending"

    def as_dict(self, now: datetime | None = None) -> dict:
        """Public shape: never includes the code or its hash."""
        payload = asdict(self)
        payload.pop("code_hash", None)
        payload.pop("claim_token", None)
        payload["status"] = self.status(now)
        payload["expires_days"] = max(0, _days_between(self.created_at, self.expires_at))
        return payload


def _days_between(start: str, end: str) -> int:
    try:
        return max(0, round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 86400))
    except ValueError:
        return DEFAULT_EXPIRES_DAYS


class InviteStore:
    """JSON-backed invite registry. Reads are lock-free; writes are atomic."""

    def __init__(self, root: str | Path | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self.root = Path(root) if root else tenant_store_root()
        self.path = self.root / "invites.json"
        self.clock = clock or utcnow
        self.claim_timeout = float(os.getenv("INVITE_CLAIM_TIMEOUT_SECONDS", "900"))
        self._lock = threading.Lock()
        self._invites: dict[str, Invite] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            import json

            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in payload.get("invites", []) if isinstance(payload, dict) else []:
            try:
                invite = Invite(**item)
                validate_tenant_id(invite.organization_id)
                normalize_email(invite.email)
            except (TypeError, ValueError, TenantError):
                continue
            self._invites[invite.id] = invite

    def _save(self) -> None:
        atomic_write_json(
            self.path,
            {"schema_version": 1, "invites": [asdict(invite) for invite in self._invites.values()]},
        )

    def create(
        self,
        organization_id: str,
        email: str,
        *,
        expires_days: int = DEFAULT_EXPIRES_DAYS,
        created_by: str = "",
        notes: str = "",
    ) -> tuple[Invite, str]:
        validate_tenant_id(organization_id)
        normalized_email = normalize_email(email)
        if isinstance(expires_days, bool) or not isinstance(expires_days, int) or not 1 <= expires_days <= MAX_EXPIRES_DAYS:
            raise InviteError(f"expires_days must be between 1 and {MAX_EXPIRES_DAYS}")
        code = generate_code()
        created = self.clock()
        invite = Invite(
            id=uuid.uuid4().hex,
            organization_id=organization_id,
            email=normalized_email,
            code_hash=code_digest(code),
            created_at=iso(created),
            expires_at=iso(created + timedelta(days=expires_days)),
            created_by=(created_by or "")[:128],
            notes=(notes or "")[:2000],
        )
        with self._lock:
            self._invites[invite.id] = invite
            self._save()
        return invite, code

    def get(self, invite_id: str) -> Invite | None:
        return self._invites.get(invite_id)

    def list(self, status: str | None = None) -> list[Invite]:
        now = self.clock()
        items = [
            invite for invite in self._invites.values()
            if status is None or invite.status(now) == status
        ]
        return sorted(items, key=lambda invite: (invite.created_at, invite.id), reverse=True)

    def revoke(self, invite_id: str) -> Invite:
        now = iso(self.clock())
        with self._lock:
            invite = self._invites.get(invite_id)
            if invite is None:
                raise InviteNotFound("invite not found")
            if invite.used_at:
                raise InviteAlreadyUsed("a redeemed invite cannot be revoked")
            if not invite.revoked_at:
                invite.revoked_at = now
                self._save()
            return invite

    # ------------------------------------------------------------------ redemption

    def _find_locked(self, code: str) -> Invite | None:
        digest = code_digest(code)
        for invite in self._invites.values():
            # Constant-time comparison for every candidate: no early exit on a match.
            if hmac.compare_digest(invite.code_hash, digest):
                return invite
        return None

    def _claim_locked(self, invite: Invite) -> str | None:
        """Return a claim token, or None when a fresh claim is still in flight."""
        moment = self.clock()
        if invite.claimed_at and invite.claim_token:
            try:
                claimed = datetime.fromisoformat(invite.claimed_at)
            except ValueError:
                claimed = moment - timedelta(seconds=self.claim_timeout + 1)
            if (moment - claimed).total_seconds() < self.claim_timeout:
                return None
        invite.claimed_at = iso(moment)
        invite.claim_token = secrets.token_urlsafe(16)
        self._save()
        return invite.claim_token

    def claim(self, code: str, email: str) -> tuple[str, Invite | None, str | None]:
        """Return ``(state, invite, token)``.

        ``state`` is ``invalid``, ``already`` (same e-mail redeemed it),
        ``busy`` (another request holds it) or ``claimed``.
        """
        normalized_email = normalize_email(email)
        with self._lock:
            invite = self._find_locked(code)
            if invite is None or invite.revoked_at:
                raise InviteInvalid()
            moment = self.clock()
            if invite.status(moment) == "expired":
                raise InviteInvalid()
            if invite.used_at:
                if invite.used_by == normalized_email:
                    return "already", invite, None
                raise InviteInvalid()
            if invite.email != normalized_email:
                raise InviteInvalid()
            token = self._claim_locked(invite)
            if token is None:
                return "busy", invite, None
            return "claimed", invite, token

    def complete(self, invite_id: str, token: str, email: str) -> Invite:
        normalized_email = normalize_email(email)
        with self._lock:
            invite = self._invites.get(invite_id)
            if invite is None or invite.claim_token != token:
                raise InviteInvalid()
            invite.used_at = iso(self.clock())
            invite.used_by = normalized_email
            invite.claim_token = None
            invite.claimed_at = None
            self._save()
            return invite

    def release(self, invite_id: str, token: str) -> None:
        with self._lock:
            invite = self._invites.get(invite_id)
            if invite is not None and invite.claim_token == token:
                invite.claim_token = None
                invite.claimed_at = None
                self._save()


class SignupRateLimited(Exception):
    """Too many signup attempts for this IP or code (HTTP 429)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("too many signup attempts")
        self.retry_after = max(1, int(retry_after))


class SignupRateLimiter:
    """In-process fixed-window limiter for ``POST /v1/signup``.

    Two buckets are checked together: the caller's IP and the code (hashed, so
    the raw code never sits in memory as a key). Both must be under their limit.
    A process restart resets the window; that is acceptable for a signup form
    and needs no Redis dependency. Put a real edge rate limit in front of the
    web tier for hostile traffic.
    """

    def __init__(
        self,
        ip_limit: int | None = None,
        code_limit: int | None = None,
        window_seconds: float = 3600,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ip_limit = int(os.getenv("SIGNUP_RATE_LIMIT_PER_HOUR", "20") if ip_limit is None else ip_limit)
        self.code_limit = int(os.getenv("SIGNUP_CODE_RATE_LIMIT_PER_HOUR", "10") if code_limit is None else code_limit)
        self.window_seconds = float(window_seconds)
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], list[float]] = {}

    def _recent(self, key: tuple[str, str], now: float) -> list[float]:
        cutoff = now - self.window_seconds
        stamps = [stamp for stamp in self._buckets.get(key, []) if stamp > cutoff]
        if stamps:
            self._buckets[key] = stamps
        else:
            self._buckets.pop(key, None)
        return stamps

    def admit(self, ip: str, code: str) -> None:
        if self.ip_limit <= 0 and self.code_limit <= 0:
            return
        now = self.clock()
        ip_key = ("ip", (ip or "unknown")[:64])
        code_key = ("code", code_digest(code))
        with self._lock:
            ip_stamps = self._recent(ip_key, now)
            code_stamps = self._recent(code_key, now)
            retry = 0
            if self.ip_limit > 0 and len(ip_stamps) >= self.ip_limit:
                retry = max(retry, self.window_seconds - (now - ip_stamps[0]))
            if self.code_limit > 0 and len(code_stamps) >= self.code_limit:
                retry = max(retry, self.window_seconds - (now - code_stamps[0]))
            if retry > 0:
                raise SignupRateLimited(int(retry) + 1)
            if self.ip_limit > 0:
                ip_stamps.append(now)
                self._buckets[ip_key] = ip_stamps
            if self.code_limit > 0:
                code_stamps.append(now)
                self._buckets[code_key] = code_stamps
