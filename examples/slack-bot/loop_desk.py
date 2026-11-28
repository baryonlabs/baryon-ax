"""The question–certification loop in Slack: the bot asks first, people answer and certify.

baryon-ax's loop (apps/api/src/baryon/loop.py) finds *behaviour–knowledge gaps* — decisions people made that the
company's knowledge does not explain — and turns each into one question with three expected answers. This module is
its Slack front end:

* **Recording** (consent): messages by people listed in LOOP_SLACK_USERS that make a decision or a request
  ("운송비는 빼고 견적 내자", "이번 건은 보류하자") are recorded as actions with the thread just before them.
  Nobody else is recorded. LOOP_SLACK_USERS=* records everyone in channels the bot is in — only with consent.
* **Rounds**: every LOOP_INTERVAL_MINUTES the bot runs a round (ontology from new documents, gap judgment,
  questions) and **asks first**: a DM to the person with the question, three expected answers, "모두 틀림" and
  "직접 쓰기". Picking an answer opens a short form to add the condition; "모두 틀림" asks for the real criterion.
* **Certification**: the answer becomes a criterion. The bot shows it next to its primary sources (the recorded
  action, the answer) with one checkbox per source; **서명(인증)** is refused by the server until every source is
  checked and any [쟁점] is resolved. 보류·거부 ask for a reason and send the item back to draft.
* "@baryon 질문 있어?" shows my open questions now; "@baryon 인증할 것" shows what waits for my certification.

Environment: LOOP_SLACK_USERS, LOOP_INTERVAL_MINUTES (default 60, 0 = no schedule), LOOP_MAX_QUESTIONS (per person
per round, default 2), LOOP_DOCUMENTS_PER_ROUND (default 5).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass

import httpx

logger = logging.getLogger("baryon.slack.loop")

ACTION_PICK = "loop_pick"          # value: question id|choice
ACTION_NONE = "loop_none"          # 모두 틀림
ACTION_WRITE = "loop_write"        # 직접 쓰기
ACTION_SKIP = "loop_skip"
ACTION_CERTIFY = "loop_certify"
ACTION_HOLD = "loop_hold"
ACTION_REJECT = "loop_reject"
VIEW_ANSWER = "loop_answer_view"
VIEW_REASON = "loop_reason_view"
CHECKS = "loop_checks"
CONTEST = "loop_contest"

# A decision or a request: "~하자", "~하기로", "빼고", "보류", "승인", "~해 줘" …
DECISION_RE = re.compile(
    r"하자|하기로|합시다|갑시다|로\s*가자|으로\s*가자|정했|결정|확정|보류|반려|승인|허용|금지|제외|빼고|말고|대신|우선|먼저\s*해|"
    r"하지\s*마|하지마|하지\s*말자|미루|늦추|당기|바꿔|바꾸자|올려\s*줘|내려\s*줘|해\s*줘|해줘|맡기|넘기자|중단|멈춰", re.I)
ASK_NOW_RE = re.compile(r"(?:나한테|내게|나에게)?\s*(?:질문|물어볼\s*것|물어볼\s*거)\s*(?:있어|있나|있니|있으면|보여|줘)|먼저\s*물어", re.I)
CERTIFY_LIST_RE = re.compile(r"인증\s*(?:할\s*것|할\s*거|대기|목록|할게|하자)", re.I)
MAX_LABEL = 75  # Slack button text limit


@dataclass
class LoopConfig:
    users: frozenset[str] = frozenset()
    everyone: bool = False
    interval_minutes: float = 60.0
    max_questions: int = 2
    documents_per_round: int = 5

    @property
    def records(self) -> bool:
        return self.everyone or bool(self.users)

    @classmethod
    def from_env(cls) -> "LoopConfig":
        raw = os.getenv("LOOP_SLACK_USERS", "").strip()
        return cls(users=frozenset(u.strip() for u in raw.split(",") if u.strip() and u.strip() != "*"), everyone=raw == "*",
                   interval_minutes=float(os.getenv("LOOP_INTERVAL_MINUTES", "60")),
                   max_questions=int(os.getenv("LOOP_MAX_QUESTIONS", "2")),
                   documents_per_round=int(os.getenv("LOOP_DOCUMENTS_PER_ROUND", "5")))


def wants_questions(text: str) -> bool:
    return bool(ASK_NOW_RE.search(text or ""))


def wants_certify_list(text: str) -> bool:
    return bool(CERTIFY_LIST_RE.search(text or ""))


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def question_blocks(question: dict) -> list[dict]:
    action = (question.get("action") or {}).get("text", "")
    buttons = [{"type": "button", "action_id": f"{ACTION_PICK}_{index}", "value": f"{question['id']}|{index}",
                "text": {"type": "plain_text", "text": _clip(f"{answer.get('perspective', '')}: {answer['text']}", MAX_LABEL)}}
               for index, answer in enumerate(question.get("expected_answers") or [])]
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": f":thinking_face: *먼저 여쭤봐요*\n{question['question']}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _clip(f"이 행동을 기존 문서로는 설명하지 못했어요: _{action}_", 2900)}]},
        {"type": "actions", "block_id": f"loop_q_{question['id']}", "elements": buttons},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": ACTION_NONE, "value": question["id"], "style": "danger", "text": {"type": "plain_text", "text": "모두 틀림"}},
            {"type": "button", "action_id": ACTION_WRITE, "value": question["id"], "text": {"type": "plain_text", "text": "직접 쓰기"}},
            {"type": "button", "action_id": ACTION_SKIP, "value": question["id"], "text": {"type": "plain_text", "text": "나중에"}}]},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "예상 답은 정답이 아니라 출발점이에요. 고르고 나서 조건을 한 줄 덧붙이면 더 정확한 기준이 됩니다."}]},
    ]


def certify_blocks(item: dict) -> list[dict]:
    """The paper's certification screen in Slack: the draft, each primary source with a checkbox, then sign."""
    names = {"action": "기록된 행동", "answer": "질문과 답", "document": "문서"}
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "인증 대기"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _clip(f"*AI 초안* · {item.get('label', '')}\n{item.get('definition', '')}", 2900)}},
        {"type": "divider"},
    ]
    options = []
    for index, source in enumerate(item.get("sources") or []):
        title = names.get(source.get("type", ""), "원문")
        if source.get("type") == "document":
            title = f"문서 {source.get('title', '')} · 조각 {source.get('chunk', '')}"
        body = source.get("text") or source.get("quote") or ""
        if source.get("type") == "answer" and source.get("question"):
            body = f"질문: {source['question']}\n답: {source.get('quote', '')}"
        if source.get("unavailable"):
            body = "(볼 권한이 없는 문서입니다. 대조할 수 없으면 보류하세요.)"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip(f"*원문 {index + 1}. {title}*\n>{body.replace(chr(10), chr(10) + '>')}", 2900)}})
        options.append({"text": {"type": "plain_text", "text": f"원문 {index + 1}과 대조했어요"}, "value": str(index)})
    if options:
        blocks.append({"type": "actions", "block_id": CHECKS, "elements": [{"type": "checkboxes", "action_id": CHECKS, "options": options}]})
    if item.get("contested"):
        choices = [{"text": {"type": "plain_text", "text": _clip(f"해석 {i + 1}: {reading.get('definition', '')}", MAX_LABEL)}, "value": str(i)}
                   for i, reading in enumerate(item.get("interpretations") or [])]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*[쟁점]* 출처마다 정의가 달라요. 두 해석은 모두 보존되고, 인증에는 고른 해석이 기록됩니다."}})
        blocks.append({"type": "actions", "block_id": CONTEST, "elements": [{"type": "radio_buttons", "action_id": CONTEST, "options": choices}]})
    blocks.append({"type": "actions", "elements": [
        {"type": "button", "action_id": ACTION_CERTIFY, "value": item["id"], "style": "primary", "text": {"type": "plain_text", "text": "서명(인증)"}},
        {"type": "button", "action_id": ACTION_HOLD, "value": item["id"], "text": {"type": "plain_text", "text": "보류"}},
        {"type": "button", "action_id": ACTION_REJECT, "value": item["id"], "style": "danger", "text": {"type": "plain_text", "text": "거부"}}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "모든 원문을 대조해 체크해야 서명할 수 있어요(서버가 검사합니다). 인증된 지식만 의사결정에 쓰입니다."}]})
    return blocks


