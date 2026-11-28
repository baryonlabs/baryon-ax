"""Invite-code signup: issuance, redemption, IdP stub/rollback, mail and limits."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

TEST_ROOT = Path(tempfile.mkdtemp(prefix="baryon-ax-signup-test-"))
os.environ.setdefault("DOCUMENT_STORE", str(TEST_ROOT / "documents"))
os.environ.setdefault("SYSTEM_METRICS_ENABLED", "false")

import httpx
import pytest
from fastapi.testclient import TestClient

import baryon.main as main
from baryon.auth import PLATFORM_ADMIN
from baryon.idp import IdpError, IdpSettings, KeycloakAdminAdapter, StubIdpAdapter, build_idp_adapter
from baryon.invites import (
    InviteStore,
    SignupRateLimiter,
    code_digest,
    generate_code,
    normalize_code,
)
from baryon.mailer import MailSettings, Mailer, invite_message
from baryon.tenants import OrgUserIndex, TenantRegistry, UsageMeter

client = TestClient(main.app)
PLATFORM = {"X-User-Id": "operator", "X-Organization-Id": "platform-ops", "X-Roles": PLATFORM_ADMIN}


def admin_headers(organization: str) -> dict[str, str]:
    return {"X-User-Id": "boss", "X-Organization-Id": organization, "X-Roles": "admin"}


@pytest.fixture
def signup_env(monkeypatch, tmp_path) -> SimpleNamespace:
    root = tmp_path / "tenants"
    registry = TenantRegistry(root)
    store = InviteStore(root)
    audit = main.AuditSink(storage_path=tmp_path / "audit" / "events.jsonl", database_url="")
    monkeypatch.setattr(main, "tenant_registry", registry)
    monkeypatch.setattr(main, "invite_store", store)
    monkeypatch.setattr(main, "usage_meter", UsageMeter(root, flush_seconds=0))
    monkeypatch.setattr(main, "memory_users", OrgUserIndex(root))
    monkeypatch.setattr(main, "audit", audit)
    monkeypatch.setattr(main, "signup_limiter", SignupRateLimiter(ip_limit=100, code_limit=100))
    monkeypatch.setattr(main, "idp_adapter", StubIdpAdapter())
    monkeypatch.setattr(main, "mailer", Mailer(MailSettings(host="")))
    monkeypatch.setattr(main.tenancy, "mode", "pooled")
    return SimpleNamespace(root=root, registry=registry, store=store, audit=audit)


def issue_invite(organization_id: str = "acme", email: str = "admin@acme.example", **extra) -> dict:
    response = client.post(
        "/v1/platform/invites",
        json={"organization_id": organization_id, "email": email, "expires_days": 7, **extra},
        headers=PLATFORM,
    )
    assert response.status_code == 201, response.text
    return response.json()


def redeem(code: str, email: str = "admin@acme.example", org_name: str = "Acme Corp"):
    return client.post("/v1/signup", json={"code": code, "email": email, "org_name": org_name})


def listed_tenants() -> list[dict]:
    response = client.get("/v1/platform/tenants", headers=PLATFORM)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --------------------------------------------------------------------------- issuance


def test_platform_admin_issues_a_code_once_and_list_hides_it(signup_env) -> None:
    issued = issue_invite()
    code = issued["code"]
    assert code.startswith("AX-")
    assert normalize_code(code) != "" and len(normalize_code(code)) == 18
    assert issued["status"] == "pending" and issued["organization_id"] == "acme"
    assert "code_hash" not in issued and "claim_token" not in issued

    listing = client.get("/v1/platform/invites", headers=PLATFORM)
    assert listing.status_code == 200
    assert code not in listing.text and "code_hash" not in listing.text
    assert listing.json()["total"] == 1

    persisted = (signup_env.root / "invites.json").read_text(encoding="utf-8")
    assert code not in persisted  # raw code is never stored
    assert code_digest(code) in persisted

    # A tenant admin is not a platform admin.
    for identity in (admin_headers("acme"), {"X-User-Id": "alice", "X-Organization-Id": "acme", "X-Roles": "member"}):
        assert client.post("/v1/platform/invites", json={"organization_id": "acme", "email": "a@b.com"},
                           headers=identity).status_code == 403
        assert client.get("/v1/platform/invites", headers=identity).status_code == 403
        assert client.delete(f"/v1/platform/invites/{issued['id']}", headers=identity).status_code == 403


def test_expired_code_and_wrong_code_share_one_response(signup_env, monkeypatch) -> None:
    clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    store = InviteStore(signup_env.root, clock=lambda: clock[0])
    monkeypatch.setattr(main, "invite_store", store)
    issued = issue_invite()
    clock[0] += timedelta(days=8)

    expired = redeem(issued["code"])
    wrong = redeem("AX-AAAA-AAAA-AAAA-AAAA")
    assert expired.status_code == wrong.status_code == 400
    assert expired.json() == wrong.json()
    assert expired.json()["detail"]["code"] == "invite_invalid"
    assert "email" not in expired.json()["detail"] and "organization" not in expired.json()["detail"]


def test_wrong_email_is_rejected_like_a_wrong_code(signup_env) -> None:
    issued = issue_invite(email="admin@acme.example")
    wrong_email = redeem(issued["code"], email="someone@else.example")
    wrong_code = redeem("AX-AAAA-AAAA-AAAA-AAAA", email="admin@acme.example")
    assert wrong_email.status_code == wrong_code.status_code == 400
    assert wrong_email.json() == wrong_code.json()
    assert signup_env.registry.get("acme") is None


def test_revoke_blocks_signup_and_unknown_ids_are_404(signup_env) -> None:
    issued = issue_invite()
    revoked = client.delete(f"/v1/platform/invites/{issued['id']}", headers=PLATFORM)
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert redeem(issued["code"]).status_code == 400
    assert signup_env.registry.get("acme") is None
    assert client.delete("/v1/platform/invites/missing", headers=PLATFORM).status_code == 404


def test_a_redeemed_code_cannot_be_revoked(signup_env) -> None:
    issued = issue_invite()
    assert redeem(issued["code"]).status_code == 201
    assert client.delete(f"/v1/platform/invites/{issued['id']}", headers=PLATFORM).status_code == 409


# --------------------------------------------------------------------------- redemption


def test_signup_provisions_the_tenant_and_is_idempotent(signup_env) -> None:
    issued = issue_invite(organization_id="acme", email="admin@acme.example")
    first = redeem(issued["code"])
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["status"] == "invited" and body["organization_id"] == "acme"
    assert body["email_sent"] is False
    assert body["login_url"] == "http://localhost:5173/?organization=acme"
    assert body["already_redeemed"] is False

    tenants = listed_tenants()
    assert [tenant["id"] for tenant in tenants] == ["acme"]
    assert tenants[0]["plan"] == "standard"
    assert tenants[0]["status"] == "active"
    assert tenants[0]["notes"] == f"invite:{issued['id']}"

    # The invited address is recorded on the invite, and the invite is used.
    stored = signup_env.store.get(issued["id"])
    assert stored.status() == "used" and stored.used_by == "admin@acme.example"

    # A retry with the same address is idempotent: no second tenant, same org.
    second = redeem(issued["code"])
    assert second.status_code == 201, second.text
    assert second.json()["already_redeemed"] is True
    assert second.json()["organization_id"] == "acme"
    assert [tenant["id"] for tenant in listed_tenants()] == ["acme"]

    # A different address on the same code is rejected, without saying why.
    other = redeem(issued["code"], email="other@acme.example")
    assert other.status_code == 400 and other.json()["detail"]["code"] == "invite_invalid"
    assert len(listed_tenants()) == 1


def test_codes_are_forgiving_about_case_spaces_and_dashes(signup_env) -> None:
    issued = issue_invite()
    pasted = " " + issued["code"].lower().replace("-", " ") + " "
    assert redeem(pasted).status_code == 201
    assert signup_env.registry.get("acme") is not None


def test_signup_audit_and_auth_me_show_the_new_tenant(signup_env) -> None:
    issued = issue_invite()
    first = redeem(issued["code"])
    assert first.status_code == 201, first.text

    events = client.get("/v1/platform/tenants/acme/audit-events", headers=PLATFORM).json()["data"]
    types = [event["type"] for event in events]
    assert "tenant.created" in types and "invite.redeemed" in types
    created = next(event for event in events if event["type"] == "tenant.created")
    assert created["source"] == "invite_signup"
    assert created["invite_id"] == issued["id"]
    assert created["user_id"] == "admin@acme.example"
    redeemed = next(event for event in events if event["type"] == "invite.redeemed")
    assert redeemed["tenant_id"] == "acme" and redeemed["idp"] == "stub"
    # The raw code never reaches an audit event.
    assert issued["code"] not in json.dumps(events)

    me = client.get(
        "/v1/auth/me",
        headers={"X-User-Id": "admin@acme.example", "X-Organization-Id": "acme", "X-Roles": "admin"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["organization_id"] == "acme"
    assert "admin" in me.json()["roles"]


def test_idp_failure_rolls_back_the_tenant_and_releases_the_code(signup_env, monkeypatch) -> None:
    class BrokenIdp:
        mode = "stub"

        def provision(self, organization_id, organization_name, admin_email, login_url):
            raise IdpError("keycloak unavailable", status_code=503)

        def rollback(self, provisioned):
            raise AssertionError("nothing was provisioned")

    monkeypatch.setattr(main, "idp_adapter", BrokenIdp())
    issued = issue_invite()

    failed = redeem(issued["code"])
    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] == "provision_failed"
    assert signup_env.registry.get("acme") is None
    assert listed_tenants() == []
    invite = signup_env.store.get(issued["id"])
    assert invite.status() == "pending" and invite.claimed_at is None and invite.claim_token is None
    assert "invite.provision_failed" in [event["type"] for event in signup_env.audit.events]

    # The same code works once the IdP is healthy again.
    monkeypatch.setattr(main, "idp_adapter", StubIdpAdapter())
    retry = redeem(issued["code"])
    assert retry.status_code == 201, retry.text
    assert signup_env.registry.get("acme") is not None


def test_signup_rate_limits_by_ip_and_by_code(signup_env, monkeypatch) -> None:
    monkeypatch.setattr(main, "signup_limiter", SignupRateLimiter(ip_limit=2, code_limit=100))
    issued = issue_invite()
    assert redeem("AX-AAAA-AAAA-AAAA-AAAA").status_code == 400
    assert redeem("AX-BBBB-BBBB-BBBB-BBBB").status_code == 400
    blocked = redeem(issued["code"])
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0
    assert signup_env.registry.get("acme") is None

    monkeypatch.setattr(main, "signup_limiter", SignupRateLimiter(ip_limit=100, code_limit=1))
    assert redeem("AX-CCCC-CCCC-CCCC-CCCC").status_code == 400
    too_many = redeem("AX-CCCC-CCCC-CCCC-CCCC")
    assert too_many.status_code == 429 and too_many.json()["detail"]["code"] == "rate_limited"


# --------------------------------------------------------------------------- store & IdP units


def test_claim_handshake_precludes_a_second_redemption(tmp_path) -> None:
    store = InviteStore(tmp_path)
    invite, code = store.create("acme", "a@b.com")
    state, claimed, token = store.claim(code, "a@b.com")
    assert state == "claimed" and claimed.id == invite.id and token
    assert store.claim(code, "a@b.com")[0] == "busy"
    store.release(invite.id, token)
    assert store.claim(code, "a@b.com")[0] == "claimed"
    second = store.claim(code, "a@b.com")[0]
    assert second == "busy"


def test_generated_codes_are_unique_and_normalized() -> None:
    codes = {generate_code() for _ in range(200)}
    assert len(codes) == 200
    for code in list(codes)[:20]:
        assert normalize_code(code.lower().replace("-", " ")) == normalize_code(code)


def test_idp_auto_mode_is_stub_in_dev(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("OIDC_ISSUER", "")
    monkeypatch.setenv("KEYCLOAK_URL", "")
    monkeypatch.setenv("KEYCLOAK_ADMIN_CLIENT_ID", "")
    monkeypatch.setenv("KEYCLOAK_ADMIN_CLIENT_SECRET", "")
    monkeypatch.delenv("IDP_MODE", raising=False)
    assert build_idp_adapter().mode == "stub"


def test_keycloak_adapter_creates_org_user_and_invitation() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "admin-token", "expires_in": 300})
        if request.method == "POST" and path.endswith("/organizations"):
            return httpx.Response(201, headers={"Location": "http://kc/admin/realms/baryon-ax/organizations/org-1"})
        if request.method == "GET" and path.endswith("/users"):
            return httpx.Response(200, json=[])
        if request.method == "POST" and path.endswith("/users"):
            return httpx.Response(201, headers={"Location": "http://kc/admin/realms/baryon-ax/users/user-1"})
        if request.method == "GET" and path.endswith("/groups"):
            return httpx.Response(200, json=[{"id": "group-1", "name": "baryon-admin"}])
        if request.method == "POST" and path.endswith("/members"):
            return httpx.Response(204)
        if request.method == "PUT" and "/groups/" in path:
            return httpx.Response(204)
        if request.method == "PUT" and path.endswith("/execute-actions-email"):
            return httpx.Response(204)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)

    settings = IdpSettings(
        mode="keycloak", base_url="http://kc", realm="baryon-ax",
        client_id="admin", client_secret="secret", admin_group="baryon-admin",
    )
    adapter = KeycloakAdminAdapter(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    provisioned = adapter.provision("acme", "Acme Corp", "admin@acme.example", "https://ax.example/?organization=acme")
    assert provisioned.external_id == "org-1"
    assert provisioned.admin_user_id == "user-1" and provisioned.created_user is True
    assert provisioned.created_organization is True
    paths = [path for _, path in calls]
    assert "/admin/realms/baryon-ax/organizations" in paths
    assert "/admin/realms/baryon-ax/users" in paths
    assert "/admin/realms/baryon-ax/organizations/org-1/members" in paths
    assert "/admin/realms/baryon-ax/users/user-1/groups/group-1" in paths
    assert "/admin/realms/baryon-ax/users/user-1/execute-actions-email" in paths

    adapter.rollback(provisioned)
    assert ("DELETE", "/admin/realms/baryon-ax/organizations/org-1") in calls
    assert ("DELETE", "/admin/realms/baryon-ax/users/user-1") in calls


def test_mailer_without_smtp_logs_the_link_and_reports_not_sent(caplog) -> None:
    mailer = Mailer(MailSettings(host=""))
    with caplog.at_level(logging.INFO, logger="baryon.ax.mail"):
        sent = mailer.send_invite("admin@acme.example", "Acme", "acme", "http://localhost:5173/?organization=acme")
    assert sent is False
    assert "http://localhost:5173/?organization=acme" in caplog.text


def test_invite_email_is_bilingual_and_names_the_organization() -> None:
    message = invite_message("a@b.com", "Acme", "acme", "https://ax.example/?organization=acme", "no-reply@baryon.local")
    body = message.get_content()
    assert "만들어졌습니다" in body
    assert "has been created" in body
    assert "https://ax.example/?organization=acme" in body
    assert message["To"] == "a@b.com"
