"""Thread understanding: one Slack thread snapshot in, one strict JSON reading of it out.

The loop used to read one message at a time and call anything matching a keyword list a decision ("정리해 줘" too).
This module reads the whole thread (``thread_snapshot.ThreadSnapshot`` serialized by ``understanding_payload``) and
says what it was for, who asked whom for what, what was decided and by whom, what was only claimed and by which
kind of author, which claims disagree, what is still open, and which messages were meant for the bot at all.

Every item carries ``evidence_ts`` (messages of this thread) and ``quote`` (words copied from the first of them). An
item whose evidence is not in the thread, or whose quote is not in the message it cites, is dropped (``dropped``
counts them). The same check as the loop's term extraction (loop.extract_from_chunk): the quote, spaces aside, must
be in the source text.

Sources are not equal. The owner outranks other people, and people outrank other bots: what another bot says is a
**claim** with trust ``needs_verification``, never a fact or a decision. Two claims about the same subject and
predicate with values that share no content word are a **contest** (``contests``) until a person settles it; S4 turns
contests into ontology [쟁점]. The bot's own earlier posts are not evidence for anything.

Decisions:

* ``confirmed`` only when a person (owner or human) stated it in words ("30일로 하죠", "2주가 맞아", "ryu 담당이야",
  "<@park> 님이 양식 수정 담당해 주세요") in a message that is complete. Agreement by reactions or a short "네" is an
  ``agreement_signal``; a proposal agreed that way is an ``implicit`` decision, not a confirmed one.
* A bot's summary is never a decision. A request ("정리해 줘", "링크 줘") is a ``request``, not a decision.

Who the bot should answer (``addressed_to_bot``) and which messages are cut off (``incomplete``) are always decided
by rules, also when a model reads the thread: other bots' posts and messages that open with someone else's mention
are skipped; a message without a mention is answered only when it continues the bot's own question; a message the
truncation hold waited on is ``incomplete`` and not answered until its author finishes it.

:func:`understand` asks the model when ``complete`` is given and falls back to the rules when the call fails or the
answer is not usable; :func:`understand_rules` is the deterministic path tests and CI run.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("baryon.thread")

Complete = Callable[[list[dict[str, str]]], Awaitable[str]]

VERSION = 1
HUMAN, SELF_BOT, OTHER_BOT, USER_TOKEN_APP = "human", "self_bot", "other_bot", "user_token_app"
OWNER, NEEDS_VERIFICATION = "owner", "needs_verification"
PEOPLE = (HUMAN, USER_TOKEN_APP)
TRUST_RANK = {OWNER: 3, HUMAN: 2, NEEDS_VERIFICATION: 1}
EXPLICIT, IMPLICIT = "explicit", "implicit"
AGREE_REACTIONS = {"+1", "thumbsup", "white_check_mark", "heavy_check_mark", "ok", "ok_hand", "100", "o", "raised_hands",
                   "clap", "heart", "확인", "ㅇㅋ"}
DISAGREE_REACTIONS = {"-1", "thumbsdown", "x", "no_entry", "no_entry_sign", "heavy_multiplication_x"}


# --------------------------------------------------------------------------- input


@dataclass
class Msg:
    ts: str
    author_id: str
    author_name: str
    kind: str                       # human | self_bot | other_bot | user_token_app
    text: str                       # readable text, the bot's own mention as "너(이름)"
    raw: str = ""                   # the mrkdwn as Slack sent it
    segments: list[str] = field(default_factory=list)  # plain text between markup (quotes are taken from these)
    mentions: list[dict] = field(default_factory=list)  # {"id", "name", "is_me", "is_bot"}
    addressees: list[str] = field(default_factory=list)  # ids of the mentions the message opens with
    mentions_me: bool = False
    addressed_to_me: bool = True    # slack_text.Message.addressed_to_me: False when it opens with someone else's mention
    reactions: list[dict] = field(default_factory=list)  # {"name", "users", "count"}
    edited: bool = False
    deleted: bool = False
    incomplete: bool = False        # the truncation hold waited on it, or it stops mid-sentence and was never edited
    is_owner: bool = False

    @property
    def person(self) -> bool:
        return self.kind in PEOPLE

    @property
    def trust(self) -> str:
        if self.kind == OTHER_BOT:
            return NEEDS_VERIFICATION
        if self.kind == SELF_BOT:
            return SELF_BOT
        return OWNER if self.is_owner else HUMAN


@dataclass
class Thread:
    messages: list[Msg]
    me_id: str = ""
    me_name: str = ""
    is_dm: bool = False
    owners: set[str] = field(default_factory=set)
    interactions: list[dict] = field(default_factory=list)  # button presses {"ts", "user", "message_ts", "value"}
    channel: str = ""
    thread_ts: str = ""

    @property
    def visible(self) -> list[Msg]:
        return [m for m in self.messages if not m.deleted]

    def get(self, ts: str) -> Msg | None:
        return next((m for m in self.messages if m.ts == ts), None)

    def name_of(self, user_id: str) -> str:
        for m in self.messages:
            if m.author_id == user_id:
                return m.author_name
            for mention in m.mentions:
                if mention.get("id") == user_id and mention.get("name"):
                    return mention["name"]
        return user_id


def parse_thread(payload: dict) -> Thread:
    """The JSON payload (``thread_snapshot.understanding_payload``) → :class:`Thread`. Unknown fields are ignored."""
    me = payload.get("me") or {}
    messages = []
    for item in payload.get("messages") or []:
        if not isinstance(item, dict) or not item.get("ts"):
            continue
        messages.append(Msg(
            ts=str(item["ts"]), author_id=str(item.get("author_id") or ""), author_name=str(item.get("author_name") or item.get("author_id") or ""),
            kind=str(item.get("author_kind") or HUMAN), text=str(item.get("text") or ""), raw=str(item.get("raw") or ""),
            segments=[str(s) for s in item.get("segments") or []], mentions=[m for m in item.get("mentions") or [] if isinstance(m, dict)],
            addressees=[str(a) for a in item.get("addressees") or []], mentions_me=bool(item.get("mentions_me")),
            addressed_to_me=bool(item.get("addressed_to_me", item.get("mentions_me") or not item.get("addressees"))),
            reactions=[r for r in item.get("reactions") or [] if isinstance(r, dict)], edited=bool(item.get("edited")),
            deleted=bool(item.get("deleted")), incomplete=bool(item.get("incomplete"))))
    messages.sort(key=lambda m: float(m.ts) if re.fullmatch(r"\d+(\.\d+)?", m.ts) else 0.0)
    owners = {str(o) for o in payload.get("owners") or [] if o}
    if not owners:  # nobody named: the person who started the thread is its owner
        first = next((m for m in messages if m.person and not m.deleted), None)
        owners = {first.author_id} if first else set()
    for m in messages:
        m.is_owner = m.person and m.author_id in owners
    return Thread(messages=messages, me_id=str(me.get("id") or ""), me_name=str(me.get("name") or ""), is_dm=bool(payload.get("is_dm")),
                  owners=owners, interactions=[i for i in payload.get("interactions") or [] if isinstance(i, dict)],
                  channel=str(payload.get("channel") or ""), thread_ts=str(payload.get("thread_ts") or ""))


# --------------------------------------------------------------------------- text helpers


def norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


MENTION_RENDER = re.compile(r"@[^\s,.:;!?()]+|너\([^)]*\)|<[^>]*>")
STOP = {"있습니다", "합니다", "입니다", "있어요", "해요", "이에요", "예요", "됩니다", "있다", "한다", "이다", "현재", "지금", "바로",
        "제가", "저는", "우리", "그리고", "그래서", "다만", "있는", "하는", "되는", "것이", "것은", "수가", "정도", "님이", "님은", "님만"}


def words(text: str) -> set[str]:
    """Content words of two or more characters, with common Korean endings cut ("담당입니다" meets "담당")."""
    found = set()
    for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", str(text or "").lower()):
        stem = re.sub(r"(입니다|합니다|이에요|예요|이야|이고|이며|이다|에서|으로|에게|한테|부터|까지|하고|이랑|은|는|이|가|을|를|의|에|로|와|과|도|만|님)$", "", word)
        if len(stem) >= 2 and stem not in STOP and word not in STOP:
            found.add(stem)
    return found


def sentences(text: str) -> list[str]:
    """Lines, then sentences ("…다. …요. …?"), without list bullets."""
    out = []
    for line in str(text or "").split("\n"):
        line = re.sub(r"^\s*(?:[•·\-*>]|\d+[.)])\s*", "", line).strip()
        for part in re.split(r"(?<=[.!?])\s+|(?<=[요다죠])\s{2,}|\s+(?=:[a-z0-9_+-]+:\s)", line):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _unmention(text: str, message: Msg) -> list[str]:
    """``text`` cut at every rendered mention ("@dev bot", "너(baryon)"): the pieces Slack sent as plain text."""
    marked = text or ""
    for name in sorted({str(m.get("name") or "") for m in message.mentions} - {""}, key=len, reverse=True):
        marked = marked.replace("@" + name, "\x00")
    marked = MENTION_RENDER.sub("\x00", marked)
    return marked.split("\x00")


def anchor(text: str, message: Msg) -> str:
    """The longest piece of ``text`` (cut at rendered mentions) copied word for word from the message's plain text,
    so it is also found in the raw Slack text. "" when nothing is."""
    pool = [norm(s) for s in (message.segments or [message.raw or message.text]) if s.strip()]
    best = ""
    for piece in _unmention(text, message):
        piece = piece.strip(" \t,.:;")
        if len(piece) >= 2 and len(piece) > len(best) and any(norm(piece) in s for s in pool):
            best = piece
    if not best:  # longer than one segment (a table cell across lines): shrink from the right until it fits
        candidate = (text or "").strip()
        while len(candidate) > 3 and not any(norm(candidate) in s for s in pool):
            candidate = candidate[:-1].rstrip()
        best = candidate if len(candidate) > 3 else ""
    return best


def quote_in(quote: str, message: Msg) -> bool:
    """The quote, spaces aside, is in the message (its readable text, its plain segments or its raw mrkdwn)."""
    q = norm(quote)
    return bool(q) and any(q in norm(source) for source in [message.text, message.raw, *message.segments] if source)


# --------------------------------------------------------------------------- rule patterns

QUESTION_END = re.compile(r"(?:\?|(?<![니으])까요?|나요?|니\??|래\??|할지|건가요?|인가요?|알아|아세요|있나|있어\?)\s*$")
DEFER_RE = re.compile(r"아직\s*(?:안|못)\s*정|안\s*정했|미정|다음\s*(?:회의|번|주)에?\s*(?:서\s*)?정|나중에\s*정|정해지면|보류\s*중")
SCHEDULED_DEFER = re.compile(r"다음\s*(?:회의|번|주)|나중에")
REQUEST_RE = re.compile(r"(?:해|줘|좀)\s*줘|해줘|줄래|주세요|줄\s*수\s*있|해\s*줄|부탁|올려\s*줘|남겨\s*(?:둬|줘|주세요)|알려\s*줘|"
                        r"정리해|찾아\s*줘|보내\s*줘|[가-힣]+\s*줘[.!]?$|둬[.!]?$|할\s*수\s*있어\??$")
ASSIGN_RE = re.compile(r"@(?P<who>[^\s@]+?)\s*(?:님)?\s*(?:이|가|께서|님이)?\s+(?P<what>[^.?]*?)\s*(?:담당(?:해|을\s*맡|하)|맡아\s*(?:줘|주세요))")
EXPLICIT_RE = re.compile(r"(?:으로|로)\s*(?:하죠|하자|합시다|갑시다|가자|가죠|정했|정합니다|정할게요|결정|확정)|하자\b|하자[.!]?$|하기로\s*(?:했|함|하죠|해요)|"
                         r"합시다|갑시다|정했(?:어|습니다|다|어요)|결정(?:했|합니다|입니다|이에요)|확정(?:합니다|이에요|입니다|할게|했)|"
                         r"[가-힣]{1,4}자[.!]?$|해야\s*(?:하|한다|해|돼|된다|합니다|할)|(?:이|가)\s*맞아|(?:이|가)\s*맞습니다|^맞아\S*\s+\S|"
                         r"담당(?:이야|입니다|이에요|이다|으로\s*하)")
INTENT_RE = re.compile(r"해야겠|해야\s*할\s*것\s*같|하는\s*게\s*좋겠")
PROPOSAL_RE = re.compile(r"어떨까요|어때요?\??$|하면\s*좋겠|하는\s*게\s*어때|할까요\?|하죠\?|면\s*어떨")
AGREE_RE = re.compile(r"^(?:네|넵|예|좋아요?|좋습니다|동의(?:합니다|해요)?|찬성|오케이|ok|ㅇㅋ|ㅇㅇ|그래요?|맞아요?|맞습니다|알겠습니다|확인했습니다)\b", re.I)
HANDOVER_RE = re.compile(r"여기\s*내용|넘길게|넘겨|넘깁니다|맡길게|맡겨|인계|전달할게|이어서\s*(?:해|봐)|가져가")
COMMIT_RE = re.compile(r"제가\s+\S.*?(?:게요|게|겠습니다)[.!]?$")
DEADLINE_RE = re.compile(r"(?P<when>(?:\d{1,2}월\s*)?\d{1,2}일|[월화수목금토일]요일|이번\s*주|다음\s*주|오늘|내일|모레|월말|주말)\s*(?:까지|전에|안에|중)")
REASON_RE = re.compile(r"(?:니까|으니|라서|이라서|어서|아서|해서|여서|때문|면\s|이면|하면|에만|후에만|만\s*유효|^단\s|^다만\s|경우)")
TOPIC_MARK = re.compile(r"(?<=\S)(?:은|는)\s+")
ADVERBIAL = re.compile(r"(?:으로|로|에서|에|에게|한테|께|부터|까지|쪽|때|중|후|전|동안|으로는|로는|에서는|에는|에선|엔)[,]?$")
CONTAINS_RE = re.compile(r"(?P<s>[가-힣A-Za-z0-9]{2,12})(?:에는|엔)\s+(?:(?:꼭|반드시|항상|늘)\s+)?(?P<o>[가-힣A-Za-z0-9]{2,12}?)(?:을|를)\s+"
                         r"(?:(?:꼭|반드시|항상|늘|같이|함께)\s+)?(?P<p>적|기재|포함|넣|첨부|표시)")
CONTAINS_VERB = {"적": "적는다", "기재": "기재한다", "포함": "포함한다", "넣": "넣는다", "첨부": "첨부한다", "표시": "표시한다"}
ROLE_OF = re.compile(r"^(?P<s>.+?)의\s*(?P<r>역할|담당|정체)$")
ROLE_WORDS = ("담당", "역할", "맡은 일", "하는 일", "정체", "담당자")
UNKNOWN_RE = re.compile(r"정해진\s*(?:것|게)\s*없|기록에\s*없|모르|알\s*수\s*없|확인\s*(?:하지\s*)?못|없습니다$|없어요$|미정")
META_VALUE = re.compile(r"주시면|하겠습니다$|답하겠|답하지\s*않|드리겠|게요[.!]?$|할게[.!]?$|정할\s*일|정하실\s*일|\?$")


def _clean_subject(subject: str) -> str:
    subject = re.sub(r"^(?:정리|결론|참고|요약|메모|단|다만|그리고|그럼|근데|제\s*기록으로|기록상)\s*[:：]?\s*", "", subject.strip())
    subject = re.sub(r"^[^:：]{0,20}[:：]\s*", "", subject)  # "2번 항목: helper" → "helper"
    return subject.strip(" \"'“”‘’`*_")


def _split_alias(subject: str) -> tuple[str, list[str]]:
    """"토끼(Tokki)" → ("토끼", ["Tokki"]); "일단해봐. (run-bot)" → ("일단해봐", ["run-bot"])."""
    aliases = [a.strip() for a in re.findall(r"\(([^)]{1,30})\)", subject) if a.strip()]
    base = re.sub(r"\([^)]*\)", "", subject).strip(" .")
    return (base or (aliases[0] if aliases else subject)), aliases


def _role_split(subject: str, value: str, role: str | None) -> tuple[str, str, str]:
    """(subject, predicate, value): "고객 지원 담당은 ryu 님" and "고객 지원은 song 님 담당입니다" both become
    (고객 지원, 역할, …); without a role word the predicate is "" (what the subject is)."""
    predicate = "역할" if role else ""
    for word in ROLE_WORDS:
        if subject.endswith(" " + word) or (subject.endswith(word) and len(subject) > len(word) + 1 and word == "담당"):
            subject, predicate = subject[: -len(word)].strip(), "역할"
            break
    core = re.sub(r"\s*\([^)]*\)\s*[.!]?$", "", value.strip())  # "… 실행 담당(지시 007·008)"
    stripped = re.sub(r"\s*(?:담당|역할)\s*(?:입니다|이에요|이야|이다|예요)?[.!]?$", "", core).strip()
    if stripped != core and stripped:
        value, predicate = stripped, "역할"
    value = re.sub(r"\s*(?:입니다|이에요|이야|이다|예요|입니다\.)[.!]?$", "", value.strip()).strip(" .")
    return subject, predicate, value


def _subject_key(subject: str) -> str:
    return re.sub(r"[\s.·・]+", " ", subject.lower()).strip()


# --------------------------------------------------------------------------- the rule reading


def _item(ts: str | list[str], quote: str, **fields: Any) -> dict:
    return {**fields, "evidence_ts": [ts] if isinstance(ts, str) else list(ts), "quote": quote}


def _reaction_signals(thread: Thread, message: Msg) -> tuple[list[dict], list[str]]:
    """Agreement signals on a message: reactions (agree) and short replies by other people ("네", "좋아요")."""
    signals, agreers = [], []
    for reaction in message.reactions:
        name = str(reaction.get("name") or "").split("::")[0]
        if name not in AGREE_REACTIONS and name not in DISAGREE_REACTIONS:
            continue
        for user in reaction.get("users") or []:
            if user == message.author_id or user == thread.me_id:
                continue
            signals.append({"type": "reaction", "value": name, "by": thread.name_of(user), "by_id": user, "ts": message.ts,
                            "agree": name in AGREE_REACTIONS})
            if name in AGREE_REACTIONS:
                agreers.append(thread.name_of(user))
    return signals, agreers


def _table_rows(block: str) -> list[list[str]]:
    """A text table in a code block ("누구   맡은 일 / ──── / run-bot   배포 관리") → rows of cells, header first.
    Columns are separated by two or more spaces. Rows flattened onto one line (copied text) are split again:
    the first cell of each row is the last word before the next column gap."""
    inner = block.strip("`").strip()
    lines = [line for line in inner.split("\n") if line.strip()]
    separator = re.compile(r"^[\s─━\-=|+]{3,}$")
    if len(lines) > 2:
        rows = [re.split(r"\s{2,}", line.strip()) for line in lines if not separator.match(line.strip())]
        return [row for row in rows if len(row) >= 2]
    cells = [c for c in re.split(r"\s{2,}", inner) if c.strip()]
    header = []
    while cells and not re.fullmatch(r"[─━\-=|+\s]+", cells[0]):
        head = cells.pop(0)
        if re.search(r"[─━]{3,}", head):  # "맡은 일 ────": the separator glued to the last header cell
            header.append(re.split(r"\s*[─━]{3,}", head)[0].strip())
            break
        header.append(head)
    while cells and re.fullmatch(r"[─━\-=|+\s]+", cells[0]):
        cells.pop(0)
    width = len(header)
    if width < 2 or not cells:
        return []
    rows, row = [header], []
    for index, cell in enumerate(cells):
        row.append(cell)
        if len(row) == width:
            last = row[-1]
            if index < len(cells) - 1 and " " in last:  # "배포 관리 다람쥐": the next row's first cell rode along
                row[-1], carried = last.rsplit(" ", 1)
                rows.append(row)
                row = [carried]
            else:
                rows.append(row)
                row = []
    return [r for r in rows if len(r) == width]


def _claims_of(message: Msg) -> list[dict]:
    """What a message states about something: "X은/는 Y" sentences and text tables (subject column → value)."""
    claims = []
    for block in re.findall(r"```[\s\S]*?```", message.text):
        rows = _table_rows(block)
        if len(rows) < 2:
            continue
        header = rows[0]
        for row in rows[1:]:
            subject, aliases = _split_alias(_clean_subject(row[0]))
            for column, value in zip(header[1:], row[1:]):
                predicate = "역할" if any(w in column for w in ROLE_WORDS) or column in ("일", "누구") else column.strip()
                value = value.strip()
                if subject and value and len(subject) <= 30:
                    quote = anchor(value, message)
                    claims.append(_item(message.ts, quote, subject=subject, aliases=aliases, predicate=predicate, value=value))
    text = re.sub(r"```[\s\S]*?```", " ", message.text)
    for sentence in sentences(text):
        if QUESTION_END.search(sentence):
            continue
        for clause in re.split(r",\s+(?=[^,]{1,30}?(?:은|는)\s)", sentence):
            clause = _clean_subject(re.sub(r"^(?::[a-z0-9_+-]+:\s*)+", "", clause.strip()))
            topic = _topic_split(clause)
            if not topic:
                continue
            subject, role, value = topic
            subject, aliases = _split_alias(_clean_subject(subject))
            if not subject or len(subject) > 24:
                continue
            subject, predicate, value = _role_split(subject, value, role)
            if not subject or not value or META_VALUE.search(value) or len(value) > 160:
                continue
            quote = anchor(clause, message)
            if quote:
                claims.append(_item(message.ts, quote, subject=subject, aliases=aliases, predicate=predicate, value=value))
    return claims


def _topic_split(clause: str) -> tuple[str, str | None, str] | None:
    """"제 기록으로는 고객 지원은 song 님 담당입니다" → ("고객 지원", None, "song 님 담당입니다"): the topic (은/는) near
    the start of the clause, after any adverbial ("기록으로는", "회의에서"). None when the clause has no such topic."""
    for mark in TOPIC_MARK.finditer(clause):
        head = clause[: mark.start()].split()
        if not head or ADVERBIAL.search(head[-1]):
            continue
        start = 0
        for index, word in enumerate(head[:-1]):
            if ADVERBIAL.search(word) or word.endswith((",", ":")):
                start = index + 1
        subject_words = head[start:]
        if len(subject_words) > 3:
            return None
        last = re.sub(r"\([^)]*\)$", "", subject_words[-1])
        if len(last) < 2 and not re.search(r"[A-Za-z0-9]", last):
            return None  # "있는", "하는": a verb, not a topic
        if len(subject_words) > 1 and re.search(r"[가-힣](?:이|가|을|를)$", subject_words[0]):
            return None  # "작성된것을 보고 이해는": a clause, not a name
        if any(re.search(r"(?:려면|으면|면서|지만|는데|다|라서)$", w) for w in subject_words) or \
                re.search(r"[가-힣](?:오|하|되|있|없|보|주)$", last):
            return None  # "올라오는 글은", "넣으려면 그 부분은": a verb phrase, not a name
        subject = " ".join(subject_words)
        role = None
        match = ROLE_OF.match(subject)
        if match:
            subject, role = match.group("s"), match.group("r")
        value = clause[mark.end():].strip()
        return (subject, role, value) if value else None
    return None


def understand_rules(thread: Thread) -> dict:
    """The deterministic reading: patterns over sentences, reactions and mentions (Korean first). Its items go
    through :func:`finalize` like the model's."""
    out: dict[str, Any] = {key: [] for key in ("decisions", "requests", "assumptions", "owners", "deadlines", "relations", "claims",
                                               "open_questions", "entities")}
    last_decision: dict | None = None
    proposal: tuple[Msg, str, str] | None = None  # a proposal nobody has agreed to yet
    for message in thread.visible:
        if message.kind == SELF_BOT:
            continue
        claims = _claims_of(message)
        out["claims"] += claims
        conditions = [(f"{c['subject']} {c['value']}", c["quote"]) for c in claims if REASON_RE.search(c["value"])]
        if message.kind == OTHER_BOT:  # another bot's words are claims, never decisions or requests
            conditions += [(sentence, anchor(sentence, message)) for sentence in sentences(re.sub(r"```[\s\S]*?```", " ", message.text))
                           if REASON_RE.search(sentence) and not QUESTION_END.search(sentence) and not META_VALUE.search(sentence)]
        seen = {norm(a["quote"]) for a in out["assumptions"]}
        for statement, quote in conditions:  # rules stated as conditions ("…확인 후에만 진행합니다")
            if quote and norm(quote) not in seen:
                seen.add(norm(quote))
                out["assumptions"].append(_item(message.ts, quote, statement=statement, by=message.author_name))
        if message.kind == OTHER_BOT:
            continue
        first_sentence = True
        for sentence in sentences(message.text):
            opening, first_sentence = first_sentence, False
            quote = anchor(sentence, message)
            if not quote:
                continue
            is_question = bool(QUESTION_END.search(sentence))
            to_other = opening and not message.addressed_to_me
            explicit = bool(EXPLICIT_RE.search(sentence)) and not is_question

            if DEFER_RE.search(sentence):  # "아직 안 정했어", "다음 회의에서 정하겠습니다": open, not decided
                out["open_questions"].append(_item(message.ts, quote, question=sentence, about=_topic(sentence),
                                                   ask=not SCHEDULED_DEFER.search(sentence)))
                continue
            assign = None if is_question else ASSIGN_RE.search(sentence)
            if assign:  # "@park 님이 양식 수정 담당해 주세요": a person gives someone the work — a decision
                who, what = assign.group("who"), assign.group("what").strip() or "일"
                when = DEADLINE_RE.search(message.text)
                statement = f"{who} 담당: {what}" + (f" ({when.group('when')}까지)" if when else "")
                last_decision = _item(message.ts, quote, statement=statement, decided_by=[message.author_name], kind=EXPLICIT)
                out["decisions"].append(last_decision)
                out["owners"].append(_item(message.ts, quote, who=who, what=what))
                out["relations"].append(_item(message.ts, quote, s=who, p="담당", o=what))
                if when:
                    out["deadlines"].append(_item(message.ts, anchor(when.group(0), message) or quote, what=what, when=when.group("when")))
                continue
            if to_other and HANDOVER_RE.search(sentence):  # "<@other-bot> 여기 내용이다": the thread is handed over
                target = next((str(m.get("name") or "") for m in message.mentions if m.get("id") in message.addressees), "")
                out["decisions"].append(_item(message.ts, quote, statement=f"이 스레드 내용을 @{target}에게 넘김: {sentence}",
                                              decided_by=[message.author_name], kind=IMPLICIT, handover_to=target))
                out["owners"].append(_item(message.ts, quote, who=target, what="이 스레드"))
                continue
            if (REQUEST_RE.search(sentence) and not explicit) or (is_question and (message.mentions_me or to_other)):
                to = "너" if message.mentions_me else next((str(m.get("name") or "") for m in message.mentions
                                                           if m.get("id") in message.addressees), "") or "스레드"
                out["requests"].append(_item(message.ts, quote, by=message.author_name, to=to, what=sentence,
                                             status=_request_status(thread, message, to)))
                continue
            if PROPOSAL_RE.search(sentence) or (opening and message.reactions and not explicit):
                signals, agreers = _reaction_signals(thread, message)
                if agreers:  # "목요일 4시로 고정하면 어떨까요?" + 👍 ✅: agreed without words
                    last_decision = _item(message.ts, quote, statement=sentence, kind=IMPLICIT, agreement_signals=signals,
                                          decided_by=list(dict.fromkeys([message.author_name, *agreers])))
                    out["decisions"].append(last_decision)
                    continue
                if PROPOSAL_RE.search(sentence):
                    proposal = (message, sentence, quote)
                    continue
            if is_question:
                continue
            for contains in CONTAINS_RE.finditer(sentence):  # "견적에는 납기를 꼭 적어요": what a document carries
                out["relations"].append(_item(message.ts, anchor(contains.group(0), message) or quote, s=contains.group("s"),
                                              p=CONTAINS_VERB[contains.group("p")], o=contains.group("o")))
            if AGREE_RE.search(sentence) and len(sentence) <= 24 and not explicit:  # "네 확정으로 알겠습니다"
                signal = {"type": "reply", "value": sentence, "by": message.author_name, "by_id": message.author_id,
                          "ts": message.ts, "agree": True}
                if proposal is not None and proposal[0].author_id != message.author_id:
                    source, text, source_quote = proposal
                    last_decision = _item(source.ts, source_quote, statement=text, kind=IMPLICIT, agreement_signals=[signal],
                                          decided_by=[source.author_name, message.author_name])
                    last_decision["evidence_ts"].append(message.ts)
                    out["decisions"].append(last_decision)
                    proposal = None
                elif last_decision is not None:
                    last_decision.setdefault("agreement_signals", []).append(signal)
                continue
            if explicit:
                last_decision = _item(message.ts, quote, statement=sentence, decided_by=[message.author_name], kind=EXPLICIT)
                signals, _ = _reaction_signals(thread, message)
                if signals:
                    last_decision["agreement_signals"] = signals
                out["decisions"].append(last_decision)
            elif INTENT_RE.search(sentence):  # "납기 칸을 추가해야겠네요": decided by doing, not said as a decision
                last_decision = _item(message.ts, quote, statement=sentence, decided_by=[message.author_name], kind=IMPLICIT)
                out["decisions"].append(last_decision)
            commit = COMMIT_RE.search(sentence)
            if commit:  # "견적서 양식은 제가 고칠게요": who will do it
                out["owners"].append(_item(message.ts, quote, who=message.author_name,
                                           what=re.sub(r"\s*제가\s+", " ", sentence).strip(" .")))
            if REASON_RE.search(sentence) and not explicit:
                out["assumptions"].append(_item(message.ts, quote, statement=sentence, by=message.author_name))

    out["purpose"] = _purpose(thread)
    return out


