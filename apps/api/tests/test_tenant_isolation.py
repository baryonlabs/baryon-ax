"""Pooled multi-tenancy: registry, platform admin, quotas, purge, and cross-tenant isolation.

Every isolation test uses two tenants whose users share the *same* user ids
(``alice`` / ``boss``), so any store keyed only by user id would leak.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="baryon-ax-tenancy-test-"))
os.environ.setdefault("DOCUMENT_STORE", str(TEST_ROOT / "documents"))
os.environ.setdefault("AUDIT_LOG", str(TEST_ROOT / "audit" / "events.jsonl"))
os.environ.setdefault("FASTEMBED_CACHE_PATH", str(TEST_ROOT / "fastembed"))
os.environ.setdefault("MEM0_HISTORY_DB", str(TEST_ROOT / "mem0" / "history.db"))
os.environ.setdefault("SYSTEM_METRICS_ENABLED", "false")

import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

import baryon.main as main
from baryon.auth import (
    PLATFORM_ADMIN,
    AuthError,
    OIDCAuthMiddleware,
    OIDCConfig,
    delegated_roles,
    principal_from_claims,
    roles_from_claims,
)
from baryon.tenants import (
    OrgUserIndex,
    TenantError,
    TenantExists,
    TenantRegistry,
    UploadReservations,
    UsageMeter,
    estimate_tokens,
)

client = TestClient(main.app)
PLATFORM = {"X-User-Id": "operator", "X-Organization-Id": "platform-ops", "X-Roles": PLATFORM_ADMIN}


def headers(organization: str, user: str = "alice", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def admin(organization: str) -> dict[str, str]:
    return headers(organization, user="boss", roles="admin")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def pooled(monkeypatch, tmp_path):
    """Switch the running app to pooled mode with a fresh registry and meter."""
    root = tmp_path / "tenants"
    registry = TenantRegistry(root)
    monkeypatch.setattr(main.tenancy, "mode", "pooled")
    monkeypatch.setattr(main, "tenant_registry", registry)
    monkeypatch.setattr(main, "usage_meter", UsageMeter(root, flush_seconds=0))
    monkeypatch.setattr(main, "upload_reservations", UploadReservations())
    monkeypatch.setattr(main, "memory_users", OrgUserIndex(root))
    return registry


def create_tenant(tenant_id: str, **quotas) -> dict:
    response = client.post(
        "/v1/platform/tenants",
        json={"id": tenant_id, "name": tenant_id.upper(), "plan": "standard", "quotas": quotas},
        headers=PLATFORM,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def two_tenants(pooled):
    a, b = new_id("iso-a"), new_id("iso-b")
    create_tenant(a)
    create_tenant(b)
    return a, b


def upload(organization: str, name: str, body: bytes, roles: str = "member", **params) -> dict:
    response = client.post(
        "/v1/documents", files={"file": (name, body)}, data={"allowed_roles": roles},
        headers=admin(organization), params=params,
    )
    assert response.status_code in (200, 202), response.text
    return response.json()


def ask(organization: str, question: str, user: str = "alice") -> dict:
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": question}], "stream": False},
        headers=headers(organization, user=user, roles="admin"),
    )
    assert response.status_code == 200, response.text
    return response.json()


def rpc(organization: str, name: str, arguments: dict, roles: str = "admin") -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    response = client.post("/mcp", json=body, headers={**headers(organization, user="boss", roles=roles),
                                                       "Accept": "application/json, text/event-stream"})
    assert response.status_code == 200, response.text
    return response.json()["result"]


# --------------------------------------------------------------------------- registry


def test_registry_crud_and_persistence(tmp_path) -> None:
    registry = TenantRegistry(tmp_path)
    tenant = registry.create("acme", "Acme", plan="pro", quotas={"max_documents": 10})
    assert tenant.status == "active"
    assert tenant.quotas == {"max_documents": 10, "max_storage_bytes": None,
                             "chat_requests_per_day": None, "tokens_per_month": None}
    with pytest.raises(TenantExists):
        registry.create("acme", "Again")
    for bad in ("A", "x", "has space", "UPPER", "a" * 41, "../etc"):
        with pytest.raises(TenantError):
            registry.create(bad, "Bad")
    with pytest.raises(TenantError):
        registry.create("bad-quota", "Bad", quotas={"max_documents": -1})
    with pytest.raises(TenantError):
        registry.create("bad-key", "Bad", quotas={"max_users": 1})

    registry.update("acme", name="Acme Corp", quotas={"chat_requests_per_day": 5}, status="suspended")
    reloaded = TenantRegistry(tmp_path).get("acme")
    assert reloaded.name == "Acme Corp" and reloaded.status == "suspended"
    assert reloaded.quotas["max_documents"] == 10 and reloaded.quotas["chat_requests_per_day"] == 5
    registry.update("acme", quotas={"max_documents": None})
    assert TenantRegistry(tmp_path).get("acme").quotas["max_documents"] is None
    assert [item.id for item in registry.list("suspended")] == ["acme"]

    registry.mark_purged("acme", {"documents": 0})
    with pytest.raises(TenantError):
        registry.update("acme", status="active")
    # Atomic writes leave no temp files behind.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["tenants.json"]


def test_platform_endpoints_crud(pooled) -> None:
    tenant_id = new_id("crud")
    created = create_tenant(tenant_id, max_documents=3)
    assert created["quotas"]["max_documents"] == 3
    assert client.post("/v1/platform/tenants", json={"id": tenant_id, "name": "dup"}, headers=PLATFORM).status_code == 409
    assert client.post("/v1/platform/tenants", json={"id": "Bad Id", "name": "x"}, headers=PLATFORM).status_code == 422

    listing = client.get("/v1/platform/tenants", headers=PLATFORM).json()
    assert tenant_id in {item["id"] for item in listing["data"]} and listing["mode"] == "pooled"
    assert client.get(f"/v1/platform/tenants/{tenant_id}", headers=PLATFORM).json()["name"] == tenant_id.upper()
    assert client.get("/v1/platform/tenants/missing-tenant", headers=PLATFORM).status_code == 404

    patched = client.patch(f"/v1/platform/tenants/{tenant_id}",
                           json={"plan": "enterprise", "quotas": {"chat_requests_per_day": 100}}, headers=PLATFORM)
    assert patched.status_code == 200, patched.text
    assert patched.json()["plan"] == "enterprise"
    assert patched.json()["quotas"] == {"max_documents": 3, "max_storage_bytes": None,
                                        "chat_requests_per_day": 100, "tokens_per_month": None}
    assert client.patch(f"/v1/platform/tenants/{tenant_id}", json={"status": "deleting"}, headers=PLATFORM).status_code == 422

    assert client.post(f"/v1/platform/tenants/{tenant_id}/suspend", headers=PLATFORM).json()["status"] == "suspended"
    assert client.post(f"/v1/platform/tenants/{tenant_id}/resume", headers=PLATFORM).json()["status"] == "active"

    events = client.get(f"/v1/platform/tenants/{tenant_id}/audit-events", headers=PLATFORM).json()["data"]
    types = [event["type"] for event in events]
    for expected in ("tenant.created", "tenant.updated", "tenant.suspended", "tenant.resumed"):
        assert expected in types
    platform_events = [event for event in events if event["type"].startswith("tenant.")]
    # Recorded under the acting platform administrator, not the tenant.
    assert all(event["user_id"] == "operator" and event["organization_id"] == "platform-ops" for event in platform_events)


def test_platform_endpoints_reject_tenant_admins(two_tenants) -> None:
    tenant_a, _ = two_tenants
    for identity in (admin(tenant_a), headers(tenant_a), headers("platform-ops", roles="admin")):
        assert client.get("/v1/platform/tenants", headers=identity).status_code == 403
        assert client.post("/v1/platform/tenants", json={"id": new_id("evil"), "name": "x"}, headers=identity).status_code == 403
        assert client.get(f"/v1/platform/tenants/{tenant_a}", headers=identity).status_code == 403
        assert client.patch(f"/v1/platform/tenants/{tenant_a}", json={"plan": "free"}, headers=identity).status_code == 403
        assert client.post(f"/v1/platform/tenants/{tenant_a}/suspend", headers=identity).status_code == 403
        assert client.post(f"/v1/platform/tenants/{tenant_a}/resume", headers=identity).status_code == 403
        assert client.get(f"/v1/platform/tenants/{tenant_a}/usage", headers=identity).status_code == 403
        assert client.get(f"/v1/platform/tenants/{tenant_a}/audit-events", headers=identity).status_code == 403
        assert client.delete(f"/v1/platform/tenants/{tenant_a}", headers=identity).status_code == 403
    assert main.tenant_registry.get(tenant_a).status == "active"


# --------------------------------------------------------------------------- tenant gate


def test_single_mode_needs_no_registry() -> None:
    assert main.tenancy.mode == "single"
    organization = new_id("unregistered")
    assert client.get("/v1/documents", headers=headers(organization)).status_code == 200


def test_pooled_rejects_unregistered_and_suspended_tenants(pooled) -> None:
    unknown = new_id("nobody")
    response = client.get("/v1/documents", headers=headers(unknown))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "tenant_not_registered"
    # Missing organization header falls back to local-org, which is not registered either.
    assert client.get("/v1/documents").json()["detail"]["code"] == "tenant_not_registered"
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers(unknown)).status_code == 403
    # Public paths stay open.
    assert client.get("/health").status_code == 200
    assert client.get("/v1/open-source").status_code == 200

    tenant_id = new_id("gate")
    create_tenant(tenant_id)
    assert client.get("/v1/documents", headers=headers(tenant_id)).status_code == 200
    client.post(f"/v1/platform/tenants/{tenant_id}/suspend", headers=PLATFORM)
    suspended = client.get("/v1/documents", headers=headers(tenant_id))
    assert suspended.status_code == 403
    assert suspended.json()["detail"] == {"code": "tenant_suspended", "message": "Tenant is suspended",
                                          "tenant_id": tenant_id, "status": "suspended"}
    assert client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
                       headers=headers(tenant_id)).status_code == 403
    client.post(f"/v1/platform/tenants/{tenant_id}/resume", headers=PLATFORM)
    assert client.get("/v1/documents", headers=headers(tenant_id)).status_code == 200


# --------------------------------------------------------------------------- isolation


def test_documents_files_chunks_search_and_jobs_are_isolated(two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    secret = f"secretcodeword{uuid.uuid4().hex[:6]}"
    document = upload(tenant_a, "a-plan.md", f"# A plan\n\nThe {secret} launch is in March.".encode())
    document_id = document["id"]
    job = upload(tenant_a, "a-async.md", f"async {secret} notes".encode(), **{"async": "true"})

    for identity in (headers(tenant_b), admin(tenant_b)):
        assert client.get(f"/v1/documents/{document_id}", headers=identity).status_code == 404
        assert client.get(f"/v1/documents/{document_id}?chunk=1", headers=identity).status_code == 404
        assert client.get(f"/v1/documents/{document_id}/file", headers=identity).status_code == 404
        assert client.get(f"/v1/jobs/{job['job_id']}", headers=identity).status_code == 404
        listed = client.get("/v1/documents", headers=identity).json()["data"]
        assert all(item["id"] != document_id for item in listed)
        hits = client.get("/v1/documents/search", params={"q": secret}, headers=identity).json()["data"]
        assert hits == []
    assert client.patch(f"/v1/documents/{document_id}", json={"allowed_roles": ["member"], "tags": ["x"]},
                        headers=admin(tenant_b)).status_code == 404
    assert client.delete(f"/v1/documents/{document_id}", headers=admin(tenant_b)).status_code == 404

    # Owner tenant still sees everything, including the async job.
    assert client.get(f"/v1/documents/{document_id}/file", headers=admin(tenant_a)).status_code == 200
    assert client.get(f"/v1/jobs/{job['job_id']}", headers=admin(tenant_a)).json()["state"] == "done"
    assert client.get("/v1/documents/search", params={"q": secret}, headers=admin(tenant_a)).json()["data"]

    # Hybrid/lexical search is scoped too.
    principal_b = main.Principal("alice", tenant_b, {"member", "admin"})
    assert main.documents._lexical_search(principal_b, secret) == []

    # Chat in B never cites A.
    answer = ask(tenant_b, f"What about the {secret} launch?")
    assert all(item["document_id"] != document_id for item in answer["citations"])
    # Ossie export lists only the caller's documents.
    exported = client.get("/v1/ossie/export", headers=admin(tenant_b)).json()
    exported_ids = {item["id"] for item in exported["nonportable_extensions"][0]["data"]["documents"]}
    assert document_id not in exported_ids


def test_mcp_tools_are_isolated(two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    secret = f"mcpsecret{uuid.uuid4().hex[:6]}"
    upload(tenant_a, "mcp.md", f"The {secret} budget is 7 million.".encode())
    assert secret in json.dumps(rpc(tenant_a, "search_documents", {"query": secret}))
    result_b = rpc(tenant_b, "search_documents", {"query": secret})
    assert result_b["structuredContent"]["results"] == []
    asked = rpc(tenant_b, "ask", {"question": f"How big is the {secret} budget?"})
    assert asked["structuredContent"]["citations"] == []

    rpc(tenant_a, "ask", {"question": f"unanswerable {uuid.uuid4().hex[:6]} question about renewals?"})
    gaps_a = rpc(tenant_a, "list_knowledge_gaps", {"status": "all"})["structuredContent"]["gaps"]
    gaps_b = rpc(tenant_b, "list_knowledge_gaps", {"status": "all"})["structuredContent"]["gaps"]
    assert gaps_a and {gap["id"] for gap in gaps_a}.isdisjoint({gap["id"] for gap in gaps_b})

    draft = rpc(tenant_a, "record_answer", {"question": "Who approves travel?", "answer": "The team lead."})
    entry_id = draft["structuredContent"]["entry"]["id"]
    published = rpc(tenant_b, "publish_answer", {"entry_id": entry_id})
    assert published["isError"] is True and "404" in published["content"][0]["text"]


def test_gaps_clusters_packs_and_ossie_are_isolated(two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    ask(tenant_a, f"2031년 {uuid.uuid4().hex[:4]} 계약 갱신 일정은?")
    gaps_a = client.get("/v1/admin/gaps?status=all", headers=admin(tenant_a)).json()["data"]
    assert gaps_a
    gap_id = gaps_a[0]["id"]

    assert all(gap["id"] != gap_id for gap in client.get("/v1/admin/gaps?status=all", headers=admin(tenant_b)).json()["data"])
    clusters_b = client.get("/v1/admin/gaps/clusters", headers=admin(tenant_b)).json()["data"]
    assert all(gap_id not in cluster["gap_ids"] for cluster in clusters_b)
    assert client.post(f"/v1/admin/gaps/{gap_id}/dismiss", headers=admin(tenant_b)).status_code == 404
    assert client.post("/v1/admin/ontology-packs", json={"gap_ids": [gap_id]}, headers=admin(tenant_b)).status_code == 404
    questions_b = client.get("/v1/tacit/questions", headers=admin(tenant_b)).json()["data"]
    assert all(item.get("gap_id") != gap_id for item in questions_b)

    pack = client.post("/v1/admin/ontology-packs", json={"gap_ids": [gap_id]}, headers=admin(tenant_a)).json()
    pack_id = pack["id"]
    assert all(item["id"] != pack_id for item in client.get("/v1/admin/ontology-packs", headers=admin(tenant_b)).json()["data"])
    for method, path in (
        ("get", f"/v1/admin/ontology-packs/{pack_id}"),
        ("get", f"/v1/admin/ontology-packs/{pack_id}/ossie"),
        ("post", f"/v1/admin/ontology-packs/{pack_id}/approve"),
        ("post", f"/v1/admin/ontology-packs/{pack_id}/verify"),
    ):
        assert getattr(client, method)(path, headers=admin(tenant_b)).status_code == 404, path
    assert client.get(f"/v1/admin/ontology-packs/{pack_id}", headers=admin(tenant_a)).status_code == 200


def test_tacit_feedback_audit_and_settings_are_isolated(two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    document = upload(tenant_a, "attach.md", b"attachment body for tacit entry")
    entry = client.post("/v1/tacit/entries", json={"question": "How do we onboard?", "answer": "Buddy system."},
                        headers=headers(tenant_a)).json()
    entry_id = entry["id"]

    # B, with the same user id, cannot see, edit or publish A's answer.
    assert all(item["id"] != entry_id for item in client.get("/v1/tacit/entries", headers=headers(tenant_b)).json()["data"])
    assert all(item["id"] != entry_id
               for item in client.get("/v1/tacit/entries?scope=all", headers=admin(tenant_b)).json()["data"])
    assert client.patch(f"/v1/tacit/entries/{entry_id}", json={"answer": "hijack"}, headers=headers(tenant_b)).status_code == 404
    assert client.post(f"/v1/tacit/entries/{entry_id}/publish", headers=admin(tenant_b)).status_code == 404
    # B cannot attach A's document to its own answer.
    own = client.post("/v1/tacit/entries", json={"question": "Q?", "answer": "A.", "attachment_ids": [document["id"]]},
                      headers=headers(tenant_b)).json()
    assert own["attachments"] == []

    client.post("/v1/feedback", json={"trace_id": "t-a", "rating": -1, "question": "secret question"}, headers=headers(tenant_a))
    feedback_b = client.get("/v1/admin/feedback", headers=admin(tenant_b)).json()
    assert all(item["question"] != "secret question" for item in feedback_b["data"])

    events_b = client.get("/v1/audit-events", headers=admin(tenant_b)).json()["data"]
    assert events_b and all(event["organization_id"] == tenant_b for event in events_b)

    settings_b = client.get("/v1/admin/settings", headers=admin(tenant_b)).json()
    assert settings_b["system"]["scope"] == "tenant"
    assert settings_b["system"]["host"]["hostname"] == "shared"
    assert settings_b["system"]["storage"]["documents_count"] == settings_b["counts"]["documents"]
    assert settings_b["links"] == {}
    assert settings_b["tenant"]["id"] == tenant_b


def test_reindex_and_connector_tools_are_scoped_in_pooled_mode(two_tenants, monkeypatch) -> None:
    tenant_a, tenant_b = two_tenants
    upload(tenant_a, "reindex.md", b"reindex me")
    indexed: list[str] = []

    class Recorder:
        enabled = True

        def index_document(self, document) -> None:
            indexed.append(document.organization_id)

    monkeypatch.setattr(main.documents, "vector", Recorder())
    monkeypatch.setattr(main.vector, "enabled", True)
    result = client.post("/v1/admin/reindex", headers=admin(tenant_b))
    assert result.status_code == 200, result.text
    assert set(indexed) <= {tenant_b}

    tool_id = main.TOOLS[0].id
    response = client.post(f"/v1/tools/{tool_id}/call", json={"arguments": {}}, headers=admin(tenant_a))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "tool_execution_not_isolated"
    assert client.get("/v1/mcp/status", headers=admin(tenant_a)).json()["execution"] == "disabled_pooled"


def test_qdrant_search_filters_by_organization(monkeypatch) -> None:
    monkeypatch.delenv("QDRANT_URL", raising=False)
    captured: dict = {}

    class Result:
        points: list = []

    class FakeClient:
        def query_points(self, collection, query, query_filter, limit, with_payload):
            captured["filter"] = query_filter
            return Result()

        def delete(self, collection, points_selector, wait):
            captured["delete"] = points_selector

    index = main.VectorIndex()
    index.enabled, index.client = True, FakeClient()
    monkeypatch.setattr(index, "_embed", lambda texts, query=False: [[0.0, 0.0, 0.0]])
    for roles in ({"member"}, {"admin"}):
        assert index.search(main.Principal("alice", "org-a", roles), "query", 4) == []
        must = captured["filter"].must
        assert any(condition.key == "organization_id" and condition.match.value == "org-a" for condition in must)
    index.delete_organization("org-a")
    condition = captured["delete"].must[0]
    assert condition.key == "organization_id" and condition.match.value == "org-a"


class FakeMem0:
    """Mimics the pre-fix weakness: search/get_all filter only by user_id."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.deleted_users: list[str] = []
        self.filters: list[dict] = []

    def add(self, text, user_id, metadata):
        row = {"id": uuid.uuid4().hex, "memory": text, "user_id": user_id, "metadata": metadata}
        self.rows.append(row)
        return {"results": [{"id": row["id"], "memory": text}]}

    def _match(self, filters):
        self.filters.append(filters)
        return {"results": [row for row in self.rows if row["user_id"] == filters["user_id"]]}

    def search(self, query, filters, limit):
        return self._match(filters)

    def get_all(self, filters, limit):
        return self._match(filters)

    def delete(self, memory_id):
        self.rows = [row for row in self.rows if row["id"] != memory_id]

    def delete_all(self, user_id):
        self.deleted_users.append(user_id)
        self.rows = [row for row in self.rows if row["user_id"] != user_id]


