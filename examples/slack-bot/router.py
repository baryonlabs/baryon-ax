"""One router for every Slack message the bot sees: who handles it, or whether it is answered at all.

The bot used to route by a chain of keyword checks spread over two files. One owner message in one thread went
to the document bot, and the next went to the voice secretary because it contained "작성" ("슬랙에 작성된것을 보고
이해는 해야하는데…"). This module replaces the chain with one decision in two steps:

1. **Gate** (rules only, also when a model classifies): a message is answered only when it is meant for the bot.
   Deleted messages, posts by any bot, messages that open with someone else's mention ("<@other> 여기 내용이다"),
   messages still held because they stop mid-sentence (thread_snapshot.TruncationHold), and plain thread replies
   that do not continue the bot's own last turn are ``silent``. The same rules as the thread-understanding step's
   ``addressed_to_bot`` (apps/api baryon.thread_understanding), so the bot and the loop agree.
2. **Intent**: what the message asks for.

   ========================  ==============================================================  ==========
   intent                    example                                                          handler
   ========================  ==============================================================  ==========
   ``doc_question``          "연차휴가는 며칠이야?"                                          responder
   ``thread_question``       "<@other-bot> 이 어떤 일을 하고 있는지 알아?", "위에서 누가 정했어?"  responder
   ``summary``               "정리해줘", "지금까지 결정된 거 요약해 줘"                      responder
   ``action``                "메일 초안 만들어서 보내 줘" (the secretary's owner only)        secretary
   ``doc_intake``            "문서로 추가해줘", "올린 엑셀 분석해줘"                          responder
   ``guide``                 "설명서 알려줘"                                                  responder
   ``loop_command``          "질문 있어?", "인증할 것"                                       loop
   ``silent``                (the gate said no, or a file without a question)                 —
   ========================  ==============================================================  ==========

   Explicit commands (loop, intake, guide) are recognized by rules. The rest is classified by a model when one is
   given (``classify_model``) and by rules when it is not, or when its answer is unusable. CI runs rules only.
   ``action`` needs the secretary's owner: a model saying "action" for anyone else is read as a question.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from slack_text import HUMAN, USER_TOKEN_APP, Message

logger = logging.getLogger("baryon.slack.router")

DOC_QUESTION, THREAD_QUESTION, SUMMARY, ACTION = "doc_question", "thread_question", "summary", "action"
DOC_INTAKE, GUIDE, LOOP_COMMAND, SILENT = "doc_intake", "guide", "loop_command", "silent"
INTENTS = (DOC_QUESTION, THREAD_QUESTION, SUMMARY, ACTION, DOC_INTAKE, GUIDE, LOOP_COMMAND, SILENT)
MODEL_INTENTS = (DOC_QUESTION, THREAD_QUESTION, SUMMARY, ACTION, SILENT)  # the commands stay with the rules
RESPONDER, SECRETARY, LOOP, NOBODY = "responder", "secretary", "loop", "silent"

# Work for the secretary: an action word *asked for* ("보내 줘", "작성해 줘", "만들어 봐", "연락까지 해 봐"), a
# message that opens with "비서", or an errand noun with a request ending anywhere ("메일 초안 … 해 줘"). A word
# used as a description ("슬랙에 작성된 것", "등록된 문서", "보낸 메일이 뭐였지?") is not a request.
_ASK = r"(?:\s*(?:줘|줄래|주세요|주실래요?|주시겠어요?|봐|봐요|보세요|라|자|둬|놔|놓아\s*줘|부탁(?:해|해요|드려요|합니다)?))"
_DO = r"(?:해|하여|해서)"
ACTION_VERBS = re.compile(
    r"(?:작성|등록|발송|연락|예약|처리|실행|전달|공유|답장|회신|초대|리마인드|정리)\s*" + _DO + _ASK
    + r"|(?:만들어|보내|올려|맡겨|짜|잡아|넣어|써|적어|붙여|옮겨|돌려|띄워)\s*(?:서\s*\S+\s*)?" + _ASK
    + r"|해\s*봐|해봐|진행\s*해\s*(?:줘|주세요)|할\s*일로\s*(?:등록|넣|만들|추가)",
    re.I)
ERRAND_NOUNS = re.compile(r"초안|메일|이메일|답장|일정|미팅|회의\s*잡|예약|리마인드|할\s*일|캘린더|문자|전화|연락", re.I)
REQUEST_END = re.compile(_DO + "?" + _ASK + r"\s*[.!~]*\s*$|해\s*$|하자\s*$|해라\s*$", re.I)
SECRETARY_CALL = re.compile(r"^\s*/?비서(?:\s|야|님|,|:|$)")
ACTION_WORDS = ACTION_VERBS  # the name secretary_bridge.py used; one pattern for both

# Asking for the thread's state: what it is for, what was decided, who does what, what is left.
SUMMARY_RE = re.compile(
    r"(?:정리|요약)\s*(?:해|하여|좀|부탁|해서\s*알려|좀\s*해)?\s*(?:줘|줄래|주세요|줄\s*수|봐|줄래요|해\s*줘|부탁)"
    r"|(?:정리|요약)\s*해\s*줘|(?:정리|요약)해\s*(?:줘|줄래|주세요)|(?:정리|요약)\s*부탁"
    r"|(?:정리|요약)\s*해\s*서\s*(?:남겨|알려|올려|공유)"
    r"|지금까지\s*(?:뭐|무엇|어디|정리|결정|나온)|어디까지\s*(?:왔|됐|진행|했)|결정\s*(?:사항|된\s*(?:것|거))\s*(?:은|이|정리|알려|뭐)"
    r"|(?:뭐|무엇)\s*(?:가|이)?\s*(?:정해졌|결정됐|남았)|남은\s*(?:질문|일|것|거)\s*(?:은|이|뭐|알려|정리)",
    re.I)
# About the thread, the channel, a person or a bot — answered from what was said, not from documents.
THREAD_RE = re.compile(
    r"이\s*스레드|스레드에서|스레드\s*(?:내용|글)|위\s*(?:글|내용|대화|메시지)|위에서|위에\s*(?:쓴|적은|올린)|앞에서|아까|방금"
    r"|(?:슬랙|채널|스레드)에\s*(?:작성|적힌|적은|쓴|올라온|올린|남긴)|이\s*채널|채널에서|여기서\s*(?:누가|뭐|무슨)|누가\s*(?:정했|말했|썼|올렸|맡|담당|했)|누구(?:야|예요|인지|한테|에게|가)"
    r"|(?:어떤|무슨)\s*일\s*(?:을|를)?\s*하|(?:하고|하는)\s*있는\s*(?:일|것|거)|뭐\s*하고\s*있|맡은\s*일|역할이?\s*(?:뭐|무엇)"
    r"|봇이|봇은|봇한테|봇의",
    re.I)
QUESTION_RE = re.compile(r"\?|까요?\s*$|나요?\s*$|니\s*$|냐\s*$|(?:있|없|알|맞|되|돼|했|됐|인)(?:어|나|니|냐|지|을까|아|어요|나요)\s*[?.]?\s*$"
                         r"|뭐|무엇|언제|어디|몇|얼마|누구|누가|어떻게|왜|어떤|무슨|인가|알려\s*줘|알려줘|궁금")


@dataclass(frozen=True)
class Route:
    intent: str
    handler: str
    reason: str
    method: str = "rules"      # rules | model | gate
    record_gap: bool = True    # a question the documents did not answer may become a knowledge gap
    analyze: bool = False      # doc_intake: read the thread's files and answer from them

    @property
    def silent(self) -> bool:
        return self.intent == SILENT


@dataclass
class Utterance:
    """What the router reads of one message. ``text`` is the routing text: the bot's own call left out, other
    mentions as "@이름" (baryon_slack_bot.Responder.command_text)."""
    text: str
    owner: bool = False             # the secretary's owner wrote it as a person (secretary_bridge)
    in_thread: bool = False         # a reply in a thread (or a thread the bot is in)
    others: list[str] = field(default_factory=list)  # names of other people or bots the message mentions
    unresolved_mentions: bool = False  # a mention could not be turned into a name
    files: bool = False             # files attached to this message
    thread_files: bool = False      # files shared earlier in the thread
    intake_enabled: bool = True
    guide_enabled: bool = True
    loop_enabled: bool = True
    secretary_thread: bool = False  # the secretary already works in this thread


# --------------------------------------------------------------------------- gate


def silence_reason(message: Message, *, mention: bool, is_dm: bool = False, held: bool = False,
                   previous: Message | None = None, last_caller: str = "", secretary_thread: bool = False,
                   owner: bool = False) -> str:
    """Why this message gets no answer ("" when it is meant for the bot). Rules only.

    ``previous`` is the visible message just before it in the thread, ``last_caller`` the person who last called
    the bot there. A plain reply (no mention) is answered when it continues the bot's own last turn — the bot spoke
    last, this is the person who called it, and one of the two is a question — or when it is the owner's reply in a
    thread the secretary works in."""
    if message.deleted:
        return "지운 글"
    if message.author.kind not in (HUMAN, USER_TOKEN_APP):
        return "내가 쓴 글" if message.from_me else "다른 봇의 글: 지시로 받지 않음"
    if not message.addressed_to_me:
        names = ", ".join("@" + m.name for m in message.addressees if not m.is_me) or "다른 사람"
        return f"다른 대상({names})에게 한 말"
    if held:
        return "끊긴 글: 작성자가 마저 쓸 때까지 기다림"
    if is_dm or mention or message.mentions_me:
        return ""
    if secretary_thread and owner:
        return ""
    if previous is not None and previous.from_me and (not last_caller or message.author.id == last_caller) \
            and (QUESTION_RE.search(previous.text_for_me.strip()[-60:]) or QUESTION_RE.search(message.text_plain)):
        return ""
    return "나를 부르지 않은 스레드 대화"


def thread_position(messages: list[Message], ts: str) -> tuple[Message | None, str]:
    """(the visible message just before ``ts``, the person who last called the bot before it)."""
    previous, caller = None, ""
    for message in messages:
        if message.ts == ts:
            break
        if message.deleted:
            continue
        if message.author.kind in (HUMAN, USER_TOKEN_APP) and message.mentions_me:
            caller = message.author.id
        previous = message
    return previous, caller


# --------------------------------------------------------------------------- intent: rules


def wants_action(text: str) -> bool:
    """A request for work (rules): "비서 …", an action verb asked for, or an errand noun with a request ending."""
    text = text or ""
    if SECRETARY_CALL.search(text):
        return True
    if ACTION_VERBS.search(text):
        return not _summary_only(text)
    return bool(ERRAND_NOUNS.search(text) and REQUEST_END.search(text.strip()))


def _summary_only(text: str) -> bool:
    """"정리해 줘" with nothing to send or make: a summary, not an errand."""
    return bool(SUMMARY_RE.search(text)) and not ERRAND_NOUNS.search(text) and not re.search(r"보내|만들어|올려|작성", text)


def classify_rules(u: Utterance) -> Route:
    text = (u.text or "").strip()
    if not text:
        if u.files:
            return Route(SILENT, NOBODY, "파일만 올림: 뒤따르는 질문을 기다림")
        return Route(DOC_QUESTION, RESPONDER, "빈 멘션: 사용법 안내")
    if u.loop_enabled:
        from loop_desk import wants_certify_list, wants_questions

        if wants_questions(text) or wants_certify_list(text):
            return Route(LOOP_COMMAND, LOOP, "loop 명령")
    if u.intake_enabled:
        from doc_intake import wants_file_reading, wants_intake

        if wants_intake(text):
            return Route(DOC_INTAKE, RESPONDER, "문서로 추가")
        if wants_file_reading(text) and (u.files or u.thread_files):
            return Route(DOC_INTAKE, RESPONDER, "스레드의 파일을 읽어 달라", analyze=True)
    if u.guide_enabled:
        from guide import wants_guide

        if wants_guide(text):
            return Route(GUIDE, RESPONDER, "사용 안내")
    return _free_form(u, text)


def _free_form(u: Utterance, text: str) -> Route:
    gap = not u.unresolved_mentions
    if u.owner and (u.secretary_thread or wants_action(text)):
        return Route(ACTION, SECRETARY, "비서의 주인이 일을 맡김" if not u.secretary_thread else "비서가 일하는 스레드의 이어진 말")
    if SUMMARY_RE.search(text) and u.in_thread:
        return Route(SUMMARY, RESPONDER, "스레드 정리 요청", record_gap=False)
    if u.others or THREAD_RE.search(text):
        who = ", ".join("@" + name for name in u.others)
        return Route(THREAD_QUESTION, RESPONDER, f"사람·봇·스레드에 관한 질문{f' ({who})' if who else ''}", record_gap=False)
    if not QUESTION_RE.search(text):
        return Route(DOC_QUESTION, RESPONDER, "질문이 아닌 말: 답은 하되 공백으로 남기지 않음", record_gap=False)
    return Route(DOC_QUESTION, RESPONDER, "문서 질문", record_gap=gap)


# --------------------------------------------------------------------------- intent: model

MODEL_PROMPT = """슬랙에서 봇(너)을 부른 글 하나를 분류한다. 아래 중 하나만 고르고 JSON 한 줄로만 답하라.

