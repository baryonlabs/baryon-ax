"""Qdrant-backed retrieval end to end: a real VectorIndex on in-process Qdrant (QDRANT_URL=:memory:).

Covers organization isolation, role ACLs (including role changes after indexing), deletion, tenant
purge, re-chunking, embedder failure fallback, the reindex migration CLI, and the HTTP surface.
"""

from __future__ import annotations

import hashlib
import io
import math
import uuid

import pytest

from baryon.main import DocumentService, Principal, VectorIndex
from baryon.reindex import run as reindex

DIMENSION = 64


def bigram_embed(texts: list[str], query: bool) -> list[list[float]]:
    """Deterministic character-bigram hashing: similar strings get similar vectors."""
    vectors = []
    for text in texts:
        vector = [0.0] * DIMENSION
        compact = "".join(text.split())
        for index in range(len(compact) - 1):
            bucket = int(hashlib.md5(compact[index:index + 2].encode()).hexdigest(), 16) % DIMENSION
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        vectors.append([value / norm for value in vector])
    return vectors


@pytest.fixture()
def index(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSION", str(DIMENSION))
    monkeypatch.setenv("RETRIEVAL_MIN_SCORE", "0.05")
    vector = VectorIndex(url=":memory:", collection=f"test_{uuid.uuid4().hex[:8]}", embed=bigram_embed)
    assert vector.enabled, vector.last_error
    return vector


@pytest.fixture()
def store(tmp_path, index):
    return DocumentService(tmp_path, vector=index)


ALICE_A = Principal("alice", "org-a", {"member"})
ALICE_B = Principal("alice", "org-b", {"member"})


def ids(hits: list[dict]) -> set[str]:
    return {hit["document_id"] for hit in hits}


def test_vector_hits_stay_inside_the_organization(store) -> None:
    doc_a = store.create(ALICE_A, "a.md", "재택근무는 주 2회까지 허용된다", {"member"})
    doc_b = store.create(ALICE_B, "b.md", "재택근무는 주 2회까지 허용된다", {"member"})
    assert ids(store.vector.search(ALICE_A, "재택근무 허용", 10)) == {doc_a.id}
    assert ids(store.search(ALICE_B, "재택근무 허용", mode="vector")) == {doc_b.id}
    assert ids(store.search(ALICE_B, "재택근무 허용", mode="hybrid")) == {doc_b.id}


def test_role_acl_applies_to_vector_hits(store) -> None:
    owner = Principal("hr-lead", "org-a", {"hr"})
    secret = store.create(owner, "salary.md", "연봉 테이블은 인사팀만 본다", {"hr"})
    query = "연봉 테이블"
    assert store.search(ALICE_A, query, mode="vector") == []
    assert store.search(ALICE_A, query, mode="hybrid") == []
    assert ids(store.search(Principal("other-hr", "org-a", {"hr"}), query, mode="vector")) == {secret.id}
    assert ids(store.search(owner, query, mode="vector")) == {secret.id}
    assert ids(store.search(Principal("boss", "org-a", {"admin"}), query, mode="vector")) == {secret.id}


def test_role_change_reaches_the_index(store) -> None:
    admin = Principal("boss", "org-a", {"admin"})
    document = store.create(admin, "memo.md", "주차 정기권 신청 방법", {"admin"})
    assert store.vector.search(ALICE_A, "주차 정기권", 10) == []
    store.update_roles(admin, document.id, {"member"})
    assert ids(store.vector.search(ALICE_A, "주차 정기권", 10)) == {document.id}


def test_delete_and_purge_remove_points(store) -> None:
    kept = store.create(ALICE_A, "keep.md", "회의실 예약 규칙", {"member"})
    doomed = store.create(ALICE_A, "drop.md", "법인카드 한도", {"member"})
    other = store.create(ALICE_B, "other.md", "법인카드 한도", {"member"})
    assert store.vector.count() == 3
    store.delete(ALICE_A, doomed.id)
    assert store.vector.count() == 2
    store.purge_organization("org-a")
    assert store.vector.count() == 1
    assert ids(store.vector.search(ALICE_B, "법인카드", 10)) == {other.id}
    assert kept.id not in store.vector.indexed_document_ids()


def test_reindex_drops_chunks_a_shorter_version_no_longer_has(store) -> None:
    document = store.create(ALICE_A, "long.md", "x", {"member"},
                            chunks=[{"text": "첫째 조각"}, {"text": "둘째 조각"}, {"text": "셋째 조각"}])
    assert store.vector.count() == 3
    document.chunks = [{"text": "하나로 합친 조각"}]
    assert store.vector.index_document(document) is True
    assert store.vector.count() == 1


def test_embedder_failure_falls_back_to_lexical(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_DIMENSION", str(DIMENSION))

    def broken(texts, query):
        raise RuntimeError("embedding model unavailable")

    vector = VectorIndex(url=":memory:", collection="broken", embed=broken)
    store = DocumentService(tmp_path, vector=vector)
    document = store.create(ALICE_A, "leave.md", "연차휴가는 15일이다", {"member"})
    assert vector.search(ALICE_A, "연차휴가", 4) is None
    assert ids(store.search(ALICE_A, "연차휴가", mode="hybrid")) == {document.id}
    assert ids(store.search(ALICE_A, "연차휴가", mode="vector")) == {document.id}


def test_unreachable_qdrant_disables_vector_search(tmp_path) -> None:
    vector = VectorIndex(url="http://127.0.0.1:9", collection="nowhere")
    assert not vector.enabled and vector.last_error
    store = DocumentService(tmp_path, vector=vector)
    document = store.create(ALICE_A, "leave.md", "연차휴가는 15일이다", {"member"})
    assert ids(store.search(ALICE_A, "연차휴가")) == {document.id}


def test_reindex_cli_migrates_a_lexical_only_store(tmp_path, index) -> None:
    # Documents written while Qdrant was off (the lexical-only deployment) ...
    lexical_only = DocumentService(tmp_path)
    first = lexical_only.create(ALICE_A, "a.md", "재택근무 규정", {"member"})
    lexical_only.create(ALICE_B, "b.md", "출장비 규정", {"member"})
    # ... are picked up after restart with Qdrant on.
    migrated = DocumentService(tmp_path, vector=index)
    out = io.StringIO()
    assert reindex(["--dry-run"], documents=migrated, vector=index, out=out) == 0
    assert index.count() == 0 and "documents=2" in out.getvalue()

    assert reindex(["--organization", "org-a"], documents=migrated, vector=index, out=io.StringIO()) == 0
    assert index.indexed_document_ids() == {first.id}
    assert reindex([], documents=migrated, vector=index, out=io.StringIO()) == 0
    assert index.count() == 2

    # A document deleted behind the index's back is pruned.
    migrated.documents.pop(first.id)
    out = io.StringIO()
    assert reindex(["--prune"], documents=migrated, vector=index, out=out) == 0
    assert "pruned=1" in out.getvalue() and index.count() == 1

    assert reindex(["--recreate"], documents=migrated, vector=index, out=io.StringIO()) == 0
    assert index.count() == 1


def test_reindex_cli_refuses_without_qdrant(tmp_path) -> None:
    out = io.StringIO()
    assert reindex([], documents=DocumentService(tmp_path), vector=VectorIndex(url=""), out=out) == 2
    assert "not enabled" in out.getvalue()


def test_http_search_uses_qdrant_and_keeps_tenant_headers(monkeypatch, index) -> None:
    from fastapi.testclient import TestClient

    import baryon.main as main

    monkeypatch.setattr(main.documents, "vector", index)
    monkeypatch.setattr(main, "vector", index)
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    client = TestClient(main.app)
    org = f"org-{uuid.uuid4().hex[:8]}"
    member = {"X-User-Id": "writer", "X-Organization-Id": org, "X-Roles": "member"}
    uploaded = client.post("/v1/documents", files={"file": ("policy.md", "동호회 활동비는 월 2만 원".encode())},
                           data={"allowed_roles": "member"}, headers=member)
    assert uploaded.status_code == 200, uploaded.text
    document_id = uploaded.json()["id"]
    assert document_id in index.indexed_document_ids()

    hits = client.get("/v1/documents/search", params={"q": "동호회 활동비"}, headers=member).json()["data"]
    assert document_id in ids(hits)
    stranger = {**member, "X-Organization-Id": f"other-{org}"}
    assert client.get("/v1/documents/search", params={"q": "동호회 활동비"}, headers=stranger).json()["data"] == []
    guest = {**member, "X-User-Id": "guest", "X-Roles": "guest"}
    assert client.get("/v1/documents/search", params={"q": "동호회 활동비"}, headers=guest).json()["data"] == []

    health = client.get("/health").json()
    assert health["vector_backend"] == "qdrant" and health["retrieval_mode"] == "hybrid"


def test_vector_index_reconnects_when_qdrant_comes_up_later(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_DIMENSION", str(DIMENSION))
    monkeypatch.setenv("RETRIEVAL_MIN_SCORE", "0.05")
    monkeypatch.setenv("QDRANT_RETRY_SECONDS", "0")
    vector = VectorIndex(url="http://127.0.0.1:9", collection="late", embed=bigram_embed)
    assert not vector.enabled
    vector.url = ":memory:"  # Qdrant is now reachable
    store = DocumentService(tmp_path, vector=vector)
    document = store.create(ALICE_A, "late.md", "회의실 예약 규칙", {"member"})
    assert vector.enabled and vector.count() == 1
    assert ids(vector.search(ALICE_A, "회의실 예약", 4)) == {document.id}


def test_auto_backfill_fills_only_an_empty_collection(tmp_path, index) -> None:
    import asyncio

    from baryon.main import backfill_empty_index

    DocumentService(tmp_path).create(ALICE_A, "a.md", "재택근무 규정", {"member"})
    store = DocumentService(tmp_path, vector=index)
    assert asyncio.run(backfill_empty_index(store, wait_seconds=0)) == 1
    assert index.count() == 1
    assert asyncio.run(backfill_empty_index(store, wait_seconds=0)) == 0


def test_auto_backfill_gives_up_when_qdrant_never_starts(tmp_path, monkeypatch) -> None:
    import asyncio

    from baryon.main import backfill_empty_index

    monkeypatch.setenv("QDRANT_RETRY_SECONDS", "0")
    store = DocumentService(tmp_path, vector=VectorIndex(url="http://127.0.0.1:9", collection="never"))
    assert asyncio.run(backfill_empty_index(store, wait_seconds=0.2, poll=0.05)) == 0