def test_memories_are_namespaced_by_organization(two_tenants, monkeypatch) -> None:
    tenant_a, tenant_b = two_tenants
    fake = FakeMem0()
    monkeypatch.setattr(main.memory_service, "enabled", True)
    monkeypatch.setattr(main.memory_service, "memory", fake)

    added = client.post("/v1/memories", json={"text": "alice in A likes tea", "metadata": {"organization_id": tenant_b}},
                        headers=headers(tenant_a))
    assert added.status_code == 200, added.text
    assert fake.rows[0]["user_id"] == f"{tenant_a}::alice"
    # Caller metadata cannot forge the organization stamp.
    assert fake.rows[0]["metadata"]["organization_id"] == tenant_a
    memory_id = fake.rows[0]["id"]

    assert client.get("/v1/memories", headers=headers(tenant_b)).json()["data"] == []
    assert client.get("/v1/memories/search", params={"q": "tea"}, headers=headers(tenant_b)).json()["data"] == []
    assert client.delete(f"/v1/memories/{memory_id}", headers=headers(tenant_b)).status_code == 404
    assert fake.filters[-1]["organization_id"] == tenant_b
    assert [item["id"] for item in client.get("/v1/memories", headers=headers(tenant_a)).json()["data"]] == [memory_id]

    # Single mode keeps bare user ids so existing memories stay reachable.
    monkeypatch.setattr(main.tenancy, "mode", "single")
    assert main.tenant_user_key(main.Principal("alice", tenant_a, set())) == "alice"