def answer_view(question: dict, choice: int | None, none_correct: bool) -> dict:
    if none_correct:
        intro, label, optional = "예상 답이 모두 틀렸다면, 실제로 따른 기준을 적어 주세요.", "실제 기준", False
    elif choice is None:
        intro, label, optional = "기준을 직접 적어 주세요.", "기준", False
    else:
        picked = question["expected_answers"][choice]["text"]
        intro, label, optional = f"고른 답: *{picked}*\n적용 조건이나 예외를 한 줄 덧붙이면 더 정확한 기준이 됩니다.", "덧붙일 조건 (선택)", True
    return {
        "type": "modal", "callback_id": VIEW_ANSWER,
        "private_metadata": json.dumps({"question": question["id"], "choice": choice, "none": none_correct}),
        "title": {"type": "plain_text", "text": "먼저 여쭤봐요"}, "submit": {"type": "plain_text", "text": "보내기"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": _clip(f"*{question['question']}*\n{intro}", 2900)}},
            {"type": "input", "block_id": "text", "optional": optional, "label": {"type": "plain_text", "text": label},
             "element": {"type": "plain_text_input", "action_id": "text", "multiline": True, "max_length": 1000}},
        ],
    }


def reason_view(item_id: str, decision: str, message: dict) -> dict:
    name = "보류" if decision == "hold" else "거부"
    return {
        "type": "modal", "callback_id": VIEW_REASON,
        "private_metadata": json.dumps({"item": item_id, "decision": decision, **message}),
        "title": {"type": "plain_text", "text": f"{name} 사유"}, "submit": {"type": "plain_text", "text": name},
        "blocks": [{"type": "input", "block_id": "reason", "label": {"type": "plain_text", "text": "사유 (5자 이상)"},
                    "element": {"type": "plain_text_input", "action_id": "reason", "min_length": 5, "max_length": 1000}}],
    }


