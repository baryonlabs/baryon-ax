"""Proactive mode: the bot asks people first when it could not answer.

baryon-ax records a *knowledge gap* whenever an answer was ungrounded (no sources,
a refusal, …).  This module turns those gaps into a loop that runs in Slack:

1. On a schedule, open gaps the bot has not asked about yet are posted to a
   "knowledge desk" channel: "I couldn't answer this — does anyone know?"
2. A reply in that thread is saved as a tacit-knowledge draft for the person who
   wrote it, and the bot asks before doing anything with it: [문서로 등록] [취소].
3. Only the author can publish.  Publishing indexes the answer as a document and
   resolves the gap in baryon-ax.
4. If the original question came from Slack, the bot goes back to that thread,
   tells the person who asked, and answers again — now with a source.

A "해당 없음" button on the desk post dismisses gaps that are not worth answering.
State (which gaps were asked, where) is kept in a small JSON file so restarts do
not ask twice.  Interactivity works over Socket Mode; no public URL is needed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("baryon.slack.proactive")

ACTION_PUBLISH = "baryon_tacit_publish"
ACTION_DISCARD = "baryon_tacit_discard"
ACTION_DISMISS = "baryon_gap_dismiss"
REASON_LABELS = {
    "no_citations": "근거 문서 없음",
    "refusal": "답을 찾지 못함",
    "no_retrieval": "검색 없이 답함",
    "low_confidence": "확신 낮음",
}


@dataclass
class ProactiveConfig:
    channel: str = ""               # desk channel id; empty disables proactive mode
    interval_minutes: float = 30.0
    hours: str = ""                 # "9-18" = only ask between 09:00 and 17:59 local time
    weekdays_only: bool = False
    max_per_run: int = 3
    min_occurrences: int = 1
    slack_only: bool = False        # only gaps from questions asked in Slack (skip web UI / scripts)
    mention: str = ""               # prepended to each ask, e.g. "<!here>" or "<@U123> <@U456>"
    service_roles: str = "admin"    # listing/dismissing gaps needs an admin principal
    state_path: str = ".data/proactive_state.json"
    reanswer: bool = True           # answer the original Slack thread again after publishing

    @property
    def enabled(self) -> bool:
        return bool(self.channel)

    @classmethod
    def from_env(cls) -> "ProactiveConfig":
        def flag(name: str, default: bool) -> bool:
            return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            channel=os.getenv("PROACTIVE_CHANNEL", "").strip(),
            interval_minutes=float(os.getenv("PROACTIVE_INTERVAL_MINUTES", cls.interval_minutes)),
            hours=os.getenv("PROACTIVE_HOURS", "").strip(),
            weekdays_only=flag("PROACTIVE_WEEKDAYS_ONLY", False),
            max_per_run=int(os.getenv("PROACTIVE_MAX_PER_RUN", cls.max_per_run)),
            min_occurrences=int(os.getenv("PROACTIVE_MIN_OCCURRENCES", cls.min_occurrences)),
            slack_only=flag("PROACTIVE_SLACK_ONLY", False),
            mention=os.getenv("PROACTIVE_MENTION", "").strip(),
            service_roles=os.getenv("PROACTIVE_SERVICE_ROLES", cls.service_roles),
            state_path=os.getenv("PROACTIVE_STATE_PATH", cls.state_path),
            reanswer=flag("PROACTIVE_REANSWER", True),
        )

    def is_active(self, now: datetime) -> bool:
        if self.weekdays_only and now.weekday() >= 5:
            return False
        if self.hours:
            start, _, end = self.hours.partition("-")
            if not (int(start) <= now.hour < int(end or 24)):
                return False
        return True


# --------------------------------------------------------------------------- state


class ProactiveState:
    """gap_id → where it was asked and what happened; persisted as JSON."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.asks: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                self.asks = json.loads(self.path.read_text(encoding="utf-8")).get("asks", {})
            except (OSError, ValueError):
                logger.warning("could not read %s; starting with empty state", self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"asks": self.asks}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def __contains__(self, gap_id: str) -> bool:
        with self._lock:
            return gap_id in self.asks

    def record(self, gap_id: str, **fields: Any) -> None:
        with self._lock:
            self.asks.setdefault(gap_id, {}).update(fields)
            self._save()

    def get(self, gap_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self.asks.get(gap_id)
            return dict(item) if item else None

    def gap_for_thread(self, channel: str, thread_ts: str) -> str | None:
        with self._lock:
            for gap_id, item in self.asks.items():
                if item.get("channel") == channel and item.get("ts") == thread_ts:
                    return gap_id
        return None


# --------------------------------------------------------------------------- baryon-ax calls


class KnowledgeClient:
    """Gap and tacit-knowledge endpoints, on top of the bot's BaryonClient."""

    def __init__(self, baryon, service_roles: str) -> None:
        self.baryon = baryon
        self.service_roles = service_roles

    def _service_headers(self) -> dict[str, str]:
        headers = self.baryon.headers("baryon-bot")
        headers["X-User-Id"] = "slack-bot"
        headers["X-Roles"] = self.service_roles
        return headers

    def open_gaps(self, limit: int = 100) -> list[dict]:
        response = self.baryon.http.get("/v1/admin/gaps", params={"status": "open", "limit": limit},
                                        headers=self._service_headers())
        response.raise_for_status()
        return response.json().get("data", [])

    def dismiss_gap(self, gap_id: str) -> None:
        self.baryon.http.post(f"/v1/admin/gaps/{gap_id}/dismiss", headers=self._service_headers()).raise_for_status()

    def save_answer(self, slack_user: str, question: str, answer: str, gap_id: str) -> dict:
        body = {"question": question[:500], "answer": answer[:20000], "gap_id": gap_id, "source": "slack"}
        response = self.baryon.http.post("/v1/tacit/entries", json=body, headers=self.baryon.headers(slack_user))
        response.raise_for_status()
        return response.json()

    def publish(self, slack_user: str, entry_id: str) -> dict:
        response = self.baryon.http.post(f"/v1/tacit/entries/{entry_id}/publish", headers=self.baryon.headers(slack_user))
        response.raise_for_status()
        return response.json()


# --------------------------------------------------------------------------- slack blocks


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in text.strip().splitlines() or [""])