# --------------------------------------------------------------------------- quotas & metering


def test_document_and_storage_quotas_return_429(pooled) -> None:
    tenant_id = new_id("quota-docs")
    create_tenant(tenant_id, max_documents=1, max_storage_bytes=1_000)
    upload(tenant_id, "first.md", b"first document")
    response = client.post("/v1/documents", files={"file": ("second.md", b"second")}, headers=admin(tenant_id))
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "quota_exceeded" and detail["quota"] == "max_documents"
    assert detail["limit"] == 1 and detail["used"] == 1

    client.patch(f"/v1/platform/tenants/{tenant_id}", json={"quotas": {"max_documents": None}}, headers=PLATFORM)
    too_big = client.post("/v1/documents", files={"file": ("big.md", b"x" * 2_000)}, headers=admin(tenant_id))
    assert too_big.status_code == 429 and too_big.json()["detail"]["quota"] == "max_storage_bytes"
    events = client.get("/v1/audit-events", headers=admin(tenant_id)).json()["data"]
    assert [event["quota"] for event in events if event["type"] == "tenant.quota_exceeded"] == ["max_documents", "max_storage_bytes"]

    usage = client.get(f"/v1/platform/tenants/{tenant_id}/usage", headers=PLATFORM).json()
    assert usage["documents"] == 1 and usage["today"]["uploads"] == 1
    assert usage["storage_bytes"] == len(b"first document")


