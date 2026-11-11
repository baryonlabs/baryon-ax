"""Korean Unicode normalization (NFD documents vs NFC queries)."""

from __future__ import annotations

import os
import tempfile
import unicodedata
from pathlib import Path

os.environ.setdefault("DOCUMENT_STORE", str(Path(tempfile.mkdtemp(prefix="baryon-ax-nfd-")) / "documents"))
os.environ.setdefault("AUDIT_LOG", str(Path(tempfile.mkdtemp(prefix="baryon-ax-nfd-audit-")) / "events.jsonl"))

from fastapi.testclient import TestClient

from baryon.ingest import Acl, chunk_document, normalize_text, parse_text
from baryon.main import DocumentService, Principal, app

client = TestClient(app)

TITLE = "바리온 메타연구 오토메이션 엔지니어링 프레임워크"
BODY = "바리온 메타연구 오토메이션 엔지니어링 프레임워크는 연구 자동화를 다룹니다."


def headers(organization: str) -> dict[str, str]:
    return {"X-User-Id": "nfd-user", "X-Organization-Id": organization, "X-Roles": "admin"}


def test_ir_normalizes_nfd_text() -> None:
    nfd = unicodedata.normalize("NFD", BODY)
    assert nfd != BODY

    document = parse_text(nfd.encode(), "nfd.txt", Acl("o", "u"), "mem://nfd")
    assert document.blocks[0].text == BODY
    chunks = chunk_document(document)
    assert chunks[0].text == unicodedata.normalize("NFKC", chunks[0].text)
    assert normalize_text(nfd) == BODY


def test_store_normalizes_nfd_documents_on_load(tmp_path) -> None:
    nfd = unicodedata.normalize("NFD", BODY)
    payload = {
        "id": "legacy-nfd", "organization_id": "org", "owner_id": "user",
        "title": unicodedata.normalize("NFD", TITLE + ".md"),
        "content": nfd, "allowed_roles": ["member"], "created_at": "2026-11-11T00:00:00Z",
        "chunks": [{"text": nfd}], "metadata": {},
    }
    (tmp_path / "legacy-nfd.json").write_text(__import__("json").dumps(payload, ensure_ascii=False), encoding="utf-8")

    store = DocumentService(tmp_path)
    restored = store.documents["legacy-nfd"]
    assert restored.title == TITLE + ".md"
    assert restored.content == BODY
    assert restored.chunks[0]["text"] == BODY


def test_local_search_matches_nfd_document_with_nfc_query(tmp_path) -> None:
    store = DocumentService(tmp_path)
    principal = Principal("user", "org", {"member"})
    document = store.create(principal, "nfd.md", unicodedata.normalize("NFD", BODY), {"member"})

    hits = store.search(principal, "바리온 메타연구")
    assert hits
    assert hits[0]["document_id"] == document.id
    assert unicodedata.normalize("NFKC", hits[0]["content"]) == hits[0]["content"]


def test_local_search_matches_document_title(tmp_path) -> None:
    store = DocumentService(tmp_path)
    principal = Principal("user", "org", {"member"})
    document = store.create(principal, TITLE + ".md", "본문에는 제목 단어가 없습니다.", {"member"})

    hits = store.search(principal, TITLE)
    assert hits
    assert hits[0]["document_id"] == document.id


def test_upload_nfd_korean_document_is_retrievable() -> None:
    org = "org-nfd-upload"
    payload = unicodedata.normalize("NFD", f"# {TITLE}\n\n{BODY}").encode("utf-8")
    upload = client.post(
        "/v1/documents",
        files={"file": ("nfd-framework.md", payload, "text/markdown")},
        headers=headers(org),
    )
    assert upload.status_code == 200, upload.text

    search = client.get("/v1/documents/search", params={"q": "바리온 메타연구"}, headers=headers(org))
    assert search.status_code == 200
    assert search.json()["data"]

    chat = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "바리온 메타연구 프레임워크란?"}], "stream": False, "use_retrieval": True},
        headers=headers(org),
    )
    assert chat.status_code == 200
    assert chat.json()["citations"]
