from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from baryon.auth import (
    AuthError,
    OIDCAuthMiddleware,
    OIDCConfig,
    OIDCValidator,
    current_acting_service,
    principal_from_claims,
    roles_from_claims,
)


def config(**overrides) -> OIDCConfig:
    values = dict(mode="oidc", issuer="https://id.example", audience="baryon-ax-api")
    values.update(overrides)
    return OIDCConfig(**values)


def test_roles_from_keycloak_groups() -> None:
    cfg = config()
    assert roles_from_claims({"groups": ["/baryon-admin"]}, cfg) == {"admin"}
    assert roles_from_claims({"groups": ["baryon-member"]}, cfg) == {"member"}
    assert roles_from_claims({"groups": ["/baryon-legal", "baryon-member"]}, cfg) == {"legal", "member"}


def test_roles_from_nested_realm_access_claim() -> None:
    cfg = config(role_claim="realm_access.roles")
    assert roles_from_claims({"realm_access": {"roles": ["baryon-admin"]}}, cfg) == {"admin"}


def test_roles_default_to_member() -> None:
    assert roles_from_claims({}, config()) == {"member"}
    assert roles_from_claims({"groups": ["offline_access"]}, config()) == {"member"}


def test_principal_from_claims_uses_org_and_user_claims() -> None:
    principal = principal_from_claims(
        {
            "sub": "user-123",
            "preferred_username": "alice",
            "organization": "org-1",
            "groups": ["baryon-legal"],
        },
        config(),
    )
    assert principal.user_id == "alice"
    assert principal.organization_id == "org-1"
    assert principal.roles == {"legal"}


def test_config_requires_https_issuer_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        config(issuer="").validate()
    with pytest.raises(ValueError):
        config(issuer="http://localhost:8180/realms/baryon-ax").validate()
    monkeypatch.setenv("OIDC_ALLOW_INSECURE_ISSUER", "true")
    config(issuer="http://localhost:8180/realms/baryon-ax").validate()


class FakeValidator:
    def validate(self, token: str) -> dict:
        if token == "good":
            return {
                "sub": "user-1",
                "preferred_username": "alice",
                "organization": "org-1",
                "groups": ["baryon-admin"],
            }
        raise AuthError("bad token")


def middleware_client() -> TestClient:
    app = FastAPI()

    @app.get("/who")
    def who(
        x_user_id: str | None = Header(None),
        x_organization_id: str | None = Header(None),
        x_roles: str | None = Header(None),
    ) -> dict:
        return {"user": x_user_id, "org": x_organization_id, "roles": x_roles}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    app.add_middleware(
        OIDCAuthMiddleware,
        config=config(),
        validator=FakeValidator(),
    )
    return TestClient(app)


def test_middleware_injects_verified_identity() -> None:
    client = middleware_client()
    response = client.get(
        "/who",
        headers={"Authorization": "Bearer good", "X-User-Id": "attacker", "X-Roles": "member"},
    )
    assert response.status_code == 200
    assert response.json() == {"user": "alice", "org": "org-1", "roles": "admin"}


def test_middleware_rejects_missing_or_invalid_token() -> None:
    client = middleware_client()
    missing = client.get("/who")
    invalid = client.get("/who", headers={"Authorization": "Bearer bad"})
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Bearer")


def test_middleware_allows_public_paths() -> None:
    client = middleware_client()
    assert client.get("/health").status_code == 200