def test_chat_request_and_token_quotas_return_429(pooled) -> None:
    tenant_id = new_id("quota-chat")
    create_tenant(tenant_id, chat_requests_per_day=2)
    ask(tenant_id, "first question")
    ask(tenant_id, "second question")
    blocked = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "third"}], "stream": False},
                          headers=headers(tenant_id))
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["quota"] == "chat_requests_per_day"
    assert int(blocked.headers["retry-after"]) > 0
    # MCP ask goes through the same quota.
    blocked_mcp = rpc(tenant_id, "ask", {"question": "via mcp"})
    assert blocked_mcp["isError"] is True and "429" in blocked_mcp["content"][0]["text"]

    usage = client.get(f"/v1/platform/tenants/{tenant_id}/usage", headers=PLATFORM).json()
    assert usage["today"]["chat_requests"] == 2
    assert usage["this_month"]["prompt_tokens"] > 0 and usage["this_month"]["completion_tokens"] > 0

    token_tenant = new_id("quota-tokens")
    create_tenant(token_tenant, tokens_per_month=1)
    ask(token_tenant, "uses up the token budget")
    over = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "again"}], "stream": False},
                       headers=headers(token_tenant))
    assert over.status_code == 429 and over.json()["detail"]["quota"] == "tokens_per_month"
    events = client.get(f"/v1/platform/tenants/{token_tenant}/audit-events", headers=PLATFORM).json()["data"]
    assert any(event["type"] == "tenant.quota_exceeded" and event["quota"] == "tokens_per_month" for event in events)