def _purpose(thread: Thread) -> dict:
    """What the thread is for: what the owner says it must achieve ("…이해는 해야 하는데 …"), else the first
    person's opening sentence."""
    for message in thread.visible:
        if not message.is_owner:
            continue
        for sentence in sentences(message.text):
            if re.search(r"해야\s*(?:하|한|해|돼)|목적은|목표는|하려는\s*(?:건|것)|원하는\s*(?:건|것)", sentence):
                quote = anchor(sentence, message)
                if quote:
                    return _item(message.ts, quote, text=sentence, source="owner")
    root = next((m for m in thread.visible if m.person), None)
    if root is None:
        return {}
    first = next((s for s in sentences(root.text) if anchor(s, root)), "")
    return _item(root.ts, anchor(first, root), text=first) if first else {}


def _topic(sentence: str) -> str:
    match = re.match(r"^(?P<t>[^\s]+(?:\s+[^\s]+){0,2}?)\s*(?:은|는|이|가)?\s*(?:얘기|건|기준)?(?:은|는)\s", sentence)
    return (match.group("t") if match else " ".join(sentence.split()[:2])).strip()


def _request_status(thread: Thread, message: Msg, to: str) -> str:
    after = [m for m in thread.visible if float(m.ts) > float(message.ts)]
    if to == "너":
        return "answered" if any(m.kind == SELF_BOT for m in after) else "open"
    return "answered" if any(m.author_name == to for m in after) else "open"


