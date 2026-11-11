import asyncio
import json
import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="baryon-ax-test-"))
os.environ["DOCUMENT_STORE"] = str(TEST_ROOT / "documents")
os.environ["AUDIT_LOG"] = str(TEST_ROOT / "audit" / "events.jsonl")
os.environ["FASTEMBED_CACHE_PATH"] = str(TEST_ROOT / "fastembed")
os.environ["MEM0_HISTORY_DB"] = str(TEST_ROOT / "mem0" / "history.db")
os.environ["SYSTEM_METRICS_ENABLED"] = "false"

from fastapi.testclient import TestClient

from baryon.main import AuditSink, ChatRequest, DocumentService, Message, ModelAdapter, PolicyEngine, Principal, app

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
    trace = response.json()["trace"]
    steps = [item["step"] for item in trace["steps"]]
    assert steps == ["rate_limit", "policy", "memory_search", "document_search", "model", "audit"]
    assert trace["memory_count"] == 0
    assert trace["retrieval_count"] >= 1
    assert trace["total_ms"] > 0


def test_document_detail_returns_cited_chunk_and_original_file() -> None:
    body = b"# Handbook\n\nQdrant stores vectors for grounded retrieval."
    uploaded = client.post("/v1/documents", files={"file": ("handbook.md", body)}, headers=headers(user="detail-user", roles="member")).json()
    detail = client.get(f"/v1/documents/{uploaded['id']}?chunk=1", headers=headers(user="detail-user", roles="member"))
    assert detail.status_code == 200
    data = detail.json()
    assert "Qdrant stores vectors" in data["cited_chunk"]["content"]
    assert "Handbook" in data["content"]
    assert data["has_raw_file"] is True
    original = client.get(f"/v1/documents/{uploaded['id']}/file", headers=headers(user="detail-user", roles="member"))
    assert original.status_code == 200
    assert original.content == body
    assert "handbook.md" in original.headers["content-disposition"]


def test_document_detail_hides_documents_outside_the_caller_scope() -> None:
    uploaded = client.post("/v1/documents", files={"file": ("secret.md", b"secret contents")},
                           data={"allowed_roles": "legal"}, headers=headers(user="owner", roles="legal")).json()
    detail = client.get(f"/v1/documents/{uploaded['id']}", headers=headers(user="other", roles="member"))
    assert detail.status_code == 404
    original = client.get(f"/v1/documents/{uploaded['id']}/file", headers=headers(user="other", roles="member"))
    assert original.status_code == 404


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


def test_admin_settings_are_admin_only() -> None:
    assert client.get("/v1/admin/settings", headers=headers(roles="member")).status_code == 403
    settings = client.get("/v1/admin/settings", headers=headers(user="admin", roles="admin")).json()
    assert settings["version"] == "0.6.0"
    assert settings["model"]["adapter"] in {"demo-local", "openai-compatible"}
    assert settings["policy"] == {"blocked_patterns": 1, "pii_masking": True}
    assert settings["retrieval"] == {"mode": "lexical", "result_limit": 4}
    assert isinstance(settings["counts"]["documents"], int)


def test_admin_settings_include_system_metrics() -> None:
    data = client.get("/v1/admin/settings", headers=headers(user="admin", roles="admin")).json()
    system = data["system"]
    assert system["process"]["total"] >= 1
    assert system["memory"]["total_bytes"] > 0
    assert system["storage"]["total_bytes"] > 0
    assert system["containers"]["available"] is False
    assert data["links"]["langgraph"].startswith("https://")


def test_admin_can_update_document_acl() -> None:
    org = "org-admin-acl"
    upload = client.post("/v1/documents", files={"file": ("acl.md", b"acl update content")},
                         headers=headers(user="owner", organization=org))
    document_id = upload.json()["id"]

    def patch(payload: dict, request_headers: dict[str, str]):
        return client.patch(f"/v1/documents/{document_id}", json=payload, headers=request_headers)

    assert patch({"allowed_roles": ["legal"]}, headers(user="owner", organization=org)).status_code == 403
    assert patch({"allowed_roles": ["legal"]}, headers(user="admin", organization="org-admin-other", roles="admin")).status_code == 404
    assert patch({"allowed_roles": ["legal"]}, headers(user="admin", organization=org, roles="admin")).json()["allowed_roles"] == ["legal"]
    assert all(item["id"] != document_id for item in client.get("/v1/documents", headers=headers(user="member", organization=org)).json()["data"])
    legal_documents = client.get("/v1/documents", headers=headers(user="legal", organization=org, roles="legal")).json()["data"]
    assert any(item["id"] == document_id for item in legal_documents)

    events = client.get("/v1/audit-events?limit=20", headers=headers(user="admin", organization=org, roles="admin")).json()["data"]
    assert any(event["type"] == "document.acl_updated" and event["document_id"] == document_id for event in events)

    assert patch({"allowed_roles": []}, headers(user="admin", organization=org, roles="admin")).status_code == 200
    assert client.get("/v1/documents/search", params={"q": "acl update content"},
                      headers=headers(user="legal", organization=org, roles="legal")).json()["data"] == []