def test_streaming_chat_is_metered(pooled) -> None:
    tenant_id = new_id("stream")
    create_tenant(tenant_id)
    with client.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "stream me"}]},
                       headers=headers(tenant_id)) as response:
        assert response.status_code == 200
        "".join(response.iter_text())
    usage = main.usage_meter.usage(tenant_id)
    assert usage["today"]["chat_requests"] == 1 and usage["today"]["completion_tokens"] > 0


def test_upstream_usage_is_preferred_over_estimate(pooled) -> None:
    sink: dict = {}
    main.ModelAdapter._capture_usage({"usage": {"prompt_tokens": 11, "completion_tokens": 7}}, sink)
    assert sink == {"prompt_tokens": 11, "completion_tokens": 7}
    principal = main.Principal("alice", "meter-org", {"member"})
    request = main.ChatRequest(messages=[main.Message(role="user", content="hello")])
    assert main.meter_chat_tokens(principal, request, [], [], "answer", sink) == {
        "prompt_tokens": 11, "completion_tokens": 7, "estimated": False}
    estimated = main.meter_chat_tokens(principal, request, [], [], "answer text", {})
    assert estimated["estimated"] is True and estimated["completion_tokens"] == estimate_tokens("answer text")
    assert main.usage_meter.tokens_this_month("meter-org") == 18 + estimated["prompt_tokens"] + estimated["completion_tokens"]


