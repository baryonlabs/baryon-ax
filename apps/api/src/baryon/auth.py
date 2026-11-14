"""OIDC bearer-token authentication for the Baryon AX API.

The default mode is ``dev``: the legacy ``X-User-Id`` / ``X-Organization-Id`` /
``X-Roles`` headers are trusted so local demos and tests keep working.

``AUTH_MODE=oidc`` turns on Keycloak-compatible token validation. A pure ASGI
middleware validates the bearer token, maps IdP groups to Baryon roles, and
injects trusted identity headers before the request reaches an endpoint. The
middleware is deliberately provider-agnostic: Keycloak, Entra ID, or any
standards-compliant OIDC issuer works as long as the role claim is configured.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt import InvalidTokenError
from jwt.algorithms import RSAAlgorithm
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

DEFAULT_PUBLIC_PATHS = "/health,/docs*,/redoc*,/openapi.json,/v1/open-source"
IDENTITY_HEADERS = (b"x-user-id", b"x-organization-id", b"x-roles")


class AuthError(Exception):
    """Raised when a token is missing or cannot be validated."""


@dataclass(frozen=True)
class OIDCConfig:
    mode: str = "dev"
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    role_claim: str = "groups"
    admin_role: str = "baryon-admin"
    member_role: str = "baryon-member"
    legal_role: str = "baryon-legal"
    org_claim: str = "organization"
    default_org: str = "local-org"
    user_claim: str = "preferred_username"
    public_paths: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_PUBLIC_PATHS.split(",")))
    leeway_seconds: int = 30
    http_timeout: float = 5.0
    jwks_cache_seconds: int = 300

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        mode = os.getenv("AUTH_MODE", "dev").strip().lower()
        if mode not in {"dev", "oidc"}:
            raise ValueError("AUTH_MODE must be 'dev' or 'oidc'")
        public_paths = tuple(
            value.strip()
            for value in os.getenv("OIDC_PUBLIC_PATHS", DEFAULT_PUBLIC_PATHS).split(",")
            if value.strip()
        )
        return cls(
            mode=mode,
            issuer=os.getenv("OIDC_ISSUER", "").strip().rstrip("/"),
            audience=os.getenv("OIDC_AUDIENCE", "").strip(),
            jwks_url=os.getenv("OIDC_JWKS_URL", "").strip(),
            role_claim=os.getenv("OIDC_ROLE_CLAIM", "groups").strip() or "groups",
            admin_role=os.getenv("OIDC_ADMIN_ROLE", "baryon-admin").strip(),
            member_role=os.getenv("OIDC_MEMBER_ROLE", "baryon-member").strip(),
            legal_role=os.getenv("OIDC_LEGAL_ROLE", "baryon-legal").strip(),
            org_claim=os.getenv("OIDC_ORG_CLAIM", "organization").strip(),
            default_org=os.getenv("OIDC_DEFAULT_ORG", "local-org").strip(),
            user_claim=os.getenv("OIDC_USER_CLAIM", "preferred_username").strip(),
            public_paths=public_paths,
            leeway_seconds=int(os.getenv("OIDC_LEEWAY_SECONDS", "30")),
            http_timeout=float(os.getenv("OIDC_HTTP_TIMEOUT_SECONDS", "5")),
            jwks_cache_seconds=int(os.getenv("OIDC_JWKS_CACHE_SECONDS", "300")),
        )

    def validate(self) -> None:
        """Fail fast on obviously incomplete production configuration."""
        if self.mode != "oidc":
            return
        if not self.issuer:
            raise ValueError("OIDC_ISSUER is required when AUTH_MODE=oidc")
        parsed = urlparse(self.issuer)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OIDC_ISSUER must be an absolute URL")
        allow_insecure = os.getenv("OIDC_ALLOW_INSECURE_ISSUER", "false").strip().lower() == "true"
        if parsed.scheme != "https" and not allow_insecure:
            raise ValueError(
                "OIDC_ISSUER must use https; set OIDC_ALLOW_INSECURE_ISSUER=true only for local development"
            )


@dataclass(frozen=True)
class PrincipalClaims:
    user_id: str
    organization_id: str
    roles: set[str]


def _claim_value(claims: dict[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _claim_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _normalize_role(value: str, config: OIDCConfig) -> str | None:
    role = value.strip().lstrip("/")
    if not role:
        return None
    candidates = {
        config.admin_role: "admin",
        config.member_role: "member",
        config.legal_role: "legal",
        "admin": "admin",
        "member": "member",
        "legal": "legal",
    }
    if role in candidates:
        return candidates[role]
    # Keycloak configured with full.path=true sends /baryon-admin.
    tail = role.rsplit("/", 1)[-1]
    return candidates.get(tail)


def roles_from_claims(claims: dict[str, Any], config: OIDCConfig) -> set[str]:
    roles: set[str] = set()
    for value in _claim_values(_claim_value(claims, config.role_claim)):
        normalized = _normalize_role(value, config)
        if normalized:
            roles.add(normalized)
    if not roles:
        roles.add("member")
    return roles


def principal_from_claims(claims: dict[str, Any], config: OIDCConfig) -> PrincipalClaims:
    user_id = (
        _claim_value(claims, config.user_claim)
        or claims.get("email")
        or claims.get("sub")
        or "unknown"
    )
    organization = _claim_value(claims, config.org_claim) or config.default_org
    if isinstance(organization, (list, tuple)):
        organization = organization[0] if organization else config.default_org
    return PrincipalClaims(
        user_id=str(user_id),
        organization_id=str(organization),
        roles=roles_from_claims(claims, config),
    )


class OIDCValidator:
    """Validates RS256 bearer tokens against an issuer's JWKS."""

    def __init__(
        self,
        config: OIDCConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._http_client = http_client
        self._lock = threading.Lock()
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at = 0.0

    def validate(self, token: str) -> dict[str, Any]:
        if not token:
            raise AuthError("missing bearer token")
        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as error:
            raise AuthError(f"invalid token header: {error}") from error
        if header.get("alg") != "RS256":
            raise AuthError("only RS256 tokens are accepted")
        key = self._signing_key(header.get("kid"))
        options = {"verify_aud": bool(self.config.audience), "leeway": self.config.leeway_seconds}
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.config.audience or None,
                issuer=self.config.issuer,
                options=options,
            )
        except InvalidTokenError as error:
            raise AuthError(str(error)) from error

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self.config.http_timeout)
        return self._http_client

    def _get_discovery(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        url = self.config.issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            response = self._client().get(url)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise AuthError(f"OIDC discovery failed: {error}") from error
        if document.get("issuer") and document["issuer"].rstrip("/") != self.config.issuer:
            raise AuthError("OIDC discovery issuer does not match OIDC_ISSUER")
        self._discovery = document
        return document

    def _get_jwks(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._jwks is not None and now - self._jwks_fetched_at < self.config.jwks_cache_seconds:
            return self._jwks
        with self._lock:
            if self._jwks is not None and now - self._jwks_fetched_at < self.config.jwks_cache_seconds:
                return self._jwks
            url = self.config.jwks_url or self._get_discovery().get("jwks_uri", "")
            if not url:
                raise AuthError("OIDC issuer did not publish a jwks_uri")
            try:
                response = self._client().get(url)
                response.raise_for_status()
                document = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise AuthError(f"JWKS fetch failed: {error}") from error
            if not document.get("keys"):
                raise AuthError("JWKS document has no keys")
            self._jwks = document
            self._jwks_fetched_at = now
            return document

    def _signing_key(self, kid: str | None):
        jwks = self._get_jwks()
        keys = jwks.get("keys", [])
        candidates = [key for key in keys if not kid or key.get("kid") == kid]
        if not candidates and kid:
            # Key rotation: refresh once before giving up.
            self._jwks = None
            jwks = self._get_jwks()
            candidates = [key for key in jwks.get("keys", []) if key.get("kid") == kid]
        for key in candidates:
            try:
                return RSAAlgorithm.from_jwk(key)
            except (ValueError, KeyError):
                continue
        raise AuthError("no matching signing key for token")


class OIDCAuthMiddleware:
    """Pure ASGI middleware so streaming responses and SSE are untouched."""

    def __init__(
        self,
        app,
        config: OIDCConfig,
        validator: OIDCValidator | None = None,
    ) -> None:
        self.app = app
        self.config = config
        self.validator = validator or OIDCValidator(config)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self.config.mode != "oidc":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if scope.get("method") == "OPTIONS" or self._is_public(path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            await self._unauthorized(scope, receive, send, "missing bearer token")
            return

        try:
            claims = self.validator.validate(token)
        except AuthError as error:
            await self._unauthorized(scope, receive, send, str(error))
            return

        principal = principal_from_claims(claims, self.config)
        filtered = [(name, value) for name, value in scope.get("headers", []) if name.lower() not in IDENTITY_HEADERS]
        filtered.extend(
            [
                (b"x-user-id", principal.user_id.encode("utf-8")),
                (b"x-organization-id", principal.organization_id.encode("utf-8")),
                (b"x-roles", ",".join(sorted(principal.roles)).encode("utf-8")),
            ]
        )
        scope["headers"] = filtered
        state = scope.setdefault("state", {})
        state["principal"] = principal
        await self.app(scope, receive, send)

    def _is_public(self, path: str) -> bool:
        for pattern in self.config.public_paths:
            if pattern.endswith("*") and path.startswith(pattern[:-1]):
                return True
            if path == pattern:
                return True
        return False

    async def _unauthorized(self, scope, receive, send, message: str) -> None:
        response = JSONResponse(
            {"detail": f"Invalid or missing credentials: {message}"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
        await response(scope, receive, send)
