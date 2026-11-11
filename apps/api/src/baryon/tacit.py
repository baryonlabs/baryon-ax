"""Tacit knowledge elicitation.

Tacit knowledge lives in people, not documents. This module runs a light
question-and-answer loop:

    question (from an open gap or a template)
      -> user answer + optional attachments
      -> publish: the answer becomes an indexed document
      -> the original gap is re-evaluated and may resolve

The question source matters: open knowledge gaps are the highest-signal
questions because the platform already failed to answer them.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gaps import GapStore, salient_terms
from .ingest.ir import normalize_text

# Curated prompts for tacit knowledge that documents rarely capture.
QUESTION_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "id": "tpl_judgement",
        "category": "판단 기준",
        "question": "이 업무에서 문서에 적혀 있지 않지만 반드시 알아야 하는 판단 기준은 무엇인가요?",
    },
    {
        "id": "tpl_onboarding",
        "category": "온보딩",
        "question": "신규 담당자가 가장 자주 틀리는 부분과 그 이유는 무엇인가요?",
    },
    {
        "id": "tpl_exception",
        "category": "권한/예외",
        "question": "예외 처리나 승인 없이 진행해도 되는 범위는 어디까지인가요?",
    },
    {
        "id": "tpl_glossary",
        "category": "용어",
        "question": "우리 조직에서만 다르게 쓰는 용어나 약어는 무엇인가요?",
    },
    {
        "id": "tpl_incident",
        "category": "문제 해결",
        "question": "최근 6개월간 반복해서 문제가 된 상황과 해결 방법은 무엇인가요?",
    },
)


@dataclass
class TacitEntry:
    id: str
    organization_id: str
    author_id: str
    question: str
    answer: str
    attachments: list[dict[str, str]] = field(default_factory=list)
    status: str = "draft"  # draft | published
    gap_id: str | None = None
    source: str = "template"  # template | gap | custom
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    published_document_id: str | None = None
    published_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TacitStore:
    """Append-only JSONL; later records for the same id win."""

    def __init__(self, path: str | Path | None = None) -> None:
        default = os.getenv("TACIT_STORE", "/tmp/baryon-ax-tacit/tacit.jsonl")
        self.path = Path(path or default)
        self._lock = threading.Lock()
        self._entries: dict[str, TacitEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                entry = TacitEntry(**json.loads(line))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            entry.question = normalize_text(entry.question)
            entry.answer = normalize_text(entry.answer)
            self._entries[entry.id] = entry

    def _persist(self, entry: TacitEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")

    def create(
        self,
        *,
        organization_id: str,
        author_id: str,
        question: str,
        answer: str,
        attachments: list[dict[str, str]] | None = None,
        gap_id: str | None = None,
        source: str = "template",
    ) -> TacitEntry:
        entry = TacitEntry(
            id="tk_" + uuid.uuid4().hex[:16],
            organization_id=organization_id,
            author_id=author_id,
            question=normalize_text(question).strip()[:500],
            answer=normalize_text(answer).strip()[:20000],
            attachments=attachments or [],
            gap_id=gap_id,
            source=source,
        )
        with self._lock:
            self._entries[entry.id] = entry
            self._persist(entry)
        return entry

    def update(
        self,
        organization_id: str,
        entry_id: str,
        *,
        answer: str | None = None,
        attachments: list[dict[str, str]] | None = None,
        actor_id: str = "",
        is_admin: bool = False,
    ) -> TacitEntry | None:
        with self._lock:
            entry = self._entries.get(entry_id)
            if not entry or entry.organization_id != organization_id:
                return None
            if entry.author_id != actor_id and not is_admin:
                raise PermissionError("Only the author or an administrator can edit this answer")
            if entry.status == "published":
                raise ValueError("Published answers cannot be edited")
            if answer is not None:
                entry.answer = normalize_text(answer).strip()[:20000]
            if attachments is not None:
                entry.attachments = attachments
            entry.updated_at = datetime.now(timezone.utc).isoformat()
            self._persist(entry)
            return entry

    def get(self, organization_id: str, entry_id: str) -> TacitEntry | None:
        entry = self._entries.get(entry_id)
        return entry if entry and entry.organization_id == organization_id else None

    def list(
        self,
        organization_id: str,
        *,
        author_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[TacitEntry]:
        entries = [
            entry for entry in self._entries.values()
            if entry.organization_id == organization_id
            and (author_id is None or entry.author_id == author_id)
            and (status is None or entry.status == status)
        ]
        entries.sort(key=lambda entry: entry.updated_at, reverse=True)
        return entries[:limit]

    def mark_published(self, organization_id: str, entry_id: str, document_id: str) -> TacitEntry | None:
        with self._lock:
            entry = self._entries.get(entry_id)
            if not entry or entry.organization_id != organization_id:
                return None
            entry.status = "published"
            entry.published_document_id = document_id
            entry.published_at = datetime.now(timezone.utc).isoformat()
            entry.updated_at = entry.published_at
            self._persist(entry)
            return entry

    def summary(self, organization_id: str) -> dict[str, int]:
        entries = [entry for entry in self._entries.values() if entry.organization_id == organization_id]
        return {
            "total": len(entries),
            "draft": sum(1 for entry in entries if entry.status == "draft"),
            "published": sum(1 for entry in entries if entry.status == "published"),
            "with_attachments": sum(1 for entry in entries if entry.attachments),
        }


def suggest_questions(
    *,
    organization_id: str,
    user_id: str,
    is_admin: bool,
    gap_store: GapStore,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Open gaps first (highest signal), then curated templates."""
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()

    gaps = gap_store.list(organization_id, "open", 100)
    for gap in gaps:
        if not is_admin and gap.user_id != user_id:
            continue
        if gap.question in seen:
            continue
        seen.add(gap.question)
        questions.append(
            {
                "id": "gap:" + gap.id,
                "question": gap.question,
                "source": "gap",
                "gap_id": gap.id,
                "category": "미해결 질문",
                "occurrences": gap.occurrences,
                "reasons": gap.reasons,
                "terms": gap.terms or salient_terms(gap.question)[:5],
            }
        )
        if len(questions) >= limit:
            return questions

    for template in QUESTION_TEMPLATES:
        if template["question"] in seen:
            continue
        seen.add(template["question"])
        questions.append({**template, "source": "template", "occurrences": 1, "reasons": [], "terms": []})
        if len(questions) >= limit:
            break
    return questions