def test_counters_are_safe_under_concurrency(tmp_path) -> None:
    meter = UsageMeter(tmp_path, flush_seconds=3600)
    admitted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        ok, _ = meter.admit_chat("busy", 25)
        with lock:
            admitted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert admitted.count(True) == 25
    meter.flush()
    assert UsageMeter(tmp_path).usage("busy")["today"]["chat_requests"] == 25

    reservations = UploadReservations()
    granted = [
        reservations.reserve("t", 10, current_documents=0, current_bytes=0, max_documents=3, max_storage_bytes=None)[0]
        for _ in range(5)
    ]
    assert granted == [None, None, None, "max_documents", "max_documents"]
    reservations.release("t", 10)
    assert reservations.reserve("t", 10, current_documents=0, current_bytes=0, max_documents=3,
                                max_storage_bytes=None)[0] is None


# --------------------------------------------------------------------------- purge


def test_delete_purges_every_store_and_keeps_audit(two_tenants, monkeypatch) -> None:
    tenant_a, tenant_b = two_tenants
    fake = FakeMem0()
    monkeypatch.setattr(main.memory_service, "enabled", True)
    monkeypatch.setattr(main.memory_service, "memory", fake)

    document = upload(tenant_a, "purge.md", b"purge me please")
    job = upload(tenant_a, "purge-async.md", b"async purge", **{"async": "true"})
    keep = upload(tenant_b, "keep.md", b"keep me")
    ask(tenant_a, f"2032년 {uuid.uuid4().hex[:4]} 예산 정산 기준은?")
    gap_id = client.get("/v1/admin/gaps?status=all", headers=admin(tenant_a)).json()["data"][0]["id"]
    client.post("/v1/admin/ontology-packs", json={"gap_ids": [gap_id]}, headers=admin(tenant_a))
    client.post("/v1/tacit/entries", json={"question": "Q?", "answer": "A."}, headers=headers(tenant_a))
    client.post("/v1/feedback", json={"trace_id": "t", "rating": 1}, headers=headers(tenant_a))
    client.post("/v1/memories", json={"text": "remember me"}, headers=headers(tenant_a))
    client.post("/v1/memories", json={"text": "keep me"}, headers=headers(tenant_b))
    raw_path = main.documents.raw_path(main.documents.documents[document["id"]])
    assert raw_path and raw_path.exists()

    deleted = client.delete(f"/v1/platform/tenants/{tenant_a}", headers=PLATFORM)
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["complete"] is True and body["tenant"]["status"] == "deleting" and body["tenant"]["purged_at"]
    purge = body["purge"]
    assert purge["documents"] == 2 and purge["raw_files"] == 2
    assert purge["gaps"] >= 1 and purge["ontology_packs"] == 1 and purge["tacit_entries"] == 1
    assert purge["feedback"] == 1 and purge["memory_users"] == 1 and purge["jobs"] == 1

    assert document["id"] not in main.documents.documents and not raw_path.exists()
    assert job["job_id"] not in main.JOBS
    assert main.gap_store.list(tenant_a, None) == [] and main.pack_store.list(tenant_a) == []
    assert main.tacit_store.list(tenant_a) == [] and main.feedback_store.list(tenant_a) == []
    assert fake.deleted_users == [f"{tenant_a}::alice"]
    assert [row["user_id"] for row in fake.rows] == [f"{tenant_b}::alice"]
    # Stores were compacted on disk, not only in memory.
    assert tenant_a not in main.gap_store.path.read_text(encoding="utf-8")
    assert tenant_a not in main.tacit_store.path.read_text(encoding="utf-8")

    # The other tenant is untouched.
    assert client.get(f"/v1/documents/{keep['id']}", headers=admin(tenant_b)).status_code == 200
    # The deleted tenant is blocked and cannot be resumed.
    blocked = client.get("/v1/documents", headers=admin(tenant_a))
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "tenant_suspended"
    assert client.post(f"/v1/platform/tenants/{tenant_a}/resume", headers=PLATFORM).status_code == 409

    # Audit is retained and the purge itself is audited.
    events = client.get(f"/v1/platform/tenants/{tenant_a}/audit-events?limit=500", headers=PLATFORM).json()["data"]
    types = [event["type"] for event in events]
    assert "document.ingested" in types and "tenant.deleted" in types and "tenant.purged" in types
    purged_event = next(event for event in events if event["type"] == "tenant.purged")
    assert purged_event["summary"]["documents"] == 2 and purged_event["user_id"] == "operator"
    # Re-running the purge is idempotent.
    again = client.delete(f"/v1/platform/tenants/{tenant_a}", headers=PLATFORM).json()
    assert again["purge"]["documents"] == 0 and again["complete"] is True


