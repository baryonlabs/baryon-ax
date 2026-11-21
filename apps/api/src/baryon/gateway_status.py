"""LLM gateway connection info and live fallback status for the admin settings.

Baryon AX talks to one OpenAI-compatible endpoint (``MODEL_BASE_URL``). That
endpoint is often a gateway that falls back across several backends (for example
an on-prem GPU, a second GPU box, then hosted APIs). When the gateway publishes a
status document, the API fetches it server-side and shows the fallback order,
live health, request counts and last error per backend to administrators.

Status contract (``MODEL_GATEWAY_STATUS_URL`` returns JSON):

    {
      "enabled": true, "reachable": true, "error": "",
      "endpoints": [{"label": "local", "url": "http://127.0.0.1:8030/v1"}],
      "models": {"baryon-ax-qwen": [{"provider": "b300", "model": "..."}, ...]},
      "providers": [{"name": "b300", "up": true, "ok": 12, "fail": 0, "lastError": "",
                     "lastOkAt": "...", "keyRequired": false, "keySet": false}, ...],
      "stats": {"requests": 12, "served": {"b300": 12}, "fallbacks": 0, "failed": 0}
    }

The operations dashboard (``/api/gateway``) and the AWS Bedrock gateway
(``/v1/gateway/status``) both publish this shape.

Secrets: only whitelisted fields leave this module. API keys are reported as
"configured or not" and never as values; error strings are scrubbed of anything
that looks like a key, and of the configured ``MODEL_API_KEY`` itself.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

_CACHE_SECONDS = 10.0
_cache: dict[str, Any] = {"at": 0.0, "url": "", "value": None}

_KEYLIKE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{6,}|sk_[A-Za-z0-9_\-]{6,}|Bearer\s+\S+|api[_-]?key[=:]\s*\S+|"
    r"AKIA[0-9A-Z]{12,}|[A-Za-z0-9_\-]{32,})",
    re.IGNORECASE,
)


def _scrub(text: Any, limit: int = 200) -> str:
    value = str(text or "")
    secret = os.getenv("MODEL_API_KEY", "")
    if len(secret) >= 4:
        value = value.replace(secret, "[redacted]")
    return _KEYLIKE.sub("[redacted]", value)[:limit]


def _safe_url(url: Any, keep_fragment: bool = False) -> str:
    """Drop credentials and query (and the fragment unless asked) from a URL; http(s) only."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, host, parts.path, "", parts.fragment if keep_fragment else ""))


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def sanitize(raw: Any) -> dict:
    """Keep only the documented fields, with types forced and strings scrubbed."""
    if not isinstance(raw, dict):
        return {"reachable": False, "error": "invalid status document", "endpoints": [], "models": {}, "providers": [], "stats": {}}
    endpoints = [
        {"label": _scrub(item.get("label"), 40), "url": _safe_url(item.get("url"))}
        for item in raw.get("endpoints") or [] if isinstance(item, dict) and _safe_url(item.get("url"))
    ][:10]
    models: dict[str, list[dict]] = {}
    for alias, chain in list((raw.get("models") or {}).items())[:10] if isinstance(raw.get("models"), dict) else []:
        if isinstance(chain, list):
            models[_scrub(alias, 80)] = [
                {"provider": _scrub(step.get("provider"), 40), "model": _scrub(step.get("model"), 120)}
                for step in chain if isinstance(step, dict)
            ][:10]
    providers = [
        {
            "name": _scrub(item.get("name"), 40),
            "up": bool(item.get("up")),
            "ok": _int(item.get("ok")),
            "fail": _int(item.get("fail")),
            "last_error": _scrub(item.get("lastError")),
            "last_ok_at": _scrub(item.get("lastOkAt"), 40) or None,
            "key_required": bool(item.get("keyRequired")),
            "key_set": bool(item.get("keySet")),
        }
        for item in raw.get("providers") or [] if isinstance(item, dict)
    ][:10]
    stats_raw = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    served = stats_raw.get("served") if isinstance(stats_raw.get("served"), dict) else {}
    stats = {
        "requests": _int(stats_raw.get("requests")),
        "fallbacks": _int(stats_raw.get("fallbacks")),
        "failed": _int(stats_raw.get("failed")),
        "served": {_scrub(name, 40): _int(count) for name, count in list(served.items())[:10]},
    }
    return {
        "reachable": bool(raw.get("reachable", True)),
        "error": _scrub(raw.get("error")),
        "endpoints": endpoints,
        "models": models,
        "providers": providers,
        "stats": stats,
    }


async def fetch_status() -> dict | None:
    """Fetch and sanitize the gateway status, cached for a few seconds. None when not configured."""
    url = os.getenv("MODEL_GATEWAY_STATUS_URL", "").strip()
    if not url:
        return None
    now = time.monotonic()
    if _cache["url"] == url and _cache["value"] is not None and now - _cache["at"] < _CACHE_SECONDS:
        return _cache["value"]
    headers = {"Accept": "application/json"}
    # The model key is sent only when explicitly requested (e.g. the AWS gateway's
    # protected status endpoint), never to an arbitrary dashboard by default.
    if os.getenv("MODEL_GATEWAY_STATUS_AUTH", "none").strip().lower() == "bearer" and os.getenv("MODEL_API_KEY"):
        headers["Authorization"] = f"Bearer {os.getenv('MODEL_API_KEY')}"
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
        if response.status_code != 200:
            value = sanitize({"reachable": False, "error": f"status endpoint returned HTTP {response.status_code}"})
        else:
            value = sanitize(response.json())
    except (httpx.HTTPError, ValueError) as error:
        value = sanitize({"reachable": False, "error": f"status endpoint unreachable: {type(error).__name__}"})
    _cache.update(at=now, url=url, value=value)
    return value


def connection_info(model_name: str, base_url: str) -> dict:
    """What a client needs to call the configured model endpoint; never includes key values."""
    return {
        "endpoint": _safe_url(base_url) or None,
        "model": model_name,
        "api_key_configured": bool(os.getenv("MODEL_API_KEY", "").strip()),
        "status_configured": bool(os.getenv("MODEL_GATEWAY_STATUS_URL", "").strip()),
        "dashboard_url": _safe_url(os.getenv("MODEL_SERVER_URL", ""), keep_fragment=True) or None,
    }