def test_validator_verifies_signature_issuer_and_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "test-key"
    token = jwt.encode(
        {
            "sub": "user-1",
            "iss": "https://id.example",
            "aud": "baryon-ax-api",
            "exp": int(time.time()) + 300,
            "groups": ["baryon-admin"],
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    validator = OIDCValidator(config())
    validator._jwks = {"keys": [public_jwk]}
    validator._jwks_fetched_at = time.monotonic()

    claims = validator.validate(token)
    assert claims["sub"] == "user-1"

    wrong_audience = OIDCValidator(config(audience="other-api"))
    wrong_audience._jwks = {"keys": [public_jwk]}
    wrong_audience._jwks_fetched_at = time.monotonic()
    with pytest.raises(AuthError):
        wrong_audience.validate(token)


# --- Delegated identity (bots / MCP clients) and auth audit events -----------

SERVICE_TOKENS = {
    "svc-member": {
        "preferred_username": "service-account-baryon-ax-bot",
        "organization": "org-1",
        "groups": ["baryon-service", "baryon-member"],
    },
    "svc-admin": {
        "preferred_username": "service-account-ops-bot",
        "organization": "org-1",
        "groups": ["/baryon-service", "/baryon-member", "/baryon-admin"],
    },
    "user-admin": {
        "preferred_username": "alice",
        "organization": "org-1",
        "groups": ["baryon-admin"],
    },
}


class DelegationValidator:
    def validate(self, token: str) -> dict:
        if token in SERVICE_TOKENS:
            return dict(SERVICE_TOKENS[token])
        raise AuthError("Signature verification failed")


def delegation_client(failures: list | None = None, sink=None, **overrides) -> TestClient:
    from baryon.main import Principal

    app = FastAPI()

    @app.get("/who")
    def who(
        x_user_id: str | None = Header(None),
        x_organization_id: str | None = Header(None),
        x_roles: str | None = Header(None),
        x_acting_service: str | None = Header(None),
    ) -> dict:
        return {
            "user": x_user_id,
            "org": x_organization_id,
            "roles": x_roles,
            "acting_service": x_acting_service,
            "context_acting_service": current_acting_service(),
        }

    @app.post("/act")
    def act(x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None)) -> dict:
        sink.emit("chat.completed", Principal(x_user_id, x_organization_id, {"member"}), "trace-1")
        return {"ok": True}

    def on_failure(event: dict) -> None:
        if failures is not None:
            failures.append(event)
        if sink is not None:
            actor = Principal(event.get("actor") or "anonymous", "org-1", set())
            sink.emit("auth.failed", actor, "trace-failed", **event)

    app.add_middleware(
        OIDCAuthMiddleware,
        config=config(**overrides),
        validator=DelegationValidator(),
        on_failure=on_failure,
    )
    return TestClient(app)


def test_service_account_can_delegate_to_chat_user() -> None:
    client = delegation_client()
    response = client.get(
        "/who",
        headers={
            "Authorization": "Bearer svc-member",
            "X-On-Behalf-Of": "slack:U123ABC",
            "X-User-Id": "attacker",
            "X-Organization-Id": "other-org",
            "X-Acting-Service": "forged",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "user": "slack:U123ABC",
        "org": "org-1",
        "roles": "member",
        "acting_service": "service-account-baryon-ax-bot",
        "context_acting_service": "service-account-baryon-ax-bot",
    }


def test_delegation_rejected_without_service_role() -> None:
    failures: list = []
    client = delegation_client(failures)
    response = client.get("/who", headers={"Authorization": "Bearer user-admin", "X-On-Behalf-Of": "slack:U1"})
    assert response.status_code == 403
    assert "service role" in response.json()["detail"]
    assert failures and failures[0]["status"] == 403 and failures[0]["actor"] == "alice"
    assert failures[0]["organization_id"] == "org-1"


def test_delegated_roles_are_capped_by_service_account_roles() -> None:
    client = delegation_client()
    escalate = client.get(
        "/who",
        headers={"Authorization": "Bearer svc-member", "X-On-Behalf-Of": "slack:U1", "X-On-Behalf-Of-Roles": "admin"},
    )
    assert escalate.status_code == 403
    mixed = client.get(
        "/who",
        headers={
            "Authorization": "Bearer svc-member",
            "X-On-Behalf-Of": "slack:U1",
            "X-On-Behalf-Of-Roles": "admin,member",
        },
    )
    assert mixed.status_code == 200 and mixed.json()["roles"] == "member"
    admin = client.get(
        "/who",
        headers={"Authorization": "Bearer svc-admin", "X-On-Behalf-Of": "discord:42", "X-On-Behalf-Of-Roles": "admin"},
    )
    assert admin.status_code == 200 and admin.json()["roles"] == "admin"


def test_malformed_on_behalf_of_is_rejected() -> None:
    client = delegation_client()
    for bad in ["slack U1", "a" * 129, "slack:U1;admin", "", "user/../x"]:
        response = client.get("/who", headers={"Authorization": "Bearer svc-member", "X-On-Behalf-Of": bad})
        assert response.status_code == 400, bad


def test_delegation_prefix_allowlist() -> None:
    client = delegation_client(delegation_prefixes=("slack:", "discord:"))
    ok = client.get("/who", headers={"Authorization": "Bearer svc-member", "X-On-Behalf-Of": "slack:U1"})
    denied = client.get("/who", headers={"Authorization": "Bearer svc-member", "X-On-Behalf-Of": "alice"})
    assert ok.status_code == 200
    assert denied.status_code == 403


def test_roles_header_without_on_behalf_of_is_rejected() -> None:
    client = delegation_client()
    response = client.get("/who", headers={"Authorization": "Bearer svc-admin", "X-On-Behalf-Of-Roles": "admin"})
    assert response.status_code == 400


def test_service_token_without_delegation_acts_as_itself() -> None:
    client = delegation_client()
    body = client.get("/who", headers={"Authorization": "Bearer svc-member"}).json()
    assert body["user"] == "service-account-baryon-ax-bot"
    assert body["acting_service"] is None and body["context_acting_service"] is None


def test_audit_event_includes_acting_service(tmp_path) -> None:
    from baryon.main import AuditSink

    sink = AuditSink(storage_path=tmp_path / "audit.jsonl", database_url="")
    client = delegation_client(sink=sink)
    response = client.post("/act", headers={"Authorization": "Bearer svc-member", "X-On-Behalf-Of": "slack:U9"})
    assert response.status_code == 200
    event = sink.events[-1]
    assert event["user_id"] == "slack:U9"
    assert event["organization_id"] == "org-1"
    assert event["acting_service"] == "service-account-baryon-ax-bot"
    # The context is cleared once the request finishes.
    assert current_acting_service() is None


def test_auth_failed_is_audited_without_token(tmp_path) -> None:
    from baryon.main import AuditSink

    sink = AuditSink(storage_path=tmp_path / "audit.jsonl", database_url="")
    client = delegation_client(sink=sink)
    assert client.get("/who", headers={"Authorization": "Bearer secret-token-value"}).status_code == 401
    assert client.get("/who").status_code == 401
    failed = [event for event in sink.events if event["type"] == "auth.failed"]
    assert len(failed) == 2
    assert failed[0]["status"] == 401 and failed[0]["path"] == "/who"
    assert "Signature verification failed" in failed[0]["reason"]
    assert "secret-token-value" not in (tmp_path / "audit.jsonl").read_text()


def test_auth_failed_audit_is_rate_limited() -> None:
    failures: list = []
    client = delegation_client(failures, failure_audit_per_minute=2)
    for _ in range(5):
        assert client.get("/who", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert len(failures) == 2


def test_dev_mode_on_behalf_of_aliases_user_id() -> None:
    client = delegation_client(mode="dev")
    body = client.get(
        "/who",
        headers={"X-User-Id": "slack-bot", "X-On-Behalf-Of": "slack:U1", "X-Roles": "admin", "X-Organization-Id": "org-9"},
    ).json()
    assert body["user"] == "slack:U1"
    assert body["roles"] == "admin"
    assert body["org"] == "org-9"
    assert body["acting_service"] == "slack-bot"
    assert body["context_acting_service"] == "slack-bot"
    plain = client.get("/who", headers={"X-User-Id": "local-user", "X-Roles": "member"}).json()
    assert plain["user"] == "local-user" and plain["acting_service"] is None
    assert client.get("/who", headers={"X-On-Behalf-Of": "bad id"}).status_code == 400