# --------------------------------------------------------------------------- the model reading

PROMPT = """아래는 Slack 스레드 하나다. 각 줄은 [ts] 작성자 (종류): 내용 이다. 종류: owner(오너), human(사람), other_bot(다른 봇), self_bot(너).
'너(이름)'은 너(이 스레드를 읽는 봇) 자신이다. [반응]은 그 글에 눌린 이모지와 누른 사람이다.

스레드를 처음부터 끝까지 읽고 아래 JSON 하나만 출력하라. 모든 항목에는 근거가 된 글의 ts(evidence_ts)와,
그 글에서 **글자 그대로 복사한** 짧은 인용(quote)을 넣는다. 인용을 고쳐 쓰거나 지어내면 그 항목은 버려진다.

규칙
- 모든 값(statement, subject, value, what …)은 스레드 원문의 언어 그대로 쓴다. 번역하지 않는다.
- decisions: 사람(owner/human)이 정한 것만. 사람이 "~로 하죠/하자", "~가 맞아"(앞서 나온 값을 확인), "~해야 한다"(지시),
  "@누구 여기 내용이다"(스레드를 넘김)라고 한 것은 결정이다. 다른 봇이나 너(self_bot)가 정리한 내용은 결정이 아니다.
  "해 줘", "정리해 줘", "링크 줘" 같은 부탁은 결정이 아니라 requests에 넣는다. 단, 누구에게 일을 맡기는 말("@A 님이 X 담당해 주세요")은 결정이다.
  kind: 말로 분명히 정했으면 explicit, 반응·짧은 동의·행동으로만 정해졌으면 implicit.
  agreement_signals: 동의를 나타낸 반응·짧은 답(누가, 무엇).
- claims: 누가 무엇에 대해 사실이라고 말한 것(주어 subject, 술어 predicate, 값 value). 다른 봇의 말도 여기에 넣는다.
- open_questions: 아직 정해지지 않았거나 확인이 필요한 것. 이미 스레드 안에 답이 있는 것은 넣지 않는다.
- purpose: 이 스레드가 이루려는 것 한 문장(오너가 바로잡은 말이 있으면 그것을 따른다).

{{"purpose": {{"text": "...", "evidence_ts": ["..."], "quote": "..."}},
 "requests": [{{"by": "...", "to": "...", "what": "...", "status": "open|answered|done", "evidence_ts": ["..."], "quote": "..."}}],
 "decisions": [{{"statement": "...", "decided_by": ["..."], "kind": "explicit|implicit", "agreement_signals": [{{"type": "reaction|reply", "by": "...", "value": "..."}}], "evidence_ts": ["..."], "quote": "..."}}],
 "assumptions": [{{"statement": "...", "by": "...", "evidence_ts": ["..."], "quote": "..."}}],
 "owners": [{{"who": "...", "what": "...", "evidence_ts": ["..."], "quote": "..."}}],
 "deadlines": [{{"what": "...", "when": "...", "evidence_ts": ["..."], "quote": "..."}}],
 "entities": [{{"name": "...", "type": "person|bot|service|project|term|other", "evidence_ts": ["..."], "quote": "..."}}],
 "relations": [{{"s": "...", "p": "...", "o": "...", "evidence_ts": ["..."], "quote": "..."}}],
 "claims": [{{"subject": "...", "predicate": "...", "value": "...", "evidence_ts": ["..."], "quote": "..."}}],
 "open_questions": [{{"question": "...", "about": "...", "evidence_ts": ["..."], "quote": "..."}}]}}

[스레드]
{lines}"""

