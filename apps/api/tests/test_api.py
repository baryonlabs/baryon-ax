import json
import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="baryon-ax-test-"))
os.environ["DOCUMENT_STORE"] = str(TEST_ROOT / "documents")
os.environ["AUDIT_LOG"] = str(TEST_ROOT / "audit" / "events.jsonl")

from fastapi.testclient import TestClient

from baryon.main import AuditSink, DocumentService, PolicyEngine, Principal, app

client = TestClient(app)


def headers(user: str = "local-user", organization: str = "local-org", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def test_health_and_models() -> None:
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["version"] == "0.6.0"
    assert client.get("/v1/models").json()["data"]


def test_role_scoped_retrieval_and_completion() -> None:
    uploaded = client.post("/v1/documents", files={"file": ("handbook.md", b"Baryon deployment uses Qdrant for vector search.")}, headers=headers(roles="member"))
    assert uploaded.status_code == 200
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "What is used for vector search?"}], "stream": False})
    assert response.status_code == 200
    assert response.json()["citations"]


def test_policy_blocks_configured_content() -> None:
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "how to make a bomb"}], "stream": False})
    assert response.status_code == 422


def test_document_store_survives_service_restart(tmp_path) -> None:
    store = DocumentService(tmp_path)
    document = store.create(
        principal=Principal(user_id="user-1", organization_id="org-1", roles={"member"}),
        title="durable.md", content="Qdrant is retained after restart.", allowed_roles={"member"},
    )
    restored = DocumentService(tmp_path)
    assert restored.documents[document.id].title == "durable.md"


def test_document_list_and_owner_delete() -> None:
    upload = client.post("/v1/documents", files={"file": ("owned.md", b"owner can manage this document")}, headers=headers(user="owner"))
    document_id = upload.json()["id"]
    listed = client.get("/v1/documents", headers=headers(user="owner"))
    assert any(item["id"] == document_id for item in listed.json()["data"])
    assert client.delete(f"/v1/documents/{document_id}", headers=headers(user="owner")).status_code == 200


def test_owner_can_view_and_retrieve_without_matching_role(tmp_path) -> None:
    store = DocumentService(tmp_path)
    owner = Principal("owner", "org-acl", {"viewer"})
    stranger = Principal("stranger", "org-acl", {"member"})
    document = store.create(owner, "owned.md", "legal-only content", {"legal"})
    assert [item["id"] for item in store.list_for(owner)] == [document.id]
    assert store.list_for(stranger) == []
    assert store.search(owner, "legal-only content")
    assert store.search(stranger, "legal-only content") == []


def test_korean_particles_do_not_block_retrieval(tmp_path) -> None:
    store = DocumentService(tmp_path)
    principal = Principal("user", "org-korean", {"member"})
    store.create(principal, "korean.md", "Baryon AX는 Qdrant와 LangGraph를 사용합니다.", {"member"})
    results = store.search(principal, "Qdrant는 무엇에 사용하나요?")
    assert results and results[0]["title"] == "korean.md"


def test_document_without_roles_is_private_to_owner(tmp_path) -> None:
    store = DocumentService(tmp_path)
    owner = Principal("owner", "org-private", {"member"})
    colleague = Principal("colleague", "org-private", {"member"})
    document = store.create(owner, "private.md", "restricted content", set())
    assert store.list_for(colleague) == []
    assert store.search(colleague, "restricted content") == []
    assert [item["id"] for item in store.list_for(owner)] == [document.id]


def test_retrieval_never_crosses_organizations(tmp_path) -> None:
    store = DocumentService(tmp_path)
    document = store.create(Principal("owner", "org-a", {"member"}), "a.md", "organization alpha content", {"member"})
    admin_b = Principal("admin", "org-b", {"admin"})
    assert store.list_for(admin_b) == []
    assert store.search(admin_b, "organization alpha content") == []
    assert store.delete(admin_b, document.id) is False


def test_upload_rejects_non_utf8() -> None:
    response = client.post("/v1/documents", files={"file": ("broken.md", b"\xff\xfe\x00")}, headers=headers())
    assert response.status_code == 415


def test_role_restricted_document_is_not_visible_to_other_roles() -> None:
    owner, colleague = "private-owner", "private-colleague"
    upload = client.post("/v1/documents", data={"allowed_roles": "legal"}, files={"file": ("secret.md", b"private memo")},
                         headers=headers(user=owner, organization="org-api-private"))
    assert upload.status_code == 200
    assert client.get("/v1/documents", headers=headers(user=colleague, organization="org-api-private")).json()["data"] == []
    assert client.get("/v1/documents/search", params={"q": "private memo"},
                      headers=headers(user=colleague, organization="org-api-private")).json()["data"] == []
    assert client.get("/v1/documents", headers=headers(user=owner, organization="org-api-private")).json()["data"]


def test_audit_sink_persists_and_filters_by_organization(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    principal = Principal("admin", "org-a", {"admin"})
    sink = AuditSink(path)
    sink.emit("chat.started", principal, "trace-1", model="demo-local")
    restored = AuditSink(path)
    assert len(restored.events) == 1
    assert restored.recent_for(principal)[0]["trace_id"] == "trace-1"
    assert restored.recent_for(Principal("admin", "org-b", {"admin"})) == []


def test_audit_endpoint_is_admin_only_and_organization_scoped() -> None:
    org_a, org_b = "audit-org-a", "audit-org-b"
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "audit scope check"}], "stream": False},
                           headers=headers(user="user-a", organization=org_a))
    assert response.status_code == 200

    assert client.get("/v1/audit-events", headers=headers(user="user-a", organization=org_a)).status_code == 403
    events = client.get("/v1/audit-events", headers=headers(user="admin-a", organization=org_a, roles="admin")).json()["data"]
    assert events and all(event["organization_id"] == org_a for event in events)
    assert client.get("/v1/audit-events", headers=headers(user="admin-b", organization=org_b, roles="admin")).json()["data"] == []


def test_streaming_chat_returns_tokens_and_citations() -> None:
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "stream check"}], "stream": True},
                           headers=headers(organization="stream-org"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame]
    assert frames[-1] == "data: [DONE]"
    payloads = [json.loads(frame.removeprefix("data: ")) for frame in frames[:-1]]
    assert any(item["type"] == "token" and item["content"] for item in payloads)
    citations = [item for item in payloads if item["type"] == "citations"]
    assert len(citations) == 1 and citations[0]["trace_id"]


def test_pii_masking_spans_stream_chunks() -> None:
    engine = PolicyEngine()
    text = "call 010-1234-5678 now"
    emitted, buffer = "", ""
    for character in text:
        safe, buffer = engine.mask_stream(buffer + character)
        emitted += safe
    safe, buffer = engine.mask_stream(buffer, final=True)
    emitted += safe
    assert buffer == ""
    assert emitted == "call [REDACTED-PHONE] now"
