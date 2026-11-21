"""How the bot authenticates to baryon-ax and says whom it acts for.

Kept identical in ``examples/slack-bot`` and ``examples/discord-bot`` so each
example stays self-contained.  See docs/auth-oidc.md, section 6 (서비스 계정 위임).

* **OIDC** (a token is configured): the bot sends its service-account token as
  ``Authorization: Bearer``.  A call made for a chat user adds
  ``X-On-Behalf-Of: slack:<id>`` (or ``discord:<id>``) and
  ``X-On-Behalf-Of-Roles``; the API caps those roles by the service account's
  roles and takes the organization from the token, so ``X-Organization-Id`` is
  not sent.  A call the bot makes as itself sends no On-Behalf-Of header.
* **dev** (no token, ``AUTH_MODE=dev`` on the API): the API trusts headers.  The
  bot sends its own identity as ``X-User-Id`` plus ``X-Organization-Id``; per-user
  calls add ``X-On-Behalf-Of`` (an alias of ``X-User-Id`` there), so the audit
  log records the bot as ``acting_service`` just like in OIDC mode.

The token comes from client credentials (``BARYON_OIDC_TOKEN_URL``,
``BARYON_OIDC_CLIENT_ID``, ``BARYON_OIDC_CLIENT_SECRET``), cached until shortly
before ``expires_in`` and fetched again once when the API answers 401.  A static
``BARYON_API_TOKEN`` is used as-is when client credentials are not configured.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

import httpx

EXPIRY_MARGIN_SECONDS = 30.0


class TokenSource:
    """Bearer token for the API: client credentials (cached) or a static token."""

    def __init__(self, http: httpx.Client, token_url: str = "", client_id: str = "", client_secret: str = "",
                 static_token: str = "", clock: Callable[[], float] = time.monotonic) -> None:
        self.http = http
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.static_token = static_token
        self.clock = clock
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, http: httpx.Client) -> "TokenSource":
        return cls(http,
                   token_url=os.getenv("BARYON_OIDC_TOKEN_URL", "").strip(),
                   client_id=os.getenv("BARYON_OIDC_CLIENT_ID", "").strip(),
                   client_secret=os.getenv("BARYON_OIDC_CLIENT_SECRET", "").strip(),
                   static_token=os.getenv("BARYON_API_TOKEN", "").strip())

    @property
    def refreshable(self) -> bool:
        return bool(self.token_url and self.client_id and self.client_secret)

    @property
    def enabled(self) -> bool:
        """A token is configured, so the API is expected to run with AUTH_MODE=oidc."""
        return self.refreshable or bool(self.static_token)

    def token(self) -> str:
        if not self.refreshable:
            return self.static_token
        with self._lock:
            if not self._token or self.clock() >= self._expires_at:
                self._fetch()
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token, self._expires_at = "", 0.0

    def _fetch(self) -> None:
        response = self.http.post(self.token_url, data={
            "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret,
        })
        response.raise_for_status()
        data = response.json()
        lifetime = float(data.get("expires_in") or 60)
        margin = min(EXPIRY_MARGIN_SECONDS, lifetime / 2)
        self._token = str(data["access_token"])
        self._expires_at = self.clock() + lifetime - margin


class ApiAuth:
    """Builds identity headers and retries once with a fresh token on 401."""

    def __init__(self, tokens: TokenSource, organization_id: str, bot_user_id: str, bot_roles: str) -> None:
        self.tokens = tokens
        self.organization_id = organization_id
        self.bot_user_id = bot_user_id     # dev mode only; in OIDC the token is the bot's identity
        self.bot_roles = bot_roles         # dev mode only; in OIDC the token's roles apply

    @property
    def oidc(self) -> bool:
        return self.tokens.enabled

    def headers(self, on_behalf_of: str | None = None, roles: str | None = None) -> dict[str, str]:
        """``on_behalf_of`` is a chat user ("slack:U123"); None means the bot acts as itself."""
        headers: dict[str, str] = {}
        if self.oidc:
            headers["Authorization"] = f"Bearer {self.tokens.token()}"
        else:
            headers["X-User-Id"] = self.bot_user_id
            headers["X-Organization-Id"] = self.organization_id
            if on_behalf_of is None:
                headers["X-Roles"] = self.bot_roles
        if on_behalf_of is not None:
            headers["X-On-Behalf-Of"] = on_behalf_of
            if roles:
                headers["X-On-Behalf-Of-Roles"] = roles
        return headers

    def request(self, http: httpx.Client, method: str, url: str, on_behalf_of: str | None = None,
                roles: str | None = None, **kwargs: Any) -> httpx.Response:
        response = http.request(method, url, headers=self.headers(on_behalf_of, roles), **kwargs)
        if response.status_code == 401 and self.tokens.refreshable:
            self.tokens.invalidate()  # expired or revoked early: fetch a new token and try once more
            response = http.request(method, url, headers=self.headers(on_behalf_of, roles), **kwargs)
        return response
