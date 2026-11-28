"""What the bot knows about each Slack thread, and the one place its "저장했어요" may come from.

In one thread the bot said "초안으로 남겨 둘게요", then "진행할게요", then "끝났어요" — while the owner's confirmation had
been read as a "no" and nothing was saved. Status words came from whichever component happened to post. This module
keeps, per thread:

* the thread's **purpose**, open **requests**, **decisions**, **owners** (who does what) and open **questions**, taken
  from the thread-understanding reading (``POST /v1/loop/threads/understand``) with the messages they cite;
* the **work state** of what the bot was asked to keep or do: ``draft`` (초안) → ``pending`` (확인 대기) →
  ``saved`` (저장됨) | ``rejected`` (거부됨). ``saved`` only follows ``pending`` (someone approved); a move the
  rules do not allow is refused and logged.

:func:`summary_text` answers "@baryon 정리해줘" from it. :func:`unbacked_claims` finds status words in a reply
("저장했어요", "진행할게요", "끝났어요", "초안으로 남겨 둘게요") that the thread's work state does not back, and
:func:`guard` removes them and states the real status instead, so the bot never claims what did not happen.

An edited or deleted message drops what it was the evidence for (:meth:`ThreadStateStore.forget_message`); the next
reading of the thread fills it again from what the message says now.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("baryon.slack.state")

NONE, DRAFT, PENDING, SAVED, REJECTED = "", "draft", "pending", "saved", "rejected"
STATUS_LABEL = {NONE: "아직 남긴 것 없음", DRAFT: "초안", PENDING: "확인 대기", SAVED: "저장됨", REJECTED: "거부됨"}
ALLOWED = {NONE: {DRAFT, PENDING}, DRAFT: {PENDING, DRAFT, REJECTED}, PENDING: {SAVED, REJECTED, PENDING},
           SAVED: {DRAFT, PENDING}, REJECTED: {DRAFT, PENDING}}

# Status words, and the work states that back them.
CLAIMS = (
    ("draft", re.compile(r"초안\s*(?:으로|을|를)?\s*(?:남겨|남겼|저장|만들어\s*(?:뒀|두었|놨))"), {DRAFT, PENDING, SAVED}),
    ("progress", re.compile(r"진행할게요|진행하겠습니다|진행합니다|진행됐|진행됨|진행했|실행했|실행됐|저장할게요|저장하겠습니다|남겨\s*둘게요"),
     {SAVED}),
    ("done", re.compile(r"끝났어요|끝냈어요|마쳤어요|완료했|완료됐|저장했|저장됐|저장해\s*(?:뒀|두었|놨)|남겼어요|등록했어요|반영했어요"),
     {SAVED}),
    ("rejected", re.compile(r"거부됐|거부됨|취소했어요|취소됐|저장하지\s*않았"), {REJECTED}),
)


@dataclass
class Item:
    text: str
    by: str = ""
    evidence_ts: list[str] = field(default_factory=list)
    status: str = ""  # decisions: confirmed | unconfirmed; requests: open | done


@dataclass
class ThreadState:
    channel: str
    thread_ts: str
    purpose: str = ""
    purpose_ts: list[str] = field(default_factory=list)
    requests: list[Item] = field(default_factory=list)
    decisions: list[Item] = field(default_factory=list)
    owners: list[Item] = field(default_factory=list)
    questions: list[Item] = field(default_factory=list)
    contests: list[Item] = field(default_factory=list)
    status: str = NONE          # work state: draft | pending | saved | rejected
    status_what: str = ""       # what is kept or done ("역할 정리 초안")
    status_by: str = ""         # who approved or refused it
    history: list[dict] = field(default_factory=list)  # every status move: {from, to, what, by, at}
    stale: bool = False         # a message it cites was edited or deleted since the last reading
    updated_at: float = 0.0

    @property
    def status_label(self) -> str:
        return STATUS_LABEL.get(self.status, self.status)

    def items(self) -> list[Item]:
        return self.requests + self.decisions + self.owners + self.questions + self.contests


class ThreadStateStore:
    """Thread states, at most ``max_threads`` (least recently used out), optionally kept in a JSON file
    (``SLACK_THREAD_STATE_PATH``) so a restart does not forget a pending confirmation."""

    def __init__(self, path: str = "", max_threads: int = 2000) -> None:
        self.path = path
        self.max_threads = max_threads
        self._threads: OrderedDict[tuple[str, str], ThreadState] = OrderedDict()
        self.lock = threading.RLock()
        self._load()

    @classmethod
    def from_env(cls) -> ThreadStateStore:
        return cls(os.getenv("SLACK_THREAD_STATE_PATH", ""))

    # ------------------------------------------------------------------ access

    def get(self, channel: str, thread_ts: str) -> ThreadState:
        with self.lock:
            key = (channel, thread_ts)
            state = self._threads.get(key)
            if state is None:
                state = ThreadState(channel, thread_ts)
                self._threads[key] = state
                while len(self._threads) > self.max_threads:
                    self._threads.popitem(last=False)
            self._threads.move_to_end(key)
            return state

    def known(self, channel: str, thread_ts: str) -> ThreadState | None:
        with self.lock:
            return self._threads.get((channel, thread_ts))

    # ------------------------------------------------------------------ the reading

    def apply_understanding(self, channel: str, thread_ts: str, reading: dict) -> ThreadState:
        """Purpose, requests, decisions, owners, open questions and contests from one thread reading."""
        def items(key: str, text_of, by_of=lambda x: x.get("by", ""), status_of=lambda x: x.get("status", "")) -> list[Item]:
            out = []
            for entry in reading.get(key) or []:
                text = str(text_of(entry) or "").strip()
                if text:
                    out.append(Item(text, str(by_of(entry) or ""), [str(ts) for ts in entry.get("evidence_ts") or []],
                                    str(status_of(entry) or "")))
            return out

        with self.lock:
            state = self.get(channel, thread_ts)
            purpose = reading.get("purpose") or {}
            state.purpose = str(purpose.get("text") or "")
            state.purpose_ts = [str(ts) for ts in purpose.get("evidence_ts") or []]
            state.requests = items("requests", lambda r: r.get("what"), status_of=lambda r: r.get("status", "open"))
            state.decisions = items("decisions", lambda d: d.get("statement"), lambda d: ", ".join(d.get("decided_by") or []),
                                    lambda d: "confirmed" if d.get("confirmed") else "unconfirmed")
            state.owners = items("owners", lambda o: f"{o.get('who') or o.get('owner') or ''}: {o.get('what') or o.get('task') or ''}".strip(": "))
            state.questions = items("open_questions", lambda q: q.get("question") or q.get("text"))
            state.contests = items("contests", lambda c: f"{c.get('subject', '')}: " + " / ".join(
                f"{v.get('by', '')} {v.get('value', '')}".strip() for v in c.get("values") or []))
            state.stale = False
            state.updated_at = time.time()
            self._save()
            return state

    def forget_message(self, channel: str, thread_ts: str, ts: str) -> int:
        """An edited or deleted message: drop every item it was evidence for. Returns how many were dropped."""
        with self.lock:
            state = self.known(channel, thread_ts)
            if state is None:
                return 0
            dropped = 0
            for key in ("requests", "decisions", "owners", "questions", "contests"):
                kept = [item for item in getattr(state, key) if ts not in item.evidence_ts]
                dropped += len(getattr(state, key)) - len(kept)
                setattr(state, key, kept)
            if ts in state.purpose_ts:
                state.purpose, state.purpose_ts = "", []
                dropped += 1
            state.stale = True
            self._save()
            return dropped

    # ------------------------------------------------------------------ work state

    def set_status(self, channel: str, thread_ts: str, status: str, what: str = "", by: str = "") -> bool:
        """Move the work state. False (and nothing changes) when the move is not allowed — ``saved`` without a
        ``pending`` confirmation before it, for instance."""
        with self.lock:
            state = self.get(channel, thread_ts)
            if status not in ALLOWED.get(state.status, set()):
                logger.warning("thread %s/%s: refused status %s → %s", channel, thread_ts, state.status or "none", status)
                return False
            state.history.append({"from": state.status, "to": status, "what": what or state.status_what, "by": by, "at": time.time()})
            state.status, state.status_by = status, by
            if what:
                state.status_what = what
            self._save()
            return True

    # ------------------------------------------------------------------ persistence

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                rows = json.load(handle)
            for row in rows:
                for key in ("requests", "decisions", "owners", "questions", "contests"):
                    row[key] = [Item(**item) for item in row.get(key) or []]
                state = ThreadState(**row)
                self._threads[(state.channel, state.thread_ts)] = state
        except (OSError, ValueError, TypeError):
            logger.warning("could not read thread states from %s; starting empty", self.path, exc_info=True)

    def _save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump([asdict(state) for state in self._threads.values()], handle, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            logger.warning("could not write thread states to %s", self.path, exc_info=True)


# --------------------------------------------------------------------------- what the bot says about it


def _bullets(items: list[Item], with_by: bool = False, limit: int = 6) -> list[str]:
    lines = []
    for item in items[:limit]:
        by = f" ({item.by})" if with_by and item.by else ""
        lines.append(f"• {item.text}{by}")
    if len(items) > limit:
        lines.append(f"• …외 {len(items) - limit}건")
    return lines


def summary_text(state: ThreadState | None) -> str:
    """"정리해줘": purpose, decisions, who does what, what is still open, and the work state — only what the
    thread reading found, each part said plainly when empty."""
    if state is None:
        return "이 스레드는 아직 읽지 못했어요. 스레드 안에서 다시 불러 주세요."
    confirmed = [d for d in state.decisions if d.status == "confirmed"]
    proposed = [d for d in state.decisions if d.status != "confirmed"]
    open_requests = [r for r in state.requests if r.status in ("", "open")]
    parts = [f"*목적*\n{state.purpose or '스레드에 목적이 적혀 있지 않아요.'}"]
    parts.append("*결정*\n" + "\n".join(_bullets(confirmed, with_by=True) or ["• 사람이 말로 확정한 결정은 아직 없어요."]))
    if proposed:
        parts.append("*확정 전 (동의 신호만 있거나 제안 단계)*\n" + "\n".join(_bullets(proposed, with_by=True)))
    parts.append("*담당*\n" + "\n".join(_bullets(state.owners) or ["• 정해진 담당이 없어요."]))
    left = _bullets(state.questions) + _bullets(open_requests, with_by=True) + _bullets(state.contests)
    parts.append("*남은 질문*\n" + "\n".join(left or ["• 열린 질문이 없어요."]))
    parts.append(f"*상태*\n{status_line(state)}")
    if state.stale:
        parts.append("_지난번 읽은 뒤 고쳐지거나 지워진 글이 있어 그 글에서 나온 내용은 뺐어요._")
    return "\n\n".join(parts)


def status_line(state: ThreadState | None) -> str:
    if state is None or state.status == NONE:
        return "이 스레드에서 지식으로 남기거나 실행한 것은 없어요."
    what = f" — {state.status_what}" if state.status_what else ""
    by = f" ({state.status_by})" if state.status_by and state.status in (SAVED, REJECTED) else ""
    return f"{state.status_label}{what}{by}"


def unbacked_claims(text: str, state: ThreadState | None) -> list[str]:
    """The status words in ``text`` the work state does not back ("끝났어요" while nothing was saved)."""
    status = state.status if state is not None else NONE
    return [kind for kind, pattern, backed_by in CLAIMS if pattern.search(text or "") and status not in backed_by]


_SENTENCE = re.compile(r"(?<=[.!?。])\s+|\n+")


def guard(text: str, state: ThreadState | None) -> str:
    """``text`` without the sentences whose status words the work state does not back, plus the real status."""
    if not unbacked_claims(text, state):
        return text
    kept = [part for part in _SENTENCE.split(text or "") if part.strip() and not unbacked_claims(part, state)]
    return ("\n".join(kept).strip() + "\n\n" if kept else "") + f"_상태: {status_line(state)}_"