MAX_PROMPT_CHARS = 24000


def thread_lines(thread: Thread, limit: int = MAX_PROMPT_CHARS) -> str:
    lines = []
    for message in thread.visible:
        kind = OWNER if message.is_owner else message.kind
        text = re.sub(r"\s+\n", "\n", message.text)[:2500]
        line = f"[{message.ts}] {message.author_name} ({kind}): {text}"
        reactions = ", ".join(f":{r.get('name')}: {' '.join(thread.name_of(u) for u in r.get('users') or [])}".strip()
                              for r in message.reactions)
        if reactions:
            line += f"\n  [반응] {reactions}"
        lines.append(line)
    text = "\n".join(lines)
    return text if len(text) <= limit else text[-limit:]


def _json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    try:
        value = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        try:
            value = json.loads(match.group(0)) if match else {}
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


async def understand_model(thread: Thread, complete: Complete) -> dict:
    """The model's reading (raw, before :func:`finalize`). Raises when the answer is not a usable JSON object."""
    data = _json(await complete([{"role": "user", "content": PROMPT.format(lines=thread_lines(thread))}]))
    if not data or not any(isinstance(data.get(key), list) for key in ("decisions", "requests", "claims")):
        raise ValueError("the model did not return the thread JSON")
    out: dict[str, Any] = {}
    for key in ("decisions", "requests", "assumptions", "owners", "deadlines", "entities", "relations", "claims", "open_questions"):
        out[key] = [dict(item) for item in data.get(key) or [] if isinstance(item, dict)]
    purpose = data.get("purpose")
    out["purpose"] = purpose if isinstance(purpose, dict) else ({"text": str(purpose)} if purpose else {})
    for decision in out["decisions"]:
        if isinstance(decision.get("decided_by"), str):
            decision["decided_by"] = [decision["decided_by"]]
    return out