def test_platform_audit_view_does_not_leak_other_tenants(two_tenants) -> None:
    tenant_a, tenant_b = two_tenants
    upload(tenant_b, "b-only.md", b"b only")
    events = client.get(f"/v1/platform/tenants/{tenant_a}/audit-events", headers=PLATFORM).json()["data"]
    assert events
    assert all(event["organization_id"] == tenant_a or event.get("tenant_id") == tenant_a for event in events)
    assert client.get("/v1/platform/tenants/no-such-tenant/audit-events", headers=PLATFORM).status_code == 404


# --------------------------------------------------------------------------- OIDC mapping


def oidc_config(**overrides) -> OIDCConfig:
    values = dict(mode="oidc", issuer="https://id.example", audience="baryon-ax-api")
    values.update(overrides)
    return OIDCConfig(**values)


def test_platform_admin_role_mapping() -> None:
    cfg = oidc_config()
    assert roles_from_claims({"groups": ["/baryon-platform-admin"]}, cfg) == {PLATFORM_ADMIN}
    assert roles_from_claims({"groups": ["baryon-admin"]}, cfg) == {"admin"}
    custom = oidc_config(platform_admin_role="ops-root")
    assert roles_from_claims({"groups": ["ops-root", "baryon-member"]}, custom) == {PLATFORM_ADMIN, "member"}
    # A service account can never delegate platform administration.
    with pytest.raises(Exception):
        delegated_roles(PLATFORM_ADMIN, {PLATFORM_ADMIN}, cfg)
    assert delegated_roles(f"{PLATFORM_ADMIN},member", {PLATFORM_ADMIN, "member"}, cfg) == {"member"}