def ask_blocks(gap: dict, mention: str = "") -> list[dict]:
    reasons = ", ".join(REASON_LABELS.get(reason, reason) for reason in gap.get("reasons") or [])
    asked_by = gap.get("user_id", "")
    who = f"<@{asked_by.removeprefix('slack:')}>" if asked_by.startswith("slack:") else "누군가"
    count = int(gap.get("occurrences") or 1)
    lead = (mention + " ") if mention else ""
    text = (f"{lead}:raising_hand: *제가 답하지 못한 질문이 있어요.* 아시는 분이 이 스레드에 답을 남겨 주시면 문서로 정리해 둘게요.\n"
            f"{_quote(gap.get('question', ''))}")
    context = f"{who} 님이 물어봄" + (f" · {count}번 나온 질문" if count > 1 else "") + (f" · {reasons}" if reasons else "")
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": ACTION_DISMISS, "value": gap["id"], "text": {"type": "plain_text", "text": "해당 없음"}},
        ]},
    ]


def draft_blocks(user: str, entry_id: str, gap_id: str) -> list[dict]:
    value = f"{entry_id}|{gap_id}"
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"<@{user}> 님 답변을 초안으로 저장했어요. 문서로 등록하면 다음부터 이 질문에 근거와 함께 답할 수 있습니다. 등록할까요?"}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": ACTION_PUBLISH, "style": "primary", "value": value,
             "text": {"type": "plain_text", "text": "문서로 등록"}},
            {"type": "button", "action_id": ACTION_DISCARD, "value": value, "text": {"type": "plain_text", "text": "취소"}},
        ]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "답변을 쓴 사람만 등록할 수 있어요. 다시 답하려면 이 스레드에 새로 남겨 주세요."}]},
    ]


def _note(text: str) -> list[dict]:
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]


