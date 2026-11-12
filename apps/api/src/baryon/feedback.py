"""Answer feedback loop.

A thumbs up/down is the cheapest signal that an answer was useful or not. It is
stored next to the trace id so it can be joined with audit events, Langfuse
traces, and knowledge gaps.

Kept deliberately small: JSONL now, Postgres later (same shape as gaps).
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


@dataclass
class AnswerFeedback:
    id: str
    organization_id: str
    user_id: str
    trace_id: str
    rating: int  # 1 = helpful, -1 = not helpful
    reason: str = ""
    question: str = ""
    answer_excerpt: str = ""
    citations: int = 0
    model: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class FeedbackStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("FEEDBACK_STORE", "/tmp/baryon-ax-feedback/feedback.jsonl"))
        self._lock = threading.Lock()
        self._items: list[AnswerFeedback] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                self._items.append(AnswerFeedback(**json.loads(line)))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

    def add(
        self,
        *,
        organization_id: str,
        user_id: str,
        trace_id: str,
        rating: int,
        reason: str = "",
        question: str = "",
        answer_excerpt: str = "",
        citations: int = 0,
        model: str = "",
    ) -> AnswerFeedback:
        if rating not in (-1, 1):
            raise ValueError("rating must be -1 or 1")
        item = AnswerFeedback(
            id="fb_" + uuid.uuid4().hex[:16],
            organization_id=organization_id,
            user_id=user_id,
            trace_id=trace_id[:120],
            rating=rating,
            reason=reason.strip()[:200],
            question=question.strip()[:500],
            answer_excerpt=answer_excerpt.strip()[:500],
            citations=max(0, int(citations)),
            model=model[:120],
        )
        with self._lock:
            self._items.append(item)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item.as_dict(), ensure_ascii=False) + "\n")
        return item

    def list(self, organization_id: str, limit: int = 100) -> list[AnswerFeedback]:
        items = [item for item in self._items if item.organization_id == organization_id]
        return list(reversed(items))[:limit]

    def summary(self, organization_id: str) -> dict[str, int]:
        items = [item for item in self._items if item.organization_id == organization_id]
        return {
            "total": len(items),
            "helpful": sum(1 for item in items if item.rating == 1),
            "not_helpful": sum(1 for item in items if item.rating == -1),
        }
