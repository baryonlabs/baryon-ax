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
    lexical_doc = store.create(principal, "schedule.md", "사업기간은 계약체결일로부터 180일 이내", {"member"})
    semantic_doc = store.create(principal, "plan.md", "프로젝트 일정 개요", {"member"})
    vector.hits = [{"document_id": semantic_doc.id, "title": "plan.md", "chunk": 1, "content": "프로젝트 일정 개요", "score": 0.8}]
    return store, vector, principal, lexical_doc, semantic_doc


def test_default_mode_returns_vector_hits_only(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RETRIEVAL_MODE", raising=False)
    store, vector, principal, _, semantic_doc = _store(tmp_path)
    results = store.search(principal, "사업기간", limit=4)
    assert [item["document_id"] for item in results] == [semantic_doc.id]
    assert vector.requested_limit == 4


def test_hybrid_mode_adds_lexical_hits(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    store, vector, principal, lexical_doc, semantic_doc = _store(tmp_path)
    results = store.search(principal, "사업기간", limit=4)
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