def test_patch_unknown_document_returns_not_found() -> None:
    response = client.patch("/v1/documents/missing", json={"allowed_roles": ["member"]},
                            headers=headers(user="admin", roles="admin"))
    assert response.status_code == 404


def test_memory_endpoints_are_disabled_without_mem0() -> None:
    assert client.post("/v1/memories", json={"text": "remember this"}, headers=headers()).status_code == 503
    assert client.get("/v1/memories", headers=headers()).status_code == 503
    assert client.get("/v1/memories/search", params={"q": "remember"}, headers=headers()).status_code == 503


def test_system_prompt_enforces_korean_and_grounding(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROMPT_PROFILE", "auto")
    adapter = ModelAdapter()
    request = ChatRequest(messages=[Message(role="user", content="우리 조직은?")], locale="ko")
    prompt = adapter.system_prompt(request, Principal("user", "local-org", {"member"}), [])
    assert "한국어로만" in prompt
    assert "지어내지 마세요" in prompt
    assert "제공된 문서에서" in prompt
    assert "local-org" in prompt
    assert "없음" in prompt


def test_qwen35_prompt_profile_is_selected(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROMPT_PROFILE", "qwen35")
    adapter = ModelAdapter()
    request = ChatRequest(messages=[Message(role="user", content="hello")], locale="ko")
    prompt = adapter.system_prompt(request, None, [])
    assert "사고 과정" in prompt


def test_model_adapter_can_disable_qwen_thinking(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_DISABLE_THINKING", "true")
    monkeypatch.delenv("MODEL_CHAT_TEMPLATE_KWARGS", raising=False)
    adapter = ModelAdapter()
    request = ChatRequest(messages=[Message(role="user", content="hello")])
    payload = adapter.payload(request, [], False)
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_model_adapter_injects_user_memories() -> None:
    adapter = ModelAdapter()
    request = ChatRequest(messages=[Message(role="user", content="내가 좋아하는 음료는?")])
    payload = adapter.payload(request, [], False, principal=Principal(user_id="u1", organization_id="o1", roles={"member"}),
                              memories=[{"memory": "사용자는 아메리카노를 좋아한다"}])
    system = payload["messages"][0]["content"]
    assert "사용자 기억" in system
    assert "아메리카노" in system


def test_complete_sends_user_memories_to_the_model(monkeypatch) -> None:
    import baryon.main as app_module

    captured: dict = {}

    class StubResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    class StubClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return StubResponse()

    monkeypatch.setattr(app_module.httpx, "AsyncClient", StubClient)
    monkeypatch.setenv("MODEL_BASE_URL", "http://model.test/v1")
    adapter = ModelAdapter()
    request = ChatRequest(messages=[Message(role="user", content="내 커피는?")])
    answer = asyncio.run(adapter.complete(request, [], memories=[{"memory": "아메리카노를 좋아한다"}]))
    assert answer == "ok"
    assert "아메리카노를 좋아한다" in captured["json"]["messages"][0]["content"]


def test_streaming_chat_returns_tokens_and_citations() -> None:
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "stream check"}], "stream": True},
                           headers=headers(organization="stream-org"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    trace_id = response.headers["x-trace-id"]
    frames = [frame for frame in response.text.split("\n\n") if frame]
    assert frames[-1] == "data: [DONE]"
    payloads = [json.loads(frame.removeprefix("data: ")) for frame in frames[:-1]]
    assert any(item["type"] == "token" and item["content"] for item in payloads)
    citations = [item for item in payloads if item["type"] == "citations"]
    assert len(citations) == 1 and citations[0]["trace_id"] == trace_id
    traces = [item for item in payloads if item["type"] == "trace"]
    assert len(traces) == 2
    assert traces[0]["data"]["steps"][0]["step"] == "rate_limit"
    assert traces[-1]["data"]["steps"][-1]["step"] == "audit"
    assert traces[-1]["data"]["total_ms"] > 0
    assert all("content" not in item for item in citations[0]["data"])


def test_citations_do_not_expose_document_content() -> None:
    org = "org-citation"
    upload = client.post("/v1/documents", files={"file": ("contact.md", b"Reach me at 010-9876-5432.")}, headers=headers(organization=org))
    assert upload.status_code == 200
    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Reach me"}], "stream": False},
                           headers=headers(organization=org))
    assert response.status_code == 200
    assert "010-9876-5432" not in response.text
    assert "[REDACTED-PHONE]" in response.text
    assert all("content" not in citation for citation in response.json()["citations"])


def test_pii_masking_does_not_redact_timestamps() -> None:
    engine = PolicyEngine()
    assert engine.mask_output("stack-1794334691.md") == "stack-1794334691.md"
    assert engine.mask_output("연락처는 010-1234-5678입니다.") == "연락처는 [REDACTED-PHONE]입니다."


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