# --------------------------------------------------------------------------- the loop


class GapDesk:
    def __init__(self, config: ProactiveConfig, responder, knowledge: KnowledgeClient, state: ProactiveState) -> None:
        self.config = config
        self.responder = responder           # reuses answer formatting and BaryonClient
        self.knowledge = knowledge
        self.state = state

    # 1. ask first ------------------------------------------------------------
    def run_once(self, web, now: datetime | None = None) -> int:
        if not self.config.enabled or not self.config.is_active(now or datetime.now()):
            return 0
        try:
            gaps = self.knowledge.open_gaps()
        except Exception:
            logger.exception("could not list knowledge gaps")
            return 0
        fresh = [gap for gap in gaps
                 if gap.get("id") not in self.state and int(gap.get("occurrences") or 1) >= self.config.min_occurrences
                 and (not self.config.slack_only or str(gap.get("user_id", "")).startswith("slack:"))]
        fresh.sort(key=lambda gap: (int(gap.get("occurrences") or 1), gap.get("last_seen", "")), reverse=True)
        asked = 0
        for gap in fresh[: self.config.max_per_run]:
            blocks = ask_blocks(gap, self.config.mention)
            posted = web.chat_postMessage(channel=self.config.channel, text=f"답하지 못한 질문: {gap.get('question', '')}", blocks=blocks)
            self.state.record(gap["id"], channel=posted.get("channel", self.config.channel), ts=posted["ts"], status="asked",
                              question=gap.get("question", ""), asked_by=gap.get("user_id", ""),
                              origin_thread=gap.get("thread_id") or "", asked_at=datetime.now().isoformat(timespec="seconds"))
            asked += 1
        if asked:
            logger.info("asked about %d knowledge gap(s)", asked)
        return asked

    # 2. a reply in a desk thread is an answer ---------------------------------
    def answer_gap_id(self, event: dict) -> str | None:
        if event.get("subtype") or event.get("bot_id") or not event.get("thread_ts"):
            return None
        if event.get("user") == self.responder.bot_user_id or f"<@{self.responder.bot_user_id}" in (event.get("text") or ""):
            return None  # a mention in the desk thread is a question, not an answer
        return self.state.gap_for_thread(event.get("channel", ""), event["thread_ts"])

    def on_answer(self, event: dict, gap_id: str, web) -> None:
        item = self.state.get(gap_id) or {}
        channel, user, thread_ts = event["channel"], event.get("user", "unknown"), event["thread_ts"]
        if item.get("status") in {"published", "dismissed"}:
            web.chat_postMessage(channel=channel, thread_ts=thread_ts, text="이 질문은 이미 정리됐어요. 고마워요!")
            return
        answer = (event.get("text") or "").strip()
        try:
            entry = self.knowledge.save_answer(user, item.get("question", ""), answer, gap_id)
        except Exception as error:
            logger.exception("saving tacit answer failed")
            web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f":x: 답변을 저장하지 못했습니다 ({_detail(error)}).")
            return
        self.state.record(gap_id, status="answered")
        web.chat_postMessage(channel=channel, thread_ts=thread_ts, text="답변을 문서로 등록할까요?",
                             blocks=draft_blocks(user, entry["id"], gap_id))

    # 3. ask before publishing --------------------------------------------------
    def on_publish(self, body: dict, web) -> None:
        user = body["user"]["id"]
        entry_id, _, gap_id = body["actions"][0]["value"].partition("|")
        channel, ts = body["channel"]["id"], body["message"]["ts"]
        try:
            published = self.knowledge.publish(user, entry_id)
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 403:
                web.chat_postEphemeral(channel=channel, user=user, text="답변을 쓴 사람만 등록할 수 있어요.")
                return
            logger.exception("publish failed")
            web.chat_postEphemeral(channel=channel, user=user, text=f":x: 등록하지 못했습니다 ({_detail(error)}).")
            return
        document_id = published.get("published_document_id") or ""
        self.state.record(gap_id, status="published", entry_id=entry_id, published_by=user)
        web.chat_update(channel=channel, ts=ts, text="문서로 등록했습니다",
                        blocks=_note(f":white_check_mark: <@{user}> 님 답변을 문서로 등록했습니다. 고마워요!"))
        item = self.state.get(gap_id) or {}
        desk_ts = item.get("ts")
        if desk_ts:
            try:
                web.chat_update(channel=item.get("channel", channel), ts=desk_ts, text="답이 등록된 질문",
                                blocks=_note(f":white_check_mark: *답이 등록됐어요* (<@{user}> 님)\n{_quote(item.get('question', ''))}"))
            except Exception:
                logger.info("could not update desk post", exc_info=True)
        # One re-answer per question: later answers are indexed too and will show up the next time it is asked.
        if self.config.reanswer and not item.get("reanswered") and self._reanswer(item, web):
            self.state.record(gap_id, reanswered=True)
        logger.info("published tacit answer %s for gap %s (%s)", entry_id, gap_id, document_id)

    def _reanswer(self, item: dict, web) -> bool:
        origin = item.get("origin_thread") or ""
        asked_by = (item.get("asked_by") or "").removeprefix("slack:")
        parts = origin.split(":")
        if len(parts) != 3 or parts[0] != "slack" or not asked_by:
            return False  # the question came from somewhere other than Slack
        _, channel, thread_ts = parts
        question = item.get("question", "")
        try:
            result = self.responder.baryon.ask([{"role": "user", "content": question}], asked_by, thread_id=origin)
            snippets = self.responder.baryon.snippets(question, asked_by) if self.responder.config.show_snippets and result.citations else {}
        except Exception:
            logger.exception("re-answer failed")
            return False
        from baryon_slack_bot import build_blocks  # local import avoids a cycle

        lead = _note(f"<@{asked_by}> 지난번에 답하지 못했던 질문에 동료가 답을 남겨 줘서, 다시 답해 드려요.")
        web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=result.text,
                             blocks=lead + build_blocks(result, snippets, self.responder.config.max_sources))
        return True

    def on_discard(self, body: dict, web) -> None:
        channel, ts = body["channel"]["id"], body["message"]["ts"]
        _, _, gap_id = body["actions"][0]["value"].partition("|")
        self.state.record(gap_id, status="asked")
        web.chat_update(channel=channel, ts=ts, text="등록하지 않았습니다",
                        blocks=_note(f"등록하지 않았어요 (<@{body['user']['id']}> 님이 취소). 다시 답하려면 이 스레드에 새로 남겨 주세요."))

    def on_dismiss(self, body: dict, web) -> None:
        gap_id = body["actions"][0]["value"]
        channel, ts, user = body["channel"]["id"], body["message"]["ts"], body["user"]["id"]
        try:
            self.knowledge.dismiss_gap(gap_id)
        except Exception as error:
            logger.exception("dismiss failed")
            web.chat_postEphemeral(channel=channel, user=user, text=f":x: 처리하지 못했습니다 ({_detail(error)}).")
            return
        item = self.state.get(gap_id) or {}
        self.state.record(gap_id, status="dismissed", dismissed_by=user)
        web.chat_update(channel=channel, ts=ts, text="해당 없음으로 닫은 질문",
                        blocks=_note(f":heavy_minus_sign: <@{user}> 님이 '해당 없음'으로 닫았어요.\n{_quote(item.get('question', ''))}"))


def _detail(error: Exception) -> str:
    return f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__


def start_scheduler(desk: GapDesk, web, stop: threading.Event | None = None) -> threading.Thread:
    """Background thread: ask about new gaps every PROACTIVE_INTERVAL_MINUTES."""
    stop = stop or threading.Event()

    def loop() -> None:
        stop.wait(10)  # let the socket connect first
        while not stop.is_set():
            try:
                desk.run_once(web)
            except Exception:
                logger.exception("proactive run failed")
            stop.wait(max(60.0, desk.config.interval_minutes * 60))

    thread = threading.Thread(target=loop, name="baryon-proactive", daemon=True)
    thread.start()
    return thread
