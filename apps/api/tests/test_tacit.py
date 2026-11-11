"""Tacit knowledge elicitation: store, question suggestions, and publish loop."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("DOCUMENT_STORE", str(Path(tempfile.mkdtemp(prefix="baryon-ax-tacit-")) / "documents"))
os.environ.setdefault("AUDIT_LOG", str(Path(tempfile.mkdtemp(prefix="baryon-ax-tacit-audit-")) / "events.jsonl"))

from fastapi.testclient import TestClient

from baryon.gaps import GapStore, KnowledgeGap
from baryon.main import app
from baryon.tacit import QUESTION_TEMPLATES, TacitStore, suggest_questions

client = TestClient(app)


def headers(user: str = "tacit-author", organization: str = "org-tacit", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": organization, "X-Roles": roles}


def test_store_is_author_scoped_and_publishable(tmp_path) -> None:
    store = TacitStore(tmp_path / "tacit.jsonl")
    entry = store.create(
        organization_id="o", author_id="u1", question="예외 승인 범위는?",
        answer="100만원 이하는 팀장 승인으로 처리합니다.",
        attachments=[{"document_id": "doc1", "title": "승인규정.md"}],
    )
    assert store.list("o", author_id="u1")[0].id == entry.id
    assert store.list("o", author_id="u2") == []

    updated = store.update("o", entry.id, answer="100만원 이하는 팀장 승인.", actor_id="u1")
    assert updated and updated.answer.endswith("팀장 승인.")

    published = store.mark_published("o", entry.id, "doc9")
    assert published.status == "published"
    assert store.summary("o") == {"total": 1, "draft": 0, "published": 1, "with_attachments": 1}


def test_store_rejects_unauthorized_and_published_edits(tmp_path) -> None:
    import pytest

    store = TacitStore(tmp_path / "tacit.jsonl")
    entry = store.create(organization_id="o", author_id="u1", question="q", answer="a")
    with pytest.raises(PermissionError):
        store.update("o", entry.id, answer="b", actor_id="u2")
    store.mark_published("o", entry.id, "doc1")
    with pytest.raises(ValueError):
        store.update("o", entry.id, answer="c", actor_id="u1")


def test_suggest_questions_prefers_open_gaps(tmp_path) -> None:
    gaps = GapStore(tmp_path / "gaps.jsonl")
    gaps.observe(organization_id="o", user_id="u1", question="2027년 계약 갱신 일정은?", answer="모름", citations=0)
    gaps.observe(organization_id="o", user_id="u2", question="다른 사람 질문", answer="모름", citations=0)

    admin_view = suggest_questions(organization_id="o", user_id="admin", is_admin=True, gap_store=gaps, limit=5)
    author_view = suggest_questions(organization_id="o", user_id="u1", is_admin=False, gap_store=gaps, limit=5)

    assert admin_view[0]["source"] == "gap"
    assert author_view[0]["source"] == "gap"
    assert author_view[0]["question"] == "2027년 계약 갱신 일정은?"
    assert all(item["question"] != "다른 사람 질문" for item in author_view)
    assert any(item["source"] == "template" for item in admin_view)


def test_question_templates_cover_tacit_dimensions() -> None:
    categories = {template["category"] for template in QUESTION_TEMPLATES}
    assert {"판단 기준", "온보딩", "권한/예외", "용어", "문제 해결"} <= categories


def test_tacit_flow_publishes_answer_and_closes_gap() -> None:
    org = "org-tacit-flow"
    member = headers(organization=org, roles="member")

    # A question the platform cannot answer becomes a gap and a suggested question.
    client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "예외 승인 없이 처리 가능한 금액은?"}], "stream": False, "use_retrieval": True},
        headers=member,
    )
    questions = client.get("/v1/tacit/questions", headers=member).json()
    assert questions["data"]
    gap_question = next(item for item in questions["data"] if item["source"] == "gap")
    assert "예외 승인" in gap_question["question"]

    # Attachment goes through the normal ingest path first.
    upload = client.post(
        "/v1/documents",
        files={"file": ("approval.md", "팀장 승인 한도는 100만원입니다.".encode(), "text/markdown")},
        headers=member,
    )
    assert upload.status_code == 200
    attachment_id = upload.json()["id"]

    created = client.post(
        "/v1/tacit/entries",
        json={
            "question": gap_question["question"],
            "answer": "100만원 이하는 팀장 승인으로 처리하고, 초과분은 본부장 승인이 필요합니다.",
            "attachment_ids": [attachment_id],
            "gap_id": gap_question["gap_id"],
            "source": "gap",
        },
        headers=member,
    )
    assert created.status_code == 200, created.text
    entry = created.json()
    assert entry["status"] == "draft"
    assert entry["attachments"][0]["document_id"] == attachment_id
    basis = entry["basis"]
    assert basis["source"] == "gap"
    assert basis["gap"]["id"] == gap_question["gap_id"]
    assert basis["gap"]["reasons"]
    assert basis["gap"]["occurrences"] >= 1
    assert basis["gap"]["terms"]

    published = client.post(f"/v1/tacit/entries/{entry['id']}/publish", headers=member)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    document_id = published.json()["published_document_id"]
    assert document_id
    assert published.json()["basis"]["published_document"]["id"] == document_id

    search = client.get("/v1/documents/search", params={"q": "예외 승인 금액"}, headers=member).json()["data"]
    assert any(item["document_id"] == document_id for item in search)

    gaps = client.get("/v1/admin/gaps?status=all", headers=headers(organization=org, roles="admin")).json()["data"]
    resolved = next(item for item in gaps if item["id"] == gap_question["gap_id"])
    assert resolved["status"] == "resolved"

    listed = client.get("/v1/tacit/entries", headers=member).json()["data"]
    saved = next(item for item in listed if item["id"] == entry["id"])
    assert saved["basis"]["gap"]["status"] == "resolved"


def test_tacit_entries_are_not_visible_to_other_users() -> None:
    org = "org-tacit-scope"
    author = headers(user="author", organization=org)
    colleague = headers(user="colleague", organization=org)

    created = client.post(
        "/v1/tacit/entries",
        json={"question": "우리 팀만 쓰는 용어는?", "answer": "우리는 '건'을 '케이스'라고 부릅니다."},
        headers=author,
    )
    assert created.status_code == 200

    mine = client.get("/v1/tacit/entries", headers=author).json()["data"]
    theirs = client.get("/v1/tacit/entries", headers=colleague).json()["data"]
    assert any(item["id"] == created.json()["id"] for item in mine)
    assert all(item["id"] != created.json()["id"] for item in theirs)

    all_entries = client.get("/v1/tacit/entries?scope=all", headers=headers(user="admin", organization=org, roles="admin")).json()["data"]
    assert any(item["id"] == created.json()["id"] for item in all_entries)