# --------------------------------------------------------------------------- policy, evidence and contests


def _evidence(thread: Thread, item: dict) -> tuple[list[str], str, Msg | None] | None:
    """Valid evidence for an item: the ts that are messages of this thread, and the quote anchored in one of them."""
    ts_list = [str(ts) for ts in item.get("evidence_ts") or [] if thread.get(str(ts)) and not thread.get(str(ts)).deleted]
    if not ts_list:
        return None
    quote = str(item.get("quote") or "").strip()
    for ts in ts_list:
        message = thread.get(ts)
        if quote and quote_in(quote, message):
            return ts_list, anchor(quote, message) or quote, message
    return None


def _compatible(a: str, b: str) -> bool:
    """Two values that share a content word refine each other ("배포 실행 담당" / "실행 관리"); none shared, or a
    concrete value against "정해진 것이 없음", disagree."""
    if bool(UNKNOWN_RE.search(a)) != bool(UNKNOWN_RE.search(b)):
        return False
    return bool(words(a) & words(b)) or norm(a) in norm(b) or norm(b) in norm(a)


def _same_subject(a: dict, b: dict) -> bool:
    names_a = {_subject_key(n) for n in [a["subject"], *a.get("aliases", [])] if n}
    names_b = {_subject_key(n) for n in [b["subject"], *b.get("aliases", [])] if n}
    if names_a & names_b:
        return True
    for x in names_a:  # "환불" and "환불 승인": the same topic, one word more specific
        for y in names_b:
            wx, wy = x.split(), y.split()
            short, long = (wx, wy) if len(wx) <= len(wy) else (wy, wx)
            if short and long[: len(short)] == short:
                return True
    return False