class LoopDesk:
    def __init__(self, config: LoopConfig, baryon, bot_user_id: str) -> None:
        self.config = config
        self.baryon = baryon
        self.bot_user_id = bot_user_id

    # ---------------------------------------------------------------- recording

    def observe(self, event: dict, web) -> bool:
        """Record a consenting person's decision or request as an action. True when recorded."""
        user = event.get("user", "")
        text = re.sub(r"<@[A-Z0-9]+>", " ", event.get("text") or "").strip()
        if (not self.config.records or event.get("bot_id") or user == self.bot_user_id or event.get("subtype") not in (None, "file_share")
                or (not self.config.everyone and user not in self.config.users) or len(text) < 6 or not DECISION_RE.search(text)
                or wants_questions(text) or wants_certify_list(text)):
            return False
        channel, thread_ts = event.get("channel", ""), event.get("thread_ts") or event.get("ts", "")
        context = ""
        if event.get("thread_ts"):
            try:
                replies = web.conversations_replies(channel=channel, ts=event["thread_ts"], limit=20).get("messages", [])
                before = [m for m in replies if float(m.get("ts", 0)) < float(event.get("ts", 0))][-4:]
                context = "\n".join(re.sub(r"<@[A-Z0-9]+>", "", m.get("text") or "").strip() for m in before)[:2000]
            except Exception:  # noqa: BLE001 - no history scope: record without context
                pass
        try:
            response = self.baryon.as_user("POST", "/v1/loop/actions", user, json={
                "text": f"<@{user}>님이 슬랙에서 말한 결정·요청: {text[:600]}", "context": context, "source": "slack",
                "source_ref": f"slack:{channel}:{thread_ts}"})
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            logger.warning("could not record the action", exc_info=True)
            return False

    # ---------------------------------------------------------------- asking first

    def run_round(self) -> dict:
        response = self.baryon.as_bot("POST", "/v1/loop/rounds", json={"documents": self.config.documents_per_round})
        response.raise_for_status()
        return response.json()

    def ask_pending(self, web, only_user: str = "", channel: str = "", thread_ts: str = "") -> int:
        """Post open questions nobody has been shown yet: a DM to each person (or here, when asked)."""
        if only_user:
            response = self.baryon.as_user("GET", "/v1/loop/questions", only_user)
        else:
            response = self.baryon.as_bot("GET", "/v1/loop/questions", params={"everyone": "true"})
        response.raise_for_status()
        per_person: dict[str, int] = {}
        asked = 0
        for question in response.json().get("data", []):
            person = question.get("asked_to", "")
            if not person.startswith("slack:") or (not only_user and int(question.get("asked_count") or 0) > 0):
                continue
            if per_person.get(person, 0) >= self.config.max_questions:
                continue
            slack_user = person.split(":", 1)[1]
            target = channel or slack_user  # a user id as channel posts to the person's DM with the bot
            web.chat_postMessage(channel=target, thread_ts=thread_ts or None, text=f"먼저 여쭤봐요: {question['question']}",
                                 blocks=question_blocks(question))
            self.baryon.as_bot("POST", f"/v1/loop/questions/{question['id']}/asked")
            per_person[person] = per_person.get(person, 0) + 1
            asked += 1
        return asked

    def run_once(self, web) -> dict:
        summary = self.run_round()
        summary["asked"] = self.ask_pending(web)
        logger.info("loop round %s: %s", summary.get("round"), {k: v for k, v in summary.items() if isinstance(v, int)})
        return summary

    # ---------------------------------------------------------------- answering

    def _question(self, user: str, question_id: str) -> dict | None:
        response = self.baryon.as_user("GET", "/v1/loop/questions", user, params={"status": "all"})
        response.raise_for_status()
        return next((q for q in response.json().get("data", []) if q["id"] == question_id), None)

    def on_button(self, body: dict, web) -> None:
        action = (body.get("actions") or [{}])[0]
        user = (body.get("user") or {}).get("id", "")
        action_id, value = action.get("action_id", ""), action.get("value", "")
        if action_id.startswith(ACTION_PICK) or action_id in (ACTION_NONE, ACTION_WRITE):
            question_id, _, choice = value.partition("|")
            question = self._question(user, question_id)
            if not question or question["status"] != "open":
                self._tell(web, body, "이미 답했거나 다른 분께 드린 질문이에요.")
                return
            web.views_open(trigger_id=body["trigger_id"], view=answer_view(
                question, int(choice) if choice else None, action_id == ACTION_NONE))
        elif action_id == ACTION_SKIP:
            self.baryon.as_user("POST", f"/v1/loop/questions/{value}/skip", user)
            self._replace(web, body, ":hourglass: 다음 라운드에 다시 여쭤볼게요.")
        elif action_id == ACTION_CERTIFY:
            self._certify(body, web, user, value)
        elif action_id in (ACTION_HOLD, ACTION_REJECT):
            message = {"channel": (body.get("channel") or {}).get("id", ""), "ts": (body.get("message") or {}).get("ts", "")}
            web.views_open(trigger_id=body["trigger_id"], view=reason_view(value, "hold" if action_id == ACTION_HOLD else "reject", message))

    def on_answer_view(self, body: dict, web) -> dict:
        """The answer form was sent: record the answer. Returns Slack form errors, or {"item_id"} to show next."""
        user = (body.get("user") or {}).get("id", "")
        meta = json.loads((body.get("view") or {}).get("private_metadata") or "{}")
        values = (body.get("view") or {}).get("state", {}).get("values", {})
        text = ((values.get("text") or {}).get("text") or {}).get("value") or ""
        response = self.baryon.as_user("POST", f"/v1/loop/questions/{meta['question']}/answer", user,
                                       json={"choice": meta.get("choice"), "none_correct": bool(meta.get("none")), "text": text})
        if response.status_code >= 400:
            detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else ""
            message = detail.get("message") if isinstance(detail, dict) else str(detail or response.status_code)
            return {"response_action": "errors", "errors": {"text": message[:150]}}
        return {"item_id": response.json()["id"]}

    def after_answer(self, web, user: str, item_id: str) -> None:
        self.show_item(web, user, item_id, intro=":memo: 답을 기준 초안으로 남겼어요. 원문과 대조해 인증해 주세요.")

    def show_item(self, web, user: str, item_id: str, intro: str = "", channel: str = "", thread_ts: str = "") -> None:
        response = self.baryon.as_user("GET", f"/v1/loop/items/{item_id}", user)
        response.raise_for_status()
        item = response.json()
        blocks = ([{"type": "section", "text": {"type": "mrkdwn", "text": intro}}] if intro else []) + certify_blocks(item)
        web.chat_postMessage(channel=channel or user, thread_ts=thread_ts or None, text=f"인증 대기: {item.get('label', '')}", blocks=blocks)

    def list_for_certification(self, web, user: str, channel: str, thread_ts: str, limit: int = 3) -> int:
        response = self.baryon.as_user("GET", "/v1/loop/items", user, params={"status": "verified"})
        response.raise_for_status()
        items = [item for item in response.json().get("data", [])
                 if item.get("kind") != "criterion" or item.get("answered_by") == f"slack:{user}"][:limit]
        for item in items:
            self.show_item(web, user, item["id"], channel=channel, thread_ts=thread_ts)
        return len(items)

    # ---------------------------------------------------------------- certification

    def _certify(self, body: dict, web, user: str, item_id: str) -> None:
        values = (body.get("state") or {}).get("values") or {}
        checked = [int(option["value"]) for option in ((values.get(CHECKS) or {}).get(CHECKS) or {}).get("selected_options") or []]
        contest = ((values.get(CONTEST) or {}).get(CONTEST) or {}).get("selected_option")
        response = self.baryon.as_user("POST", f"/v1/loop/items/{item_id}/decision", user, json={
            "decision": "approve", "checked": checked, "contest_choice": int(contest["value"]) if contest else None})
        if response.status_code == 200:
            item = response.json()
            self._replace(web, body, f":white_check_mark: *인증했어요* · {item.get('label', '')}\n{item.get('definition', '')}\n"
                                     "_이제 인증된 지식으로 답변의 근거와 의사결정에 쓰입니다._")
            return
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        message = detail.get("message") if isinstance(detail, dict) else f"인증하지 못했어요 (HTTP {response.status_code})"
        if isinstance(detail, dict) and detail.get("code") == "gate_incomplete":
            message = "모든 원문을 대조해 체크해야 서명할 수 있어요." + (" [쟁점] 해석도 골라 주세요." if "contest" in detail.get("message", "") else "")
        self._tell(web, body, ":no_entry: " + message)

    def on_reason_view(self, body: dict, web) -> dict | None:
        user = (body.get("user") or {}).get("id", "")
        meta = json.loads((body.get("view") or {}).get("private_metadata") or "{}")
        values = (body.get("view") or {}).get("state", {}).get("values", {})
        reason = ((values.get("reason") or {}).get("reason") or {}).get("value") or ""
        response = self.baryon.as_user("POST", f"/v1/loop/items/{meta['item']}/decision", user,
                                       json={"decision": meta["decision"], "reason": reason})
        if response.status_code >= 400:
            return {"response_action": "errors", "errors": {"reason": f"처리하지 못했어요 (HTTP {response.status_code})"}}
        name = "보류" if meta["decision"] == "hold" else "거부"
        if meta.get("channel") and meta.get("ts"):
            web.chat_update(channel=meta["channel"], ts=meta["ts"], text=f"{name}했어요", blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f":leftwards_arrow_with_hook: *{name}했어요* — 초안으로 돌아갔어요.\n사유: {reason}"}}])
        return None

    # ---------------------------------------------------------------- slack helpers

    @staticmethod
    def _tell(web, body: dict, text: str) -> None:
        channel = (body.get("channel") or {}).get("id") or (body.get("user") or {}).get("id", "")
        try:
            web.chat_postEphemeral(channel=channel, user=(body.get("user") or {}).get("id", ""), text=text)
        except Exception:  # noqa: BLE001 - ephemeral messages fail in some DMs: post plainly
            web.chat_postMessage(channel=channel, text=text)

    @staticmethod
    def _replace(web, body: dict, text: str) -> None:
        channel = (body.get("channel") or {}).get("id", "")
        ts = (body.get("message") or {}).get("ts", "")
        web.chat_update(channel=channel, ts=ts, text=text, blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}])


def start_loop_scheduler(desk: LoopDesk, web, stop: threading.Event | None = None) -> threading.Thread | None:
    if desk.config.interval_minutes <= 0:
        return None
    stop = stop or threading.Event()

    def run() -> None:
        stop.wait(30)
        while not stop.is_set():
            try:
                desk.run_once(web)
            except Exception:
                logger.exception("loop round failed")
            stop.wait(max(300.0, desk.config.interval_minutes * 60))

    thread = threading.Thread(target=run, name="baryon-loop", daemon=True)
    thread.start()
    return thread