def test_organization_claim_shapes() -> None:
    cfg = oidc_config()
    assert principal_from_claims({"organization": {"acme": {"id": "1"}}}, cfg).organization_id == "acme"
    assert principal_from_claims({"organization": ["acme", "beta"]}, cfg).organization_id == "acme"
    assert principal_from_claims({"organization": ["acme", "beta"]}, cfg, "beta").organization_id == "beta"
    # A header can only pick an organization the signed token already lists.
    assert principal_from_claims({"organization": ["acme"]}, cfg, "victim").organization_id == "acme"


def test_pooled_oidc_requires_an_organization_claim() -> None:
    class Validator:
        def validate(self, token: str) -> dict:
            if token == "no-org":
                return {"preferred_username": "alice", "groups": ["baryon-member"]}
            if token == "two-orgs":
                return {"preferred_username": "alice", "organization": ["acme", "beta"]}
            raise AuthError("bad")

    app = FastAPI()

    @app.get("/who")
    def who(x_organization_id: str | None = Header(None)) -> dict:
        return {"org": x_organization_id}

    failures: list = []
    app.add_middleware(OIDCAuthMiddleware, config=oidc_config(require_org_claim=True), validator=Validator(),
                       on_failure=failures.append)
    test_client = TestClient(app)
    rejected = test_client.get("/who", headers={"Authorization": "Bearer no-org", "X-Organization-Id": "acme"})
    assert rejected.status_code == 403
    assert rejected.json()["detail"]["code"] == "organization_claim_missing"
    assert failures and failures[0]["status"] == 403
    picked = test_client.get("/who", headers={"Authorization": "Bearer two-orgs", "X-Organization-Id": "beta"})
    assert picked.json() == {"org": "beta"}
    forged = test_client.get("/who", headers={"Authorization": "Bearer two-orgs", "X-Organization-Id": "victim"})
    assert forged.json() == {"org": "acme"}

    lenient = FastAPI()
    lenient.get("/who")(who)
    lenient.add_middleware(OIDCAuthMiddleware, config=oidc_config(), validator=Validator())
    # Single mode keeps the historical default-organization fallback.
    assert TestClient(lenient).get("/who", headers={"Authorization": "Bearer no-org"}).json() == {"org": "local-org"}