def _slot(predicate: str) -> str:
    """What a claim is about: "X는 Y" (what X is) and "X의 역할은 Y" (what X does) answer the same question."""
    return "역할" if predicate in ("", "역할") else predicate


def _settled(a: dict, b: dict, subject: str, role: bool, said_by_people: list[tuple[str, str]]) -> bool:
    """A person repeated one side after both were said ("맞아 이번 달은 ryu 담당이야")."""
    after = max(float(a["evidence_ts"][0]), float(b["evidence_ts"][0]))
    return any(float(ts) > after and (words(a["value"]) | words(b["value"])) & words(text)
               and (words(subject) & words(text) or (role and re.search(r"담당|역할|맡", text)))
               for text, ts in said_by_people)


def _outranked(a: dict, b: dict) -> bool:
    """A person said it after a bot did: the person's word stands (the bot's claim stays unverified)."""
    first, last = sorted((a, b), key=lambda claim: float(claim["evidence_ts"][0]))
    return last["author_kind"] in PEOPLE and first["author_kind"] not in PEOPLE


def _contests(claims: list[dict], decisions: list[dict]) -> list[dict]:
    """Claims about the same subject and predicate, from different authors, whose values disagree, and that no
    person settled (a person's decision or claim repeating one of the values: "맞아 이번 달은 ryu 담당이야")."""
    groups: list[list[dict]] = []
    for claim in claims:
        for group in groups:
            if _slot(group[0]["predicate"]) == _slot(claim["predicate"]) and _same_subject(group[0], claim):
                group.append(claim)
                break
        else:
            groups.append([claim])
    said_by_people = [(d["statement"], d["evidence_ts"][0]) for d in decisions] + \
                     [(f"{c['subject']} {c['value']}", c["evidence_ts"][0]) for c in claims if c["author_kind"] in PEOPLE]
    contests = []
    for group in groups:
        role = _slot(group[0]["predicate"]) == "역할"

        pairs = [(a, b) for i, a in enumerate(group) for b in group[i + 1:]
                 if (a["by_id"] or a["by"]) != (b["by_id"] or b["by"]) and not _compatible(a["value"], b["value"])
                 and not _settled(a, b, group[0]["subject"], role, said_by_people) and not _outranked(a, b)]
        if not pairs:
            continue
        involved = {id(claim) for pair in pairs for claim in pair}
        index = len(contests)
        contests.append({"subject": group[0]["subject"], "predicate": group[0]["predicate"], "status": "needs_owner",
                         "values": [{"value": c["value"], "by": c["by"], "trust": c["trust"], "evidence_ts": c["evidence_ts"]}
                                    for c in group if id(c) in involved],
                         "evidence_ts": sorted({ts for c in group for ts in c["evidence_ts"]}, key=float), "quote": group[0]["quote"]})
        for claim in group:
            claim["contest"] = index
    return contests