- doc_question: 회사 문서·규정·지식에서 답을 찾는 질문 ("연차휴가는 며칠이야?")
- thread_question: 이 스레드·채널에서 오간 말, 특정 사람이나 다른 봇이 하는 일·역할에 관한 질문
- summary: 이 스레드를 정리·요약해 달라는 요청 (목적, 결정, 담당, 남은 질문)
- action: 메일·초안·일정·연락·등록처럼 일을 시키는 요청 ("작성된 것"처럼 일을 시키지 않는 말은 아니다)
- silent: 봇에게 한 말이 아니거나 답할 필요가 없는 말

[스레드 앞부분]
{context}
[분류할 글]
{text}

{{"intent": "...", "reason": "한 문장"}}"""


def parse_model(answer: str) -> tuple[str, str] | None:
    match = re.search(r"\{.*\}", answer or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    intent = str(data.get("intent") or "").strip().lower()
    return (intent, str(data.get("reason") or "")[:200]) if intent in MODEL_INTENTS else None


class Router:
    """``classify(utterance, context)``: rules for commands, then the model (when ``complete`` is set) for the rest.

    ``complete(prompt) -> str`` is one model call (baryon_slack_bot uses the chat endpoint without retrieval and
    without recording a gap). Any failure falls back to the rules."""

    def __init__(self, complete: Callable[[str], str] | None = None) -> None:
        self.complete = complete

    def classify(self, u: Utterance, context: str = "") -> Route:
        rules = classify_rules(u)
        if self.complete is None or rules.intent in (LOOP_COMMAND, DOC_INTAKE, GUIDE) or not (u.text or "").strip():
            return rules
        try:
            parsed = parse_model(self.complete(MODEL_PROMPT.format(context=context[-3000:] or "(없음)", text=u.text[:2000])))
        except Exception as error:  # noqa: BLE001 - the model is optional: the rules decide
            logger.info("intent model failed (%s): using the rules", type(error).__name__)
            return rules
        if parsed is None:
            logger.info("intent model answer unusable: using the rules")
            return rules
        intent, reason = parsed
        if intent == ACTION and not u.owner:
            intent, reason = (THREAD_QUESTION if u.others else DOC_QUESTION), "일 요청이지만 비서의 주인이 아님"
        if intent == SUMMARY and not u.in_thread:
            intent = DOC_QUESTION
        handler = {ACTION: SECRETARY, SILENT: NOBODY}.get(intent, RESPONDER)
        record_gap = intent == DOC_QUESTION and rules.record_gap
        return Route(intent, handler, reason or "모델 분류", method="model", record_gap=record_gap)
