"""RETRIEVAL_MODE=hybrid fuses Qdrant hits with lexical hits (reciprocal rank fusion)."""

from __future__ import annotations

from baryon.main import DocumentService, Principal


class FakeVector:
    """Stands in for VectorIndex: returns a fixed ranking for every query."""

    enabled = True

    def __init__(self) -> None:
        self.hits: list[dict] = []
        self.requested_limit = 0

    def index_document(self, document) -> None:
        pass

    def delete_document(self, document_id: str) -> None:
        pass

    def search(self, principal, query: str, limit: int):
        self.requested_limit = limit
        return self.hits[:limit]


def _store(tmp_path):
    vector = FakeVector()
    store = DocumentService(tmp_path, vector=vector)
    principal = Principal("user", "org-hybrid", {"member"})
    lexical_doc = store.create(principal, "leave-policy.md", "연차휴가는 입사 1년 후 15일을 부여한다", {"member"})
    semantic_doc = store.create(principal, "benefits.md", "복리후생 제도 개요", {"member"})
    vector.hits = [{"document_id": semantic_doc.id, "title": "benefits.md", "chunk": 1, "content": "복리후생 제도 개요", "score": 0.8}]
    return store, vector, principal, lexical_doc, semantic_doc


def test_vector_mode_returns_vector_hits_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", "vector")
    store, vector, principal, _, semantic_doc = _store(tmp_path)
    results = store.search(principal, "연차휴가", limit=4)
    assert [item["document_id"] for item in results] == [semantic_doc.id]
    assert vector.requested_limit == 4


def test_default_mode_is_hybrid(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RETRIEVAL_MODE", raising=False)
    store, vector, principal, lexical_doc, semantic_doc = _store(tmp_path)
    ids = {item["document_id"] for item in store.search(principal, "연차휴가", limit=4)}
    assert ids == {lexical_doc.id, semantic_doc.id}


def test_lexical_mode_never_calls_the_vector_index(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", "lexical")
    store, vector, principal, lexical_doc, _ = _store(tmp_path)
    results = store.search(principal, "연차휴가", limit=4)
    assert [item["document_id"] for item in results] == [lexical_doc.id]
    assert vector.requested_limit == 0


def test_explicit_mode_overrides_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", "lexical")
    store, _, principal, _, semantic_doc = _store(tmp_path)
    results = store.search(principal, "연차휴가", limit=4, mode="vector")
    assert [item["document_id"] for item in results] == [semantic_doc.id]


def test_unknown_mode_falls_back_to_hybrid(monkeypatch) -> None:
    from baryon.main import retrieval_mode

    monkeypatch.setenv("RETRIEVAL_MODE", "semantic-ish")
    assert retrieval_mode() == "hybrid"


def test_candidates_setting_widens_each_retriever(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("RETRIEVAL_CANDIDATES", "12")
    store, vector, principal, _, _ = _store(tmp_path)
    store.search(principal, "연차휴가", limit=4)
    assert vector.requested_limit == 12


def test_hybrid_mode_adds_lexical_hits(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    store, vector, principal, lexical_doc, semantic_doc = _store(tmp_path)
    results = store.search(principal, "연차휴가", limit=4)
    ids = {item["document_id"] for item in results}
    assert ids == {lexical_doc.id, semantic_doc.id}
    assert vector.requested_limit == 8


def test_fuse_ranks_shared_hits_first_and_dedupes() -> None:
    a = {"document_id": "a", "chunk": 1, "title": "a"}
    b = {"document_id": "b", "chunk": 1, "title": "b"}
    c = {"document_id": "c", "chunk": 2, "title": "c"}
    fused = DocumentService._fuse([a, b], [c, b], limit=2)
    assert [item["document_id"] for item in fused] == ["b", "a"]
    assert len(DocumentService._fuse([a, b], [b, c], limit=10)) == 3


def test_fuse_weights_favour_one_retriever() -> None:
    a = {"document_id": "a", "chunk": 1, "title": "a"}
    b = {"document_id": "b", "chunk": 1, "title": "b"}
    assert DocumentService._fuse([a], [b], limit=2, weights=(1.0, 2.0))[0]["document_id"] == "b"
    assert DocumentService._fuse([a], [b], limit=2, weights=(2.0, 1.0))[0]["document_id"] == "a"


def test_vector_index_rejects_dimension_mismatch(monkeypatch) -> None:
    import qdrant_client
    from types import SimpleNamespace

    from baryon.main import VectorIndex

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def get_collections(self):
            return []

        def collection_exists(self, _name):
            return True

        def get_collection(self, _name):
            return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=384))))

        def create_payload_index(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(qdrant_client, "QdrantClient", FakeClient)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    index = VectorIndex()
    assert index.enabled is False
    assert "384-dim" in index.last_error
    monkeypatch.setenv("EMBEDDING_DIMENSION", "384")
    assert VectorIndex().enabled is True


def test_openai_compatible_embeddings_use_query_model_and_batches(monkeypatch) -> None:
    import httpx

    import baryon.main as main_module
    from baryon.main import VectorIndex

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        calls.append({"url": str(request.url), "model": body["model"], "size": len(body["input"]), "longest": max(map(len, body["input"])),
                      "auth": request.headers.get("authorization")})
        data = [{"index": i, "embedding": [float(i)] * 4} for i in reversed(range(len(body["input"])))]
        return httpx.Response(200, json={"object": "list", "data": data})

    real_client = httpx.Client
    monkeypatch.setattr(main_module.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://embed.test/v1/")
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-passage")
    monkeypatch.setenv("EMBEDDING_QUERY_MODEL", "embed-query")
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "2")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "4")
    index = VectorIndex()

    vectors = index._embed(["a", "b", "c"])
    assert vectors == [[0.0] * 4, [1.0] * 4, [0.0] * 4]
    assert [(c["model"], c["size"]) for c in calls] == [("embed-passage", 2), ("embed-passage", 1)]
    assert calls[0]["url"] == "http://embed.test/v1/embeddings" and calls[0]["auth"] == "Bearer test-key"

    index._embed(["질문"], query=True)
    assert calls[-1]["model"] == "embed-query"

    monkeypatch.setenv("EMBEDDING_DIMENSION", "1024")
    try:
        VectorIndex()._embed(["a"])
    except RuntimeError as error:
        assert "4-dim" in str(error)
    else:
        raise AssertionError("dimension mismatch should raise")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "4")
    monkeypatch.setenv("EMBEDDING_MAX_CHARS", "100")
    VectorIndex()._embed(["x" * 500, ""])
    assert calls[-1]["longest"] == 100