def addressed_to_bot(thread: Thread) -> tuple[list[dict], list[dict]]:
    """Per message: respond or skip, and why; and the messages that are cut off. Rules only (also on the model path)."""
    decisions, incomplete = [], []
    previous: Msg | None = None
    last_caller = ""
    for message in thread.messages:
        reply = {"ts": message.ts, "action": "skip", "reason": ""}
        if message.deleted:
            reply["reason"] = "지운 글"
        elif message.kind == SELF_BOT:
            reply["reason"] = "내가 쓴 글"
        elif message.kind == OTHER_BOT:
            reply["reason"] = "다른 봇의 글: 지시로 받지 않고 주장으로만 읽음"
        elif message.incomplete:
            reply["reason"] = "끊긴 글: 작성자가 마저 쓸 때까지 기다림"
            incomplete.append({"ts": message.ts, "reason": "문장이 중간에 끊김", "evidence_ts": [message.ts],
                               "quote": anchor(message.text[-40:], message)})
        elif thread.is_dm:
            reply.update(action="respond", reason="DM")
        elif message.mentions_me:
            reply.update(action="respond", reason="나를 불렀음")
        elif not message.addressed_to_me:
            names = ", ".join("@" + thread.name_of(a) for a in message.addressees) or "다른 사람"
            reply["reason"] = f"다른 대상({names})에게 한 말"
        elif previous is not None and previous.kind == SELF_BOT and QUESTION_END.search(previous.text.strip()) \
                and (not last_caller or message.author_id == last_caller):
            reply.update(action="respond", reason="내 질문에 이어지는 답")
        else:
            reply["reason"] = "나를 부르지 않은 스레드 대화"
        if message.person and message.mentions_me and not message.deleted:
            last_caller = message.author_id
        decisions.append(reply)
        if not message.deleted:
            previous = message
    return decisions, incomplete


def _confirmations(thread: Thread) -> list[dict]:
    """Button presses on the bot's own cards (응/아니): the person's answer to that card only."""
    out = []
    for press in thread.interactions:
        card = thread.get(str(press.get("message_ts") or ""))
        if card is None or card.kind != SELF_BOT:
            continue
        value = str(press.get("value") or press.get("action_id") or "").lower()
        result = "approved" if re.search(r"yes|ok|approve|confirm_yes|응|예|네", value) else "rejected"
        user = str(press.get("user") or "")
        out.append({"card_ts": card.ts, "by": thread.name_of(user), "by_id": user, "result": result, "ts": str(press.get("ts") or ""),
                    "evidence_ts": [card.ts], "quote": anchor(next(iter(sentences(card.text)), card.text), card)})
    return out


def finalize(thread: Thread, raw: dict, method: str) -> dict:
    """Check every item's evidence, apply the source and decision rules, add contests, the reply plan and the
    question bundle. The result has a fixed shape (``VERSION``)."""
    dropped = 0

    def checked(key: str) -> list[tuple[dict, Msg]]:
        nonlocal dropped
        kept = []
        for item in raw.get(key) or []:
            found = _evidence(thread, item)
            if found is None:
                dropped += 1
                continue
            ts_list, quote, message = found
            kept.append(({**item, "evidence_ts": ts_list, "quote": quote}, message))
        return kept

    requests = [item for item, _ in checked("requests")]
    decisions = []
    for item, _message in checked("decisions"):
        sources = [thread.get(ts) for ts in item["evidence_ts"]]
        if not any(m and m.person for m in sources):
            dropped += 1  # a bot's summary is not a decision
            continue
        statement = str(item.get("statement") or "").strip()
        main = next(m for m in sources if m and m.person)
        if REQUEST_RE.search(statement) and not ASSIGN_RE.search(statement) and "담당" not in statement and not EXPLICIT_RE.search(statement):
            requests.append({"by": main.author_name, "to": "", "what": statement, "status": "open",
                             "evidence_ts": item["evidence_ts"], "quote": item["quote"]})
            continue
        kind = EXPLICIT if item.get("kind") == EXPLICIT else IMPLICIT
        decided_by = [str(x) for x in item.get("decided_by") or [] if x] or [main.author_name]
        # confirmed: a person said it in words, in a finished message, to the thread (or assigning work to someone)
        confirmed = (kind == EXPLICIT and main.person and not main.incomplete and not main.deleted
                     and (main.addressed_to_me or bool(ASSIGN_RE.search(main.text)) or "담당:" in statement)
                     and not item.get("handover_to"))
        decisions.append({"statement": statement, "decided_by": decided_by, "decided_by_ids": [main.author_id], "kind": kind,
                          "status": "confirmed" if confirmed else "unconfirmed", "confirmed": confirmed,
                          "by_owner": main.is_owner, "agreement_signals": list(item.get("agreement_signals") or []),
                          "evidence_ts": item["evidence_ts"], "quote": item["quote"],
                          **({"handover_to": item["handover_to"]} if item.get("handover_to") else {})})

    assumptions = [{**item, "by": message.author_name, "trust": message.trust} for item, message in checked("assumptions")]
    for decision in decisions:  # why it was decided, as the thread says, and what earlier statement it confirms
        main_ts = decision["evidence_ts"][0]
        decision["reasons"] = [{"statement": a["statement"], "evidence_ts": a["evidence_ts"], "quote": a["quote"]}
                               for a in assumptions if a["evidence_ts"][0] == main_ts and a["by"] in decision["decided_by"]]
        decision["confirms"] = _confirms(thread, decision)

    claims = []
    for item, message in checked("claims"):
        if message.kind == SELF_BOT:
            continue  # what the bot itself wrote earlier is not a source
        subject, predicate, value = (str(item.get(k) or "").strip() for k in ("subject", "predicate", "value"))
        if not subject or not value:
            dropped += 1
            continue
        claims.append({"subject": subject, "aliases": list(item.get("aliases") or []), "predicate": predicate, "value": value,
                       "by": message.author_name, "by_id": message.author_id, "author_kind": message.kind, "trust": message.trust,
                       "evidence_ts": item["evidence_ts"], "quote": item["quote"], "contest": None})
    contests = _contests(claims, decisions)

    open_questions = []
    for item, message in checked("open_questions"):
        if message.kind == SELF_BOT:
            dropped += 1  # the bot's own earlier questions are not what the thread left open
            continue
        open_questions.append({**item, "ask": bool(item.get("ask", True))})
    reply_plan, incomplete = addressed_to_bot(thread)
    purpose = {}
    if raw.get("purpose"):
        found = _evidence(thread, raw["purpose"])
        if found:
            purpose = {"text": str(raw["purpose"].get("text") or ""), "evidence_ts": found[0], "quote": found[1]}
        else:
            dropped += 1

    entities = _entities(thread, claims, [item for item, _ in checked("entities")])
    result = {
        "version": VERSION, "method": method, "channel": thread.channel, "thread_ts": thread.thread_ts,
        "purpose": purpose,
        "requests": requests,
        "decisions": decisions,
        "assumptions": assumptions,
        "owners": [item for item, _ in checked("owners")],
        "deadlines": [item for item, _ in checked("deadlines")],
        "entities": entities,
        "relations": [item for item, _ in checked("relations")],
        "claims": claims,
        "contests": contests,
        "open_questions": open_questions,
        "confirmations": _confirmations(thread),
        "addressed_to_bot": reply_plan,
        "incomplete": incomplete,
        "dropped": dropped,
    }
    result["ask"] = question_bundle(result)
    return result


