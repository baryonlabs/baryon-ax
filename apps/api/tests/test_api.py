from fastapi.testclient import TestClient

from baryon.main import DocumentService, Principal, app


client = TestClient(app)


def test_health_and_models() -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/v1/models").json()["data"]


def test_role_scoped_retrieval_and_completion() -> None:
    uploaded = client.post("/v1/documents", files={"file": ("handbook.md", b"Baryon deployment uses Qdrant for vector search.")}, headers={"X-Roles": "member"})
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
    upload = client.post("/v1/documents", files={"file": ("owned.md", b"owner can manage this document")}, headers={"X-User-Id": "owner"})
    document_id = upload.json()["id"]
    listed = client.get("/v1/documents", headers={"X-User-Id": "owner"})
    assert any(item["id"] == document_id for item in listed.json()["data"])
    assert client.delete(f"/v1/documents/{document_id}", headers={"X-User-Id": "owner"}).status_code == 200
