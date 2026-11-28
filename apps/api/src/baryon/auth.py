"""OIDC bearer-token authentication for the Baryon AX API.

The default mode is ``dev``: the legacy ``X-User-Id`` / ``X-Organization-Id`` /
``X-Roles`` headers are trusted so local demos and tests keep working.

``AUTH_MODE=oidc`` turns on Keycloak-compatible token validation. A pure ASGI
middleware validates the bearer token, maps IdP groups to Baryon roles, and
injects trusted identity headers before the request reaches an endpoint. The
middleware is deliberately provider-agnostic: Keycloak, Entra ID, or any
standards-compliant OIDC issuer works as long as the role claim is configured.

Delegated identity: a bot or MCP client authenticates with one service-account
token that carries ``OIDC_SERVICE_ROLE`` and names the end user with
``X-On-Behalf-Of``. The middleware then acts as that user, caps the requested
roles by the service account's own roles, and records the service principal
as the acting service so audit events show both.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from contextvars import ContextVar
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
import jwt
from jwt import InvalidTokenError
from jwt.algorithms import RSAAlgorithm
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

PLATFORM_ADMIN = "platform-admin"
DEFAULT_PUBLIC_PATHS = "/health,/docs*,/redoc*,/openapi.json,/v1/open-source,/v1/signup"
IDENTITY_HEADERS = (b"x-user-id", b"x-organization-id", b"x-roles")
DELEGATION_HEADERS = (b"x-on-behalf-of", b"x-on-behalf-of-roles", b"x-acting-service")
# Allowed characters for delegated user ids such as ``slack:U123`` or ``discord:42``.
DELEGATED_USER_PATTERN = re.compile(r"[A-Za-z0-9:._@-]{1,128}")

# Set per request by the middleware; read by the audit emitter so events show
# which service account acted on behalf of the user.
ACTING_SERVICE: ContextVar[str | None] = ContextVar("baryon_acting_service", default=None)

AuthFailureHook = Callable[[dict[str, Any]], None]

logger = logging.getLogger(__name__)


def current_acting_service() -> str | None:
    return ACTING_SERVICE.get()


class AuthError(Exception):
    """Raised when a token is missing or cannot be validated."""


class DelegationError(Exception):
    """Raised when delegation headers are not allowed or malformed."""

    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


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
    # Operator of a pooled deployment. Distinct from a tenant ``admin``.
    platform_admin_role: str = "baryon-platform-admin"
    org_claim: str = "organization"
    default_org: str = "local-org"
    user_claim: str = "preferred_username"
    service_role: str = "baryon-service"
    delegation_prefixes: tuple[str, ...] = ()
    failure_audit_per_minute: int = 60
    public_paths: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_PUBLIC_PATHS.split(",")))
    leeway_seconds: int = 30
    http_timeout: float = 5.0
    jwks_cache_seconds: int = 300
    # Pooled multi-tenancy: a token without an organization claim must not fall
    # back to ``default_org`` (that would merge unrelated users into one tenant).
    require_org_claim: bool = False

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
            platform_admin_role=os.getenv("OIDC_PLATFORM_ADMIN_ROLE", "baryon-platform-admin").strip(),
            org_claim=os.getenv("OIDC_ORG_CLAIM", "organization").strip(),
            default_org=os.getenv("OIDC_DEFAULT_ORG", "local-org").strip(),
            user_claim=os.getenv("OIDC_USER_CLAIM", "preferred_username").strip(),
            service_role=os.getenv("OIDC_SERVICE_ROLE", "baryon-service").strip(),
            delegation_prefixes=tuple(
                value.strip()
                for value in os.getenv("OIDC_DELEGATION_USER_PREFIXES", "").split(",")
                if value.strip()
            ),
            failure_audit_per_minute=int(os.getenv("AUTH_FAILED_AUDIT_PER_MINUTE", "60")),
            public_paths=public_paths,
            leeway_seconds=int(os.getenv("OIDC_LEEWAY_SECONDS", "30")),
            http_timeout=float(os.getenv("OIDC_HTTP_TIMEOUT_SECONDS", "5")),
            jwks_cache_seconds=int(os.getenv("OIDC_JWKS_CACHE_SECONDS", "300")),
            require_org_claim=os.getenv("TENANCY_MODE", "single").strip().lower() == "pooled",
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
    # Only the configured IdP role grants platform administration (no bare
    # "platform-admin" alias), so tenant-managed group names cannot collide.
    if config.platform_admin_role:
        candidates[config.platform_admin_role] = PLATFORM_ADMIN
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


def has_service_role(claims: dict[str, Any], config: OIDCConfig) -> bool:
    """True when the token carries the configured delegation (service) role."""
    if not config.service_role:
        return False
    for value in _claim_values(_claim_value(claims, config.role_claim)):
        role = value.strip().lstrip("/")
        if role == config.service_role or role.rsplit("/", 1)[-1] == config.service_role:
            return True
    return False


def validate_delegated_user(user_id: str, config: OIDCConfig) -> str:
    user_id = user_id.strip()
    if not DELEGATED_USER_PATTERN.fullmatch(user_id):
        raise DelegationError("malformed X-On-Behalf-Of user id", status_code=400)
    if config.delegation_prefixes and not user_id.startswith(config.delegation_prefixes):
        raise DelegationError("X-On-Behalf-Of user id prefix is not allowed")
    return user_id


def delegated_roles(requested: str | None, service_roles: set[str], config: OIDCConfig) -> set[str]:
    """Requested roles capped by the service account's own roles (never escalates).

    No requested roles means ``member``. Roles the service account does not hold
    are dropped; if nothing is left the delegation is rejected.
    """
    wanted: set[str] = set()
    for value in (requested or "").split(","):
        normalized = _normalize_role(value, config)
        if normalized:
            wanted.add(normalized)
    if not wanted:
        wanted = {"member"}
    # Platform administration is never delegated: a bot acting for a chat user
    # must not be able to manage tenants.
    allowed = (wanted & service_roles) - {PLATFORM_ADMIN}
    if not allowed:
        raise DelegationError("requested roles exceed the service account's roles")
    return allowed


def organizations_from_claims(claims: dict[str, Any], config: OIDCConfig) -> list[str]:
    """Organization ids in the token, in claim order.

    Accepts a string, a list (Keycloak Organizations: ``["acme"]``) or a map
    keyed by organization alias (Keycloak with "add organization attributes":
    ``{"acme": {...}}``).
    """
    value = _claim_value(claims, config.org_claim)
    if isinstance(value, dict):
        value = list(value.keys())
    return [item.strip() for item in _claim_values(value) if item and item.strip()]


def principal_from_claims(
    claims: dict[str, Any], config: OIDCConfig, requested_org: str | None = None,
) -> PrincipalClaims:
    user_id = (
        _claim_value(claims, config.user_claim)
        or claims.get("email")
        or claims.get("sub")
        or "unknown"
    )
    organizations = organizations_from_claims(claims, config)
    if requested_org and requested_org in organizations:
        # A member of several organizations picks one with X-Organization-Id;
        # only organizations present in the signed token can be selected.
        organization = requested_org
    else:
        organization = organizations[0] if organizations else config.default_org
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
        on_failure: AuthFailureHook | None = None,
    ) -> None:
        self.app = app
        self.config = config
        self.validator = validator or OIDCValidator(config)
        self.on_failure = on_failure
        # Fixed-window limiter for auth.failed audit events: O(1) memory.
        self._failure_window = 0
        self._failure_count = 0
        self._failure_suppressed = 0

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.config.mode != "oidc":
            await self._dev(scope, receive, send)
            return

        path = scope.get("path", "")
        if scope.get("method") == "OPTIONS" or self._is_public(path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            await self._reject(scope, receive, send, "missing bearer token")
            return

        try:
            claims = self.validator.validate(token)
        except AuthError as error:
            await self._reject(scope, receive, send, str(error))
            return

        if self.config.require_org_claim and not organizations_from_claims(claims, self.config):
            await self._reject(
                scope, receive, send, "token has no organization claim", status_code=403,
                code="organization_claim_missing",
            )
            return
        principal = principal_from_claims(claims, self.config, headers.get("x-organization-id"))
        acting_service: str | None = None
        on_behalf_of = headers.get("x-on-behalf-of")
        if on_behalf_of is not None:
            try:
                if not has_service_role(claims, self.config):
                    raise DelegationError("token is not allowed to delegate (missing service role)")
                user_id = validate_delegated_user(on_behalf_of, self.config)
                roles = delegated_roles(headers.get("x-on-behalf-of-roles"), principal.roles, self.config)
            except DelegationError as error:
                await self._reject(
                    scope, receive, send, str(error), status_code=error.status_code, actor=principal,
                )
                return
            acting_service = principal.user_id
            principal = PrincipalClaims(user_id=user_id, organization_id=principal.organization_id, roles=roles)
        elif headers.get("x-on-behalf-of-roles") is not None:
            await self._reject(
                scope, receive, send, "X-On-Behalf-Of-Roles requires X-On-Behalf-Of",
                status_code=400, actor=principal,
            )
            return

        self._set_identity(
            scope, principal.user_id, principal.organization_id, ",".join(sorted(principal.roles)), acting_service,
        )
        state = scope.setdefault("state", {})
        state["principal"] = principal
        if acting_service:
            state["acting_service"] = acting_service
        await self._call_as(acting_service, scope, receive, send)

    async def _dev(self, scope, receive, send) -> None:
        """Dev mode trusts headers; ``X-On-Behalf-Of`` is an alias of ``X-User-Id``.

        When a client sends both, the original ``X-User-Id`` (or an explicit
        ``X-Acting-Service``) is recorded as the acting service, so local bot
        runs produce the same audit shape as OIDC delegation. Roles are not
        capped here because dev mode trusts every header anyway.
        """
        headers = Headers(scope=scope)
        on_behalf_of = headers.get("x-on-behalf-of")
        if on_behalf_of is None:
            await self._call_as(headers.get("x-acting-service"), scope, receive, send)
            return
        try:
            user_id = validate_delegated_user(on_behalf_of, self.config)
        except DelegationError as error:
            await self._error(scope, receive, send, str(error), error.status_code)
            return
        acting_service = headers.get("x-acting-service") or headers.get("x-user-id")
        roles = headers.get("x-on-behalf-of-roles") or headers.get("x-roles") or "member"
        organization = headers.get("x-organization-id") or self.config.default_org
        self._set_identity(scope, user_id, organization, roles, acting_service)
        await self._call_as(acting_service, scope, receive, send)

    async def _call_as(self, acting_service: str | None, scope, receive, send) -> None:
        token = ACTING_SERVICE.set(acting_service or None)
        try:
            await self.app(scope, receive, send)
        finally:
            ACTING_SERVICE.reset(token)

    @staticmethod
    def _set_identity(scope, user_id: str, organization_id: str, roles: str, acting_service: str | None) -> None:
        stripped = IDENTITY_HEADERS + DELEGATION_HEADERS
        filtered = [(name, value) for name, value in scope.get("headers", []) if name.lower() not in stripped]
        filtered.extend(
            [
                (b"x-user-id", user_id.encode("utf-8")),
                (b"x-organization-id", organization_id.encode("utf-8")),
                (b"x-roles", roles.encode("utf-8")),
            ]
        )
        if acting_service:
            filtered.append((b"x-acting-service", acting_service.encode("utf-8")))
        scope["headers"] = filtered

    def _is_public(self, path: str) -> bool:
        for pattern in self.config.public_paths:
            if pattern.endswith("*") and path.startswith(pattern[:-1]):
                return True
            if path == pattern:
                return True
        return False

    async def _reject(
        self, scope, receive, send, message: str, status_code: int = 401, actor: PrincipalClaims | None = None,
        code: str | None = None,
    ) -> None:
        self._record_failure(scope, message, status_code, actor)
        await self._error(scope, receive, send, message, status_code, code=code)

    @staticmethod
    async def _error(scope, receive, send, message: str, status_code: int, code: str | None = None) -> None:
        if code:
            response = JSONResponse({"detail": {"code": code, "message": message}}, status_code=status_code)
        elif status_code == 401:
            response = JSONResponse(
                {"detail": f"Invalid or missing credentials: {message}"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        else:
            response = JSONResponse({"detail": f"Delegation rejected: {message}"}, status_code=status_code)
        await response(scope, receive, send)

    def _record_failure(self, scope, reason: str, status_code: int, actor: PrincipalClaims | None) -> None:
        """Emit a rate-limited ``auth.failed`` event. Never includes the token."""
        if self.on_failure is None:
            return
        window = int(time.monotonic() // 60)
        if window != self._failure_window:
            self._failure_window = window
            self._failure_count = 0
        if self._failure_count >= max(self.config.failure_audit_per_minute, 0):
            self._failure_suppressed += 1
            return
        self._failure_count += 1
        event: dict[str, Any] = {
            "reason": reason[:200],
            "status": status_code,
            "path": str(scope.get("path", ""))[:200],
            "method": scope.get("method", ""),
        }
        if actor:
            event["actor"] = actor.user_id[:128]
            event["organization_id"] = actor.organization_id
        if self._failure_suppressed:
            event["suppressed"] = self._failure_suppressed
            self._failure_suppressed = 0
        try:
            self.on_failure(event)
        except Exception:
            # Audit persistence must never break the auth path, but do not hide it.
            logger.exception("Could not record auth.failed audit event")
