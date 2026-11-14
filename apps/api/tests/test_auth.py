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