CONFIRM_RE = re.compile(r"맞아|맞습니다|맞아요|그게\s*맞|그대로\s*(?:가|해)|확정으로")
CONFIRM_WORDS = {"맞아", "맞습니다", "맞아요", "확정으로", "그대로"}


def _confirms(thread: Thread, decision: dict) -> list[dict]:
    """"정산 기한은 2주가 맞아" after "'출장비 정산은 2주 안에'를 남길까요?": the earlier message whose value this
    decision confirms (sharing at least two content words), latest first. Empty for decisions that are not confirmations."""
    if not CONFIRM_RE.search(decision["statement"]):
        return []
    wanted = words(decision["statement"]) - CONFIRM_WORDS
    main_ts = float(decision["evidence_ts"][0])
    for message in reversed(thread.visible):
        if float(message.ts) >= main_ts or message.author_id in decision["decided_by_ids"]:
            continue
        shared = wanted & words(message.text)
        if len(shared) >= 2:
            return [{"ts": message.ts, "by": message.author_name, "author_kind": message.kind, "shared": sorted(shared),
                     "evidence_ts": [message.ts], "quote": anchor(next((s for s in sentences(message.text) if words(s) & shared), message.text), message)}]
    return []


def _entities(thread: Thread, claims: list[dict], extra: list[dict]) -> list[dict]:
    """People and bots (authors, mentions) and the things claims are about, each with where it first appears."""
    found: dict[str, dict] = {}

    def add(name: str, kind: str, ts: str, quote: str, aliases: Iterable[str] = ()) -> None:
        key = _subject_key(name)
        if not key:
            return
        for existing in found.values():
            if key in {_subject_key(n) for n in [existing["name"], *existing["aliases"]]} or \
                    any(_subject_key(a) in {_subject_key(n) for n in [existing["name"], *existing["aliases"]]} for a in aliases):
                existing["aliases"] = sorted(set(existing["aliases"]) | set(aliases) | ({name} - {existing["name"]}))
                return
        found[key] = {"name": name, "type": kind, "aliases": sorted(set(aliases)), "evidence_ts": [ts], "quote": quote}

    for message in thread.visible:
        kind = "self" if message.kind == SELF_BOT else ("bot" if message.kind == OTHER_BOT else "person")
        add(message.author_name, kind, message.ts, "")
        for mention in message.mentions:
            name = str(mention.get("name") or mention.get("id") or "")
            raw = next((s for s in re.findall(r"<@[^>]+>", message.raw) if str(mention.get("id")) in s), "")
            add(name, "self" if mention.get("is_me") else ("bot" if mention.get("is_bot") else "person"), message.ts, raw)
    for claim in claims:
        add(claim["subject"], "term", claim["evidence_ts"][0], claim["quote"], claim.get("aliases") or [])
    for item in extra:
        add(str(item.get("name") or ""), str(item.get("type") or "term"), item["evidence_ts"][0], item["quote"])
    return list(found.values())


def question_bundle(result: dict, budget: int = 1) -> list[dict]:
    """At most ``budget`` questions for the owner: the contests first, bundled into one question, then what people
    said is still undecided (and not scheduled for later). Nothing the thread already answered."""
    parts, evidence = [], []
    for contest in result.get("contests") or []:
        label = f"{contest['subject']}의 {contest['predicate'] or '역할'}"
        latest: dict[str, dict] = {}  # one reading per author: what they said last
        for value in contest["values"]:
            latest[value["by"]] = value
        readings = " / ".join(f"{v['by']}: '{v['value'][:40]}'" for v in list(latest.values())[:3])
        parts.append(f"{label} — {readings}")
        evidence += contest["evidence_ts"]
    for question in result.get("open_questions") or []:
        if question.get("ask") and not any(norm(question.get("about", "")) and norm(question["about"]) in norm(p) for p in parts):
            parts.append(str(question.get("question") or ""))
            evidence += question["evidence_ts"]
    if not parts or budget <= 0:
        return []
    text = "확인이 필요해요. " + " · ".join(parts) + " 어느 쪽이 맞나요?" if result.get("contests") else \
        "아직 정해지지 않았어요: " + " · ".join(parts) + " 정해지면 알려 주시겠어요?"
    return [{"text": text, "evidence_ts": sorted(set(evidence), key=float)}][:budget]


# --------------------------------------------------------------------------- entry points


def merge(model: dict, rules: dict) -> dict:
    """The model's reading, with what the rules found and it missed: every claim and assumption (they are checked
    and ranked by source anyway), the owner's own statement of purpose, and decisions from messages the model
    cited for none."""
    out = dict(model)
    for key in ("claims", "assumptions", "open_questions"):
        seen = {(str((item.get("evidence_ts") or [""])[0]), norm(item.get("quote"))) for item in model.get(key) or []}
        out[key] = list(model.get(key) or []) + [item for item in rules.get(key) or []
                                                 if (item["evidence_ts"][0], norm(item["quote"])) not in seen]
    if (rules.get("purpose") or {}).get("source") == "owner" or not model.get("purpose"):
        out["purpose"] = rules.get("purpose") or {}
    cited = {str(ts) for item in model.get("decisions") or [] for ts in item.get("evidence_ts") or []}
    out["decisions"] = list(model.get("decisions") or []) + [d for d in rules.get("decisions") or [] if d["evidence_ts"][0] not in cited]
    return out


def understand_rules_payload(payload: dict) -> dict:
    thread = parse_thread(payload)
    return finalize(thread, understand_rules(thread), "rules")


async def understand(payload: dict, complete: Complete | None = None) -> dict:
    """Read one thread. With ``complete`` the model reads it and its items are checked like the rules' ones; when the
    call fails or its answer is unusable, the rules read it instead (``method`` says which)."""
    thread = parse_thread(payload)
    rules = understand_rules(thread)
    if complete is not None:
        try:
            reading = await understand_model(thread, complete)
            own = finalize(thread, reading, "model")
            if own["decisions"] or own["claims"] or own["requests"] or own["purpose"]:
                result = finalize(thread, merge(reading, rules), "model")
                result["dropped"] = own["dropped"]  # what the model cited wrongly
                return result
            logger.info("thread %s: the model's reading had no valid item, using the rules", thread.thread_ts)
        except Exception as error:  # noqa: BLE001 - endpoint down, timeout, bad JSON: the rules still read the thread
            logger.info("thread %s: model reading failed (%s), using the rules", thread.thread_ts, type(error).__name__)
    return finalize(thread, rules, "rules")
