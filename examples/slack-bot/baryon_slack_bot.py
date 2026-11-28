"""Slack @mention bot for baryon-ax: a grounded answer plus the sources it was based on.

Behaviour follows the OpenTag pattern (https://github.com/CopilotKit/OpenTag):

* ``@bot question`` in a channel answers in a thread and subscribes that thread.
* Later messages in a subscribed thread are answered without another mention.
* The whole thread is sent as conversation history, so follow-up questions work.
* An :eyes: reaction and a placeholder show that work started; the placeholder
  is replaced with the answer, a "근거" (sources) block and the trace id.

Runs over Socket Mode, so no public URL is needed: the bot only makes outbound
connections to Slack and to the baryon-ax API.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

import router as routing
import thread_state
from baryon_auth import ApiAuth, TokenSource
from slack_text import Message, UserDirectory, readable
from thread_snapshot import ThreadSnapshot, fetch_replies, understanding_payload

logger = logging.getLogger("baryon.slack")

SECTION_LIMIT = 2900  # Slack section text limit is 3000 characters
MAX_ANSWER_SECTIONS = 12  # a message holds 50 blocks; sources and notes need a few
TEXT_LIMIT = 3500  # fallback `text` of a message with blocks; Slack rejects long ones with msg_too_long
LINKED_CHARS = 4000  # text read from one linked Slack thread
SLACK_LINK_RE = re.compile(
    r"<?(https?://([a-z0-9-]+)\.slack\.com/archives/([A-Z0-9]+)/p(\d{10})(\d{6})([^|>\s]*))(?:\|[^>]*)?>?")
FOREIGN_LINK_NOTE = (":link: 다른 Slack 워크스페이스의 링크는 봇이 읽을 수 없습니다(그 워크스페이스에 봇이 설치되어 있지 않음). "
                     "내용이 필요하면 본문을 붙여 넣거나 파일로 올려 주세요.")
UNREADABLE_LINK_NOTE = ":link: 링크된 Slack 대화를 읽지 못했습니다. 봇을 그 채널에 초대하면 다음부터 함께 읽습니다."
ATTACHMENT_NOTE = (":paperclip: 첨부 파일은 읽지 않고 이미 등록된 문서에서 찾았습니다. 파일 내용으로 답하게 하려면 "
                   "`문서로 추가하고 분석해줘`라고 보내 주세요.")
SNIPPET_CHARS = 180
# "> 작성자: …" style front matter (e.g. who answered and when) is provenance, not content worth quoting.
METADATA_LINE_RE = re.compile(r"^\s*>\s*[^:\n]{1,20}:\s.*$", re.MULTILINE)
PENDING_TEXT = ":hourglass_flowing_sand: 문서를 찾아 답변을 준비하고 있습니다…"
THREAD_PENDING_TEXT = ":hourglass_flowing_sand: 스레드에 적힌 내용부터 읽고 있습니다…"
SUMMARY_PENDING_TEXT = ":hourglass_flowing_sand: 이 스레드를 정리하고 있습니다…"
NO_THREAD_ANSWER = "NO_ANSWER_IN_THREAD"
THREAD_NOTE = ":thread: 이 스레드·채널에 적힌 글로 답했습니다(문서 검색 안 함)."
NO_GAP_NOTE = ":mag: 스레드·채널과 문서 어디에도 답이 없었습니다. 사람·봇에 관한 질문이라 지식 공백으로 남기지 않았습니다."
CHANNEL_LINES = 30  # channel messages read before a top-level question about people or bots
THREAD_PROMPT = """너는 슬랙 봇이다. 아래 [스레드]와 [채널 최근 글], [스레드 이해]만 근거로 [질문]에 답하라.
- 근거는 사람·봇 이름과 함께 짧게 밝혀라. 다른 봇의 글은 그 봇의 주장일 뿐 확인된 사실이 아니라고 구분하라.
- 저장·진행·완료 같은 상태는 말하지 마라. 너는 이 답에서 아무것도 저장하거나 실행하지 않는다.
- 이 글들에 답이 없으면 다른 말 없이 정확히 {sentinel} 한 줄만 써라.

[스레드]
{thread}

[채널 최근 글]
{channel}

[스레드 이해]
{understanding}

[질문]
{question}"""
ERROR_PREFIX = ":x: 답변을 만들지 못했습니다"
STATUS_PREFIXES = (PENDING_TEXT, ERROR_PREFIX)  # bot status posts are not conversation turns


# --------------------------------------------------------------------------- config


@dataclass
class Config:
    api_url: str = "http://127.0.0.1:8000"
    organization_id: str = "local-org"
    roles: str = "member"
    model: str = ""
    timeout: float = 120.0
    max_sources: int = 4
    thread_turns: int = 10
    subscribe_threads: bool = True
    show_snippets: bool = True
    direct_messages: bool = True     # answer 1:1 DMs without a mention
    bot_user_id: str = "slack-bot"   # the bot's own principal in dev mode (AUTH_MODE=dev)

    @classmethod
    def from_env(cls) -> "Config":
        def flag(name: str, default: bool) -> bool:
            return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            api_url=os.getenv("BARYON_API_URL", cls.api_url).rstrip("/"),
            organization_id=os.getenv("BARYON_ORGANIZATION_ID", cls.organization_id),
            roles=os.getenv("BARYON_ROLES", cls.roles),
            model=os.getenv("BARYON_MODEL", ""),
            timeout=float(os.getenv("BARYON_TIMEOUT", cls.timeout)),
            max_sources=int(os.getenv("SLACK_MAX_SOURCES", cls.max_sources)),
            thread_turns=int(os.getenv("SLACK_THREAD_TURNS", cls.thread_turns)),
            subscribe_threads=flag("SLACK_SUBSCRIBE_THREADS", True),
            show_snippets=flag("SLACK_SHOW_SNIPPETS", True),
            direct_messages=flag("SLACK_DIRECT_MESSAGES", True),
        )


# --------------------------------------------------------------------------- baryon-ax client


@dataclass
class Answer:
    text: str
    citations: list[dict] = field(default_factory=list)
    trace_id: str = ""
    answered: bool = True  # False when the documents did not answer it (baryon-ax `grounding.answered`)


class BaryonClient:
    """Thin client for the OpenAI-compatible chat endpoint and document search.

    Each Slack user is its own baryon-ax principal (``slack:<USER_ID>``), sent as
    ``X-On-Behalf-Of`` so document permissions, gaps and audit logs are per person
    and the bot is recorded as ``acting_service`` (see baryon_auth.py).
    """

    def __init__(self, config: Config, http: httpx.Client | None = None, tokens: TokenSource | None = None,
                 bot_roles: str = "admin") -> None:
        self.config = config
        self.http = http or httpx.Client(base_url=config.api_url, timeout=config.timeout)
        self.auth = ApiAuth(tokens or TokenSource.from_env(self.http), config.organization_id,
                            config.bot_user_id, bot_roles)

    def as_user(self, method: str, path: str, slack_user: str, **kwargs: Any) -> httpx.Response:
        """A call made for a Slack user."""
        return self.auth.request(self.http, method, path, on_behalf_of=f"slack:{slack_user}",
                                 roles=self.config.roles, **kwargs)

    def as_bot(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """A call the bot makes as itself (no On-Behalf-Of header)."""
        return self.auth.request(self.http, method, path, **kwargs)

    def ask(self, messages: list[dict], slack_user: str, thread_id: str, use_retrieval: bool = True,
            record_gap: bool = True) -> Answer:
        """``record_gap=False``: an unanswered exchange is not a knowledge gap (a question about people or bots, a
        classification call, a remark that is not a question)."""
        body: dict[str, Any] = {"messages": messages, "stream": False, "thread_id": thread_id}
        if not use_retrieval:
            body["use_retrieval"] = False
        if not record_gap:
            body["record_gap"] = False
        if self.config.model:
            body["model"] = self.config.model
        response = self.as_user("POST", "/v1/chat/completions", slack_user, json=body)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        grounding = data.get("grounding") or {}
        return Answer(text=text, citations=data.get("citations") or [], trace_id=data.get("id", ""),
                      answered=bool(grounding.get("answered", True)))

    def understand(self, payload: dict, slack_user: str) -> dict | None:
        """The thread-understanding reading (apps/api thread_understanding.py), or None when it is unavailable."""
        try:
            response = self.as_user("POST", "/v1/loop/threads/understand", slack_user, json=payload)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            logger.info("thread understanding unavailable", exc_info=True)
            return None

    def snippets(self, query: str, slack_user: str) -> dict[tuple[str, int], str]:
        """Chunk text for quoting; the chat endpoint returns only document id/title/chunk."""
        if len(query.strip()) < 2:
            return {}
        try:
            response = self.as_user("GET", "/v1/documents/search", slack_user, params={"q": query})
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("document search failed; answering without snippets", exc_info=True)
            return {}
        return {
            (item["document_id"], int(item.get("chunk", 0))): str(item.get("content") or "")
            for item in response.json().get("data", [])
        }

    def cited_snippets(self, query: str, citations: list[dict], slack_user: str) -> dict[tuple[str, int], str]:
        """Quote text for exactly the cited chunks. A search on the last message misses them on follow-ups
        ("다시 해봐"), so chunks the search did not return are read one by one."""
        found = self.snippets(query, slack_user)
        for item in citations:
            key = (item.get("document_id", ""), int(item.get("chunk", 0)))
            if not key[0] or key in found:
                continue
            try:
                response = self.as_user("GET", f"/v1/documents/{key[0]}", slack_user, params={"chunk": key[1]})
                response.raise_for_status()
                found[key] = str((response.json().get("cited_chunk") or {}).get("content") or "")
            except (httpx.HTTPError, ValueError):
                logger.info("could not read cited chunk %s#%s", *key)
        return found


# --------------------------------------------------------------------------- formatting


def slack_domain(url: str) -> str:
    """"https://acme.slack.com/" → "acme" (auth.test url of the bot's workspace)."""
    match = re.match(r"https?://([a-z0-9-]+)\.slack\.com", url or "")
    return match.group(1) if match else ""


def strip_mentions(text: str | None, me: str = "", directory: UserDirectory | None = None) -> str:
    """Routing text for keyword checks ("문서로 추가", "질문 있어?"): the bot's mention is left out when it only
    calls the bot; every other mention stays as "@이름". What the model reads comes from Responder.read instead."""
    return readable(text, me=me, directory=directory, drop_me=True)


def to_mrkdwn(markdown: str) -> str:
    """Convert the model's CommonMark to Slack mrkdwn (bold, headings, links)."""
    # Slack only renders *bold* when the markers touch whitespace or punctuation, so
    # Korean text glued to them ("**15일**입니다") gets zero-width spaces around it.
    bold = "​*\\1*​"
    # Headings ("### **1. 소개**") become one bold line; the inner ** would otherwise show as stray stars.
    text = re.sub(r"^#{1,6}\s+(.+)$", lambda m: "*" + m.group(1).replace("**", "").strip() + "*", markdown, flags=re.MULTILINE)
    # Slack has no lists or tables: "*   item" / "- item" become bullets, table rows become "a · b" lines.
    text = re.sub(r"^(\s*)[*-]\s+", lambda m: m.group(1) + "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|(.+)\|\s*$", lambda m: " · ".join(cell.strip() for cell in m.group(1).split("|")), text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", bold, text)
    text = re.sub(r"__(.+?)__", bold, text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)
    return text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def thread_messages(replies: list[dict], bot_user_id: str, max_turns: int,
                    directory: UserDirectory | None = None) -> list[dict]:
    """Slack thread → chat messages. The bot's own posts become assistant turns; everyone else's (people and
    other bots) are user turns written as "이름: 내용" / "이름 (봇): 내용", so the model knows who said what.
    The last turn is the question being answered and stays unlabelled (the API searches with its text)."""
    turns: list[tuple[str, str, str]] = []  # (role, labelled content, bare content)
    for reply in replies:
        own = bool(bot_user_id) and reply.get("user") == bot_user_id
        if reply.get("subtype") not in (None, "file_share", "bot_message", "thread_broadcast") and not own:
            continue
        if own:
            content = (reply.get("text") or "").strip()
            if content and not content.startswith(STATUS_PREFIXES):
                turns.append(("assistant", content, content))
            continue
        message = Message.from_event(reply, directory, bot_user_id)  # another bot's post counts as a bot (is_bot)
        content = message.text_for_me
        if content:
            turns.append(("user", message.line(), content))
    turns = turns[-max_turns:]
    while turns and turns[0][0] == "assistant":
        turns.pop(0)
    messages = [{"role": role, "content": labelled} for role, labelled, _ in turns]
    if turns and turns[-1][0] == "user":
        messages[-1]["content"] = turns[-1][2]
    return messages


def quote_text(content: str) -> str:
    """Chunk text for a one-line quote, without metadata blockquote lines."""
    return " ".join(METADATA_LINE_RE.sub("", content).split())


def source_lines(citations: list[dict], snippets: dict[tuple[str, int], str], max_sources: int) -> list[str]:
    seen: set[tuple[str, int]] = set()
    lines: list[str] = []
    for item in citations:
        key = (item.get("document_id", ""), int(item.get("chunk", 0)))
        if key in seen:
            continue
        seen.add(key)
        line = f"*{len(lines) + 1}. {item.get('title') or key[0]}* · chunk {key[1]}"
        snippet = quote_text(snippets.get(key, ""))
        if snippet:
            line += f"\n> {_clip(snippet, SNIPPET_CHARS)}"
        lines.append(line)
        if len(lines) >= max_sources:
            break
    return lines


def answer_sections(mrkdwn: str, limit: int = SECTION_LIMIT, max_sections: int = MAX_ANSWER_SECTIONS) -> list[str]:
    """Long answers become several sections (Slack caps one at 3000 characters), split at paragraph or line breaks."""
    parts: list[str] = []
    rest = mrkdwn.strip()
    while rest and len(parts) < max_sections:
        if len(rest) <= limit:
            parts.append(rest)
            rest = ""
            break
        cut = max(rest.rfind("\n\n", 0, limit), rest.rfind("\n", 0, limit))
        cut = cut if cut > limit // 2 else limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest and parts:
        parts[-1] = _clip(parts[-1] + "\n" + rest, limit)
    return parts or [""]


def build_blocks(answer: Answer, snippets: dict[tuple[str, int], str], max_sources: int, note: str = "",
                 recorded_gap: bool = True) -> list[dict]:
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": part}} for part in answer_sections(to_mrkdwn(answer.text))]
    if note:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": note}]})
    lines = source_lines(answer.citations, snippets, max_sources) if answer.answered else []
    blocks.append({"type": "divider"})
    if not answer.answered:
        # Retrieval may still return passages, but they did not answer the question: listing them as sources misleads.
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
            ":mag: 문서에서 답을 찾지 못해 지식 공백으로 남겼습니다. 담당자가 답하면 다음부터는 바로 답합니다." if recorded_gap
            else NO_GAP_NOTE}]})
    elif lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip("*:bookmark_tabs: 근거*\n" + "\n".join(lines), SECTION_LIMIT)}})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ":warning: 근거 문서를 찾지 못했습니다. 문서에 없는 내용일 수 있습니다."}]})
    if answer.trace_id:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"baryon-ax · trace `{answer.trace_id}`"}]})
    return blocks


def understanding_lines(state: thread_state.ThreadState | None) -> str:
    """The thread reading as prompt lines: purpose, decisions, owners, open questions, disagreements."""
    if state is None:
        return ""
    lines = [f"목적: {state.purpose}"] if state.purpose else []
    lines += [f"결정({'확정' if d.status == 'confirmed' else '확정 전'}): {d.text}" + (f" — {d.by}" if d.by else "") for d in state.decisions]
    lines += [f"담당: {o.text}" for o in state.owners]
    lines += [f"열린 질문: {q.text}" for q in state.questions]
    lines += [f"엇갈리는 주장: {c.text}" for c in state.contests]
    return "\n".join(lines)


# --------------------------------------------------------------------------- thread subscriptions


class ThreadSubscriptions:
    """Threads the bot was mentioned in; follow-ups there need no new mention."""

    def __init__(self, ttl_seconds: float = 24 * 3600, max_threads: int = 5000) -> None:
        self.ttl = ttl_seconds
        self.max_threads = max_threads
        self._threads: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def add(self, channel: str, thread_ts: str) -> None:
        with self._lock:
            self._threads[(channel, thread_ts)] = time.monotonic()
            if len(self._threads) > self.max_threads:
                oldest = min(self._threads, key=self._threads.get)
                self._threads.pop(oldest, None)

    def __contains__(self, key: tuple[str, str]) -> bool:
        with self._lock:
            started = self._threads.get(key)
            if started is None:
                return False
            if time.monotonic() - started > self.ttl:
                self._threads.pop(key, None)
                return False
            return True


# --------------------------------------------------------------------------- responder


class Responder:
    """Slack-framework-free core so it can be tested with a fake WebClient."""

    def __init__(self, config: Config, baryon: BaryonClient, bot_user_id: str, subscriptions: ThreadSubscriptions | None = None,
                 intake=None, guide=None) -> None:
        self.config = config
        self.guide = guide  # guide.Guide: "설명서 알려줘" is answered from the whole usage guide
        self.baryon = baryon
        self.bot_user_id = bot_user_id
        self.subscriptions = subscriptions or ThreadSubscriptions()
        self.intake = intake  # doc_intake.DocumentIntake: "문서로 추가" requests
        self.directory = UserDirectory()  # user id → name and bot flag; create_app backs it with users.info
        self.team_domain = ""  # the bot's workspace ("acme" of acme.slack.com); links elsewhere cannot be read
        self.snapshots = None  # thread_snapshot.SnapshotStore; create_app shares the bot's
        self.states = thread_state.ThreadStateStore()  # per-thread purpose, decisions, owners and work state
        self.read_threads = False  # read the thread once (POST /v1/loop/threads/understand) before answering in it

    def read(self, text: str | None) -> str:
        """Slack mrkdwn → what the model reads: nothing deleted, the bot as "너(이름)", others as "@이름"."""
        return readable(text, me=self.bot_user_id, directory=self.directory)

    def command_text(self, text: str | None) -> str:
        """Slack mrkdwn → text for keyword routing and the empty-question check (the bot's own call left out)."""
        return strip_mentions(text, self.bot_user_id, self.directory)

    def expand_links(self, web, messages: list[dict]) -> tuple[list[dict], list[str]]:
        """Slack message links in people's turns become the linked thread's text, so "see this thread" is
        understood. Links to another workspace, or to a channel the bot is not in, are marked unreadable."""
        notes: list[str] = []
        cache: dict[str, str] = {}

        def replace(match: re.Match) -> str:
            url, domain, channel, seconds, micros, query = match.groups()
            if self.team_domain and domain != self.team_domain:
                if FOREIGN_LINK_NOTE not in notes:
                    notes.append(FOREIGN_LINK_NOTE)
                return f"[다른 Slack 워크스페이스({domain})의 링크: 봇이 읽을 수 없음]"
            thread = re.search(r"thread_ts=(\d+\.\d+)", query or "")
            ts = thread.group(1) if thread else f"{seconds}.{micros}"
            if url not in cache:
                try:
                    found = fetch_replies(web, channel, ts)
                    said = [Message.from_event(m, self.directory, self.bot_user_id) for m in found]
                    lines = [f"- {m.line()}" for m in said if m.text_for_me]
                    cache[url] = _clip("\n".join(lines), LINKED_CHARS)
                except Exception as error:  # not_in_channel, channel_not_found, missing scope
                    logger.info("linked thread %s unreadable (%s)", url, error)
                    cache[url] = ""
            if not cache[url]:
                if UNREADABLE_LINK_NOTE not in notes:
                    notes.append(UNREADABLE_LINK_NOTE)
                return "[링크된 Slack 대화: 읽지 못함]"
            return f"[링크된 Slack 대화]\n{cache[url]}\n[링크 끝]"

        expanded = [{**m, "content": SLACK_LINK_RE.sub(replace, m["content"])} if m["role"] == "user" else m
                    for m in messages]
        return expanded, notes

    def is_direct_message(self, event: dict) -> bool:
        """A person's message in a 1:1 DM with the bot. Every DM is a question; no mention needed."""
        if not self.config.direct_messages or event.get("channel_type") != "im":
            return False
        if event.get("subtype") not in (None, "file_share"):
            return False
        return not (event.get("bot_id") or event.get("user") == self.bot_user_id)

    def should_follow_up(self, event: dict) -> bool:
        """Plain thread message (no mention) in a subscribed thread."""
        if event.get("channel_type") == "im":
            return False  # DMs are answered by is_direct_message
        if not self.config.subscribe_threads or event.get("subtype") not in (None, "file_share") or event.get("bot_id"):
            return False
        if event.get("user") == self.bot_user_id or f"<@{self.bot_user_id}" in (event.get("text") or ""):
            return False  # mentions are handled by the app_mention event
        thread_ts = event.get("thread_ts")
        return bool(thread_ts) and (event.get("channel", ""), thread_ts) in self.subscriptions

    def _history(self, web, channel: str, thread_ts: str, fallback: str) -> list[dict]:
        try:
            replies = fetch_replies(web, channel, thread_ts)  # every page: the newest turns are at the end
        except Exception as error:  # missing channels:history scope, private channel, …
            logger.info("thread history unavailable (%s); answering the single message", error)
            replies = []
        messages = thread_messages(replies, self.bot_user_id, self.config.thread_turns, self.directory)
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": fallback})
        return messages

    def utterance(self, event: dict, web=None, owner: bool = False, secretary_thread: bool = False) -> routing.Utterance:
        """What the router reads of this message (router.Utterance)."""
        message = Message.from_event(event, self.directory, self.bot_user_id)
        others = [m.name for m in message.mentions if m.kind == "user" and not m.is_me]
        thread_files = False
        if event.get("thread_ts") and web is not None and self.intake and self.intake.config.enabled and not event.get("files"):
            from doc_intake import wants_file_reading

            if wants_file_reading(self.command_text(event.get("text"))):
                from doc_intake import thread_files as files_in

                thread_files = bool(files_in(self._intake_context(web, event) + [event], self.bot_user_id))
        return routing.Utterance(
            text=self.command_text(event.get("text")), owner=owner, in_thread=bool(event.get("thread_ts")), others=others,
            unresolved_mentions=any(m.kind == "user" and m.name in ("", m.id) for m in message.mentions),
            files=bool(event.get("files")), thread_files=thread_files,
            intake_enabled=bool(self.intake and self.intake.config.enabled), guide_enabled=bool(self.guide and self.guide.enabled),
            loop_enabled=False, secretary_thread=secretary_thread)

    def answer(self, event: dict, web, route: routing.Route | None = None) -> None:
        """Answer one message the bot was asked. ``route`` is the router's decision (Dispatcher); without it the
        rules decide (a direct call: the caller already knows the message is meant for the bot)."""
        channel = event["channel"]
        command = self.command_text(event.get("text"))  # "<@봇>" alone is an empty question
        question = self.read(event.get("text")) if command else ""
        thread_ts = event.get("thread_ts") or event["ts"]
        route = route or routing.classify_rules(self.utterance(event, web))
        if route.silent:
            return  # files posted without a question: wait for the question ("분석해줘") that usually follows
        if not question:
            usage = ("무엇을 찾아드릴까요? 질문을 그대로 보내 주세요. 예: `연차휴가는 며칠이야?`" if event.get("channel_type") == "im"
                     else "무엇을 찾아드릴까요? `@봇 질문` 형태로 물어봐 주세요. 예: `@baryon 연차휴가는 며칠이야?`")
            web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=usage)
            return
        if self.config.subscribe_threads:
            self.subscriptions.add(channel, thread_ts)
        if route.intent == routing.DOC_INTAKE and self.intake and self.intake.config.enabled:
            if route.analyze:
                self.add_documents(event, web, question, analyze=True, messages=self._intake_context(web, event))
            else:
                self.add_documents(event, web, question)
            return
        if route.intent == routing.GUIDE and self.guide and self.guide.enabled and self.answer_from_guide(event, web, question):
            return
        if route.intent == routing.SUMMARY and event.get("thread_ts"):
            self.answer_summary(event, web)
            return
        self.answer_question(event, web, question, route)

    # ---------------------------------------------------------------- the thread first

    def read_thread(self, web, channel: str, thread_ts: str, user: str) -> tuple[ThreadSnapshot | None, thread_state.ThreadState | None]:
        """The thread's snapshot and, when thread reading is on, its state from one understanding reading."""
        snapshot = None
        try:
            if self.snapshots is not None:
                snapshot = self.snapshots.get(web, channel, thread_ts)
            else:
                snapshot = ThreadSnapshot.fetch(web, channel, thread_ts, self.directory, self.bot_user_id)
        except Exception as error:  # noqa: BLE001 - no history scope: the message alone
            logger.info("thread %s/%s unreadable (%s)", channel, thread_ts, error)
            return None, self.states.known(channel, thread_ts)
        if self.read_threads and len(snapshot):
            reading = self.baryon.understand(understanding_payload(snapshot, me_name=self.directory.name(self.bot_user_id, "")), user)
            if reading:
                return snapshot, self.states.apply_understanding(channel, thread_ts, reading)
        return snapshot, self.states.known(channel, thread_ts)

    def channel_lines(self, web, event: dict) -> list[str]:
        """Recent channel messages before a top-level question ("그 봇이 무슨 일을 하고 있어?"), as "이름: 내용"."""
        try:
            history = web.conversations_history(channel=event["channel"], latest=event["ts"], inclusive=False,
                                                limit=CHANNEL_LINES).get("messages", [])
        except Exception as error:  # noqa: BLE001 - missing *:history scope
            logger.info("channel history unavailable (%s)", error)
            return []
        lines = []
        for item in reversed(history):  # oldest first
            if item.get("subtype") not in (None, "file_share", "bot_message", "thread_broadcast"):
                continue
            message = Message.from_event(item, self.directory, self.bot_user_id)
            if message.text_for_me and not message.from_me:
                lines.append(message.line())
        return lines

    def answer_question(self, event: dict, web, question: str, route: routing.Route) -> None:
        """A question: what the thread and channel say first, documents after. A question about people or bots, or
        one whose mentions could not be read, never becomes a knowledge gap."""
        channel, user = event["channel"], event.get("user", "unknown")
        thread_ts = event.get("thread_ts") or event["ts"]
        self._react(web, channel, event["ts"], "eyes", add=True)
        # Read the thread before posting the placeholder so it is not part of the history.
        messages = self._history(web, channel, thread_ts, question) if event.get("thread_ts") else [{"role": "user", "content": question}]
        messages, link_notes = self.expand_links(web, messages)
        state = None
        context_lines: list[str] = []
        if event.get("thread_ts"):
            _, state = self.read_thread(web, channel, thread_ts, user)
            context_lines = [m["content"] for m in messages[:-1]]
        channel_lines = self.channel_lines(web, event) if route.intent == routing.THREAD_QUESTION and not event.get("thread_ts") else []
        thread_first = bool(context_lines or channel_lines)
        pending = web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=THREAD_PENDING_TEXT if thread_first else PENDING_TEXT)
        try:
            if thread_first:
                prompt = THREAD_PROMPT.format(sentinel=NO_THREAD_ANSWER, thread="\n".join(context_lines) or "(없음)",
                                              channel="\n".join(channel_lines[-CHANNEL_LINES:]) or "(없음)",
                                              understanding=understanding_lines(state) or "(없음)", question=question)
                from_thread = self.baryon.ask([{"role": "user", "content": prompt}], user, thread_id=f"slack:{channel}:{thread_ts}",
                                              use_retrieval=False, record_gap=False)
                if NO_THREAD_ANSWER not in from_thread.text and from_thread.text.strip():
                    from_thread.citations, from_thread.answered = [], True
                    text = thread_state.guard(from_thread.text, state)
                    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": part}} for part in answer_sections(to_mrkdwn(text))]
                    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": " ".join([THREAD_NOTE] + link_notes)}]})
                    if from_thread.trace_id:
                        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"baryon-ax · trace `{from_thread.trace_id}`"}]})
                    web.chat_update(channel=channel, ts=pending["ts"], text=_clip(text, TEXT_LIMIT), blocks=blocks)
                    self._react(web, channel, event["ts"], "white_check_mark", add=True)
                    return
                web.chat_update(channel=channel, ts=pending["ts"], text=PENDING_TEXT)
            result = self.baryon.ask(messages, user, thread_id=f"slack:{channel}:{thread_ts}", record_gap=route.record_gap)
            result.text = thread_state.guard(result.text, state)
            snippets = (self.baryon.cited_snippets(question, result.citations[: self.config.max_sources * 2], user)
                        if self.config.show_snippets and result.citations and result.answered else {})
            note = " ".join(link_notes + ([ATTACHMENT_NOTE] if event.get("files") else []))
            web.chat_update(channel=channel, ts=pending["ts"], text=_clip(result.text, TEXT_LIMIT),
                            blocks=build_blocks(result, snippets, self.config.max_sources, note=note, recorded_gap=route.record_gap))
            self._react(web, channel, event["ts"], "white_check_mark", add=True)
        except Exception as error:
            logger.exception("baryon-ax answer failed")
            detail = f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
            web.chat_update(channel=channel, ts=pending["ts"], text=f"{ERROR_PREFIX} ({detail}). 잠시 후 다시 시도해 주세요.")
            self._react(web, channel, event["ts"], "x", add=True)
        finally:
            self._react(web, channel, event["ts"], "eyes", add=False)

    def answer_summary(self, event: dict, web) -> None:
        """"정리해줘": purpose, decisions, owners and open questions from the thread state (no model, no search)."""
        channel, user = event["channel"], event.get("user", "unknown")
        thread_ts = event.get("thread_ts") or event["ts"]
        self._react(web, channel, event["ts"], "eyes", add=True)
        pending = web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=SUMMARY_PENDING_TEXT)
        _, state = self.read_thread(web, channel, thread_ts, user)
        text = thread_state.summary_text(state)
        web.chat_update(channel=channel, ts=pending["ts"], text=_clip(text, TEXT_LIMIT),
                        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": part}} for part in answer_sections(text)]
                        + [{"type": "context", "elements": [{"type": "mrkdwn", "text": ":memo: 스레드 글에서 읽은 것만 정리했습니다."}]}])
        self._react(web, channel, event["ts"], "white_check_mark", add=True)
        self._react(web, channel, event["ts"], "eyes", add=False)

    def answer_from_guide(self, event: dict, web, question: str) -> bool:
        """Explain from the whole usage guide. False when there is no guide to read (search answers instead)."""
        channel, user = event["channel"], event.get("user", "unknown")
        thread_ts = event.get("thread_ts") or event["ts"]
        document = self.guide.find(user)
        if not document:
            return False
        self._react(web, channel, event["ts"], "eyes", add=True)
        pending = web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=":book: 사용 안내서를 읽고 설명을 준비하고 있습니다…")
        try:
            result = self.baryon.ask([{"role": "user", "content": self.guide.prompt(question, document)}], user,
                                     thread_id=f"slack:{channel}:{thread_ts}", use_retrieval=False)
            result.citations = [{"document_id": document["id"], "title": document["title"], "chunk": 0}]
            result.answered = True
            web.chat_update(channel=channel, ts=pending["ts"], text=_clip(result.text, TEXT_LIMIT),
                            blocks=build_blocks(result, {}, self.config.max_sources, note=":book: 사용 안내서 전체를 근거로 설명했습니다."))
            self._react(web, channel, event["ts"], "white_check_mark", add=True)
        except Exception as error:
            logger.exception("guide answer failed")
            detail = f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
            web.chat_update(channel=channel, ts=pending["ts"], text=f"{ERROR_PREFIX} ({detail}). 잠시 후 다시 시도해 주세요.")
            self._react(web, channel, event["ts"], "x", add=True)
        finally:
            self._react(web, channel, event["ts"], "eyes", add=False)
        return True

    def _intake_context(self, web, event: dict) -> list[dict]:
        """The thread, or for a top-level request the person's message just above it."""
        channel = event["channel"]
        try:
            if event.get("thread_ts"):
                return fetch_replies(web, channel, event["thread_ts"])
            if event.get("files"):
                return []
            history = web.conversations_history(channel=channel, latest=event["ts"], inclusive=False, limit=10).get("messages", [])
        except Exception as error:  # missing *:history scope
            logger.info("conversation history unavailable (%s)", error)
            return []
        for message in history:  # newest first
            if not (message.get("user") == self.bot_user_id or message.get("bot_id") or message.get("subtype") not in (None, "file_share")):
                return [message]
        return []

    def add_documents(self, event: dict, web, question: str, analyze: bool | None = None,
                      messages: list[dict] | None = None) -> None:
        """"문서로 추가": register the thread's files (or text), then analyze them if asked.
        With ``analyze=True`` (a request to read the thread's files) the answer always comes from their text."""
        from doc_intake import ANALYSIS_PENDING, INTAKE_PENDING, IntakeError, analysis_blocks, result_text, wants_analysis

        channel, user = event["channel"], event.get("user", "unknown")
        thread_ts = event.get("thread_ts") or event["ts"]
        self._react(web, channel, event["ts"], "eyes", add=True)
        messages = self._intake_context(web, event) if messages is None else messages
        pending = web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=INTAKE_PENDING)
        try:
            result = self.intake.collect(messages, event, self.bot_user_id, self.read, user)
        except IntakeError as error:
            web.chat_update(channel=channel, ts=pending["ts"], text=f":warning: {error}")
            self._react(web, channel, event["ts"], "x", add=True)
            self._react(web, channel, event["ts"], "eyes", add=False)
            return
        except Exception as error:
            logger.exception("document intake failed")
            web.chat_update(channel=channel, ts=pending["ts"], text=f":x: 문서를 등록하지 못했습니다 ({type(error).__name__}).")
            self._react(web, channel, event["ts"], "x", add=True)
            self._react(web, channel, event["ts"], "eyes", add=False)
            return
        summary = result_text(result)
        foreign = any(m.group(2) != self.team_domain for msg in messages + [event]
                      for m in SLACK_LINK_RE.finditer(msg.get("text") or "")) if self.team_domain else False
        if foreign:
            summary += "\n\n" + FOREIGN_LINK_NOTE
        web.chat_update(channel=channel, ts=pending["ts"], text=summary,
                        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": summary}}])
        if result.added:
            self._attach_sheet(web, event, result, user, thread_ts)
        if result.added and (analyze if analyze is not None else wants_analysis(question)):
            working = web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=ANALYSIS_PENDING)
            try:
                prompt, truncated = self.intake.analysis_prompt(question, result.added, user)
                answer = self.baryon.ask([{"role": "user", "content": prompt}], user,
                                         thread_id=f"slack:{channel}:{thread_ts}", use_retrieval=False)
                web.chat_update(channel=channel, ts=working["ts"], text=answer.text,
                                blocks=analysis_blocks(to_mrkdwn(answer.text), result.added, truncated, answer.trace_id))
            except Exception as error:
                logger.exception("analysis of added documents failed")
                detail = f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
                web.chat_update(channel=channel, ts=working["ts"], text=f"{ERROR_PREFIX} ({detail}). 문서는 등록되었으니 질문으로 다시 물어봐 주세요.")
        self._react(web, channel, event["ts"], "white_check_mark" if result.added else "x", add=True)
        self._react(web, channel, event["ts"], "eyes", add=False)

    def _attach_sheet(self, web, event: dict, result, user: str, thread_ts: str) -> None:
        """The added documents as an .xlsx in the thread: one row each, with a one-line summary."""
        import intake_sheet

        if not intake_sheet.sheet_enabled():
            return
        channel = event["channel"]
        try:
            summaries = intake_sheet.one_line_summaries(self.intake, result.added, user, f"slack:{channel}:{thread_ts}:sheet")
            content = intake_sheet.build_sheet(result, summaries, requested_by=f"slack:{user}",
                                               source=f"Slack {channel} 스레드 {thread_ts}")
        except Exception:
            logger.exception("could not build the added-documents sheet")
            return
        intake_sheet.post_sheet(web, channel, thread_ts, content, intake_sheet.sheet_filename(event.get("ts", "")))

    @staticmethod
    def _react(web, channel: str, ts: str, name: str, add: bool) -> None:
        try:
            (web.reactions_add if add else web.reactions_remove)(channel=channel, timestamp=ts, name=name)
        except Exception:
            pass  # reactions:write is optional


# --------------------------------------------------------------------------- slack app


class Dispatcher:
    """Every Slack message through one router (router.py): who handles it, or silence.

    ``route`` is the decision alone (the eval replays it); ``on_mention`` / ``on_message`` act on it. A message that
    stops mid-sentence is held (thread_snapshot.TruncationHold) and routed again once its author edits it or the wait
    ends (:meth:`release`). Edits and deletions update the thread state and take back what the loop recorded from
    the message (loop_desk.LoopDesk.observe)."""

    def __init__(self, responder: Responder, router: routing.Router | None = None, bridge=None, loop_desk=None, desk=None,
                 snapshots=None, hold=None, archive=None, background: bool = True) -> None:
        self.responder = responder
        self.router = router or routing.Router()
        self.bridge, self.loop_desk, self.desk, self.archive = bridge, loop_desk, desk, archive
        self.snapshots, self.hold = snapshots, hold
        self.background = background  # loop recording off the event thread (the eval runs it inline)
        self.deferred: dict[tuple[str, str], tuple[dict, Any, bool]] = {}  # held messages: (event, client, mention)
        self.released: set[tuple[str, str]] = set()
        self.lock = threading.Lock()
        self.log: list[tuple[str, routing.Route]] = []  # (ts, route) of recent decisions, newest last
        self.my_bot_id = ""

    @property
    def me(self) -> str:
        return self.responder.bot_user_id

    # ---------------------------------------------------------------- the decision

    def route(self, event: dict, web, mention: bool) -> routing.Route:
        channel, ts = event.get("channel", ""), event.get("ts", "")
        message = Message.from_event(event, self.responder.directory, self.me, self.my_bot_id)
        is_dm = event.get("channel_type") == "im"
        owner = bool(self.bridge and self.bridge.from_owner(event))
        secretary_thread = bool(self.bridge and self.bridge.working_in(event))
        held = False
        if self.hold is not None and self.hold.config.wait_seconds > 0 and self.hold.should_hold(message):
            with self.lock:
                held = (channel, ts) not in self.released
        previous, caller, context = None, "", ""
        thread_ts = event.get("thread_ts")
        if thread_ts and not mention and not is_dm and not message.mentions_me:
            subscribed = (channel, thread_ts) in self.responder.subscriptions
            if not subscribed and not (secretary_thread and owner):
                return self._decided(ts, routing.Route(routing.SILENT, routing.NOBODY, "봇이 들어가지 않은 스레드", "gate"))
        if thread_ts and self.snapshots is not None:
            try:
                snapshot = self.snapshots.get(web, channel, thread_ts)
                previous, caller = routing.thread_position(snapshot.messages, ts)
                context = "\n".join(m.line() for m in snapshot.visible()[-8:] if m.ts != ts and m.text_for_me)
            except Exception as error:  # noqa: BLE001 - no history scope: decide on the message alone
                logger.info("thread %s/%s unreadable for routing (%s)", channel, thread_ts, error)
        reason = routing.silence_reason(message, mention=mention, is_dm=is_dm, held=held, previous=previous, last_caller=caller,
                                        secretary_thread=secretary_thread, owner=owner)
        if reason:
            return self._decided(ts, routing.Route(routing.SILENT, routing.NOBODY, reason, "gate"))
        utterance = self.responder.utterance(event, web, owner=owner, secretary_thread=secretary_thread)
        utterance.loop_enabled = self.loop_desk is not None
        return self._decided(ts, self.router.classify(utterance, context))

    def _decided(self, ts: str, route: routing.Route) -> routing.Route:
        logger.info("route %s → %s/%s (%s, %s)", ts, route.intent, route.handler, route.method, route.reason)
        self.log.append((ts, route))
        del self.log[:-500]
        return route

    # ---------------------------------------------------------------- acting on it

    def on_mention(self, event: dict, client) -> routing.Route:
        return self.act(event, client, mention=True)

    def on_message(self, event: dict, client) -> routing.Route | None:
        if self.snapshots is not None:
            self.snapshots.on_event(event)  # threads already read stay current: edits, deletions (thread_snapshot.py)
        if self.archive:  # this channel's day changed: it is written as a document once the channel goes quiet
            self.archive.observe(event)
        self._observe_change(event)
        if self.loop_desk:  # record a consenting person's decision or request (loop_desk.py), off the event thread
            if self.background:
                threading.Thread(target=self.loop_desk.observe, args=(event, client), daemon=True).start()
            else:
                self.loop_desk.observe(event, client)
        if event.get("subtype") not in (None, "file_share"):
            return None  # edits, deletions, joins: nothing to answer
        if f"<@{self.me}" in (event.get("text") or ""):
            return None  # the app_mention event answers it
        gap_id = self.desk.answer_gap_id(event) if self.desk else None
        if gap_id:
            self.desk.on_answer(event, gap_id, client)
            return None
        is_dm = self.responder.is_direct_message(event)
        if not (is_dm or event.get("thread_ts")):
            return None  # a channel message that does not call the bot
        return self.act(event, client, mention=False)

    def act(self, event: dict, client, mention: bool) -> routing.Route:
        route = self.route(event, client, mention)
        if route.silent:
            if route.method == "gate" and route.reason.startswith("끊긴 글"):
                with self.lock:
                    self.deferred[(event.get("channel", ""), event.get("ts", ""))] = (event, client, mention)
                if self.hold is not None:  # the message event may not have reached the hold yet
                    self.hold.submit(event.get("channel", ""), Message.from_event(event, self.responder.directory, self.me, self.my_bot_id))
            return route
        self.execute(route, event, client)
        return route

    def execute(self, route: routing.Route, event: dict, client) -> None:
        if route.handler == routing.LOOP:
            self.loop_command(event, client)
        elif route.handler == routing.SECRETARY and self.bridge:
            self.bridge.handle(event, client)
        elif route.handler == routing.RESPONDER:
            self.responder.answer(event, client, route=route)

    def release(self, channel: str, message: Message, reason: str) -> None:
        """TruncationHold handed a held message on (edited, or the wait ended): route it again, with the edited text."""
        if reason == "ready":
            return
        key = (channel, message.ts)
        with self.lock:
            entry = self.deferred.pop(key, None)
            self.released.add(key)
            if len(self.released) > 5000:
                self.released.clear()
        logger.info("unfinished message %s/%s handed on (%s)%s", channel, message.ts, reason, "" if entry else ": not for the bot")
        if entry is None:
            return
        event, client, mention = entry
        raw = "".join(token.raw for token in message.tokens) or event.get("text", "")
        self.act({**event, "text": raw}, client, mention)

    def _observe_change(self, event: dict) -> None:
        """An edit or a deletion: the thread state forgets what that message was the evidence for."""
        subtype = event.get("subtype")
        if subtype not in ("message_changed", "message_deleted"):
            return
        inner = event.get("message") if subtype == "message_changed" else event.get("previous_message")
        inner = inner if isinstance(inner, dict) else {}
        ts = inner.get("ts") or event.get("deleted_ts") or ""
        thread_ts = inner.get("thread_ts") or event.get("thread_ts") or ts
        if ts:
            dropped = self.responder.states.forget_message(event.get("channel", ""), thread_ts, ts)
            if dropped:
                logger.info("thread %s: %s of %s dropped %d item(s)", thread_ts, subtype, ts, dropped)

    def loop_command(self, event: dict, client) -> bool:
        """"질문 있어?" / "인증할 것": the loop's questions and certifications, on demand."""
        if not self.loop_desk:
            return False
        from loop_desk import wants_certify_list, wants_questions

        text, user = self.responder.command_text(event.get("text")), event.get("user", "")
        thread_ts = event.get("thread_ts") or event["ts"]
        try:
            if wants_questions(text):
                if not self.loop_desk.ask_pending(client, only_user=user, channel=event["channel"], thread_ts=thread_ts):
                    client.chat_postMessage(channel=event["channel"], thread_ts=thread_ts, text="지금은 여쭤볼 게 없어요. 결정이나 요청이 쌓이면 먼저 여쭤볼게요.")
                return True
            if wants_certify_list(text):
                if not self.loop_desk.list_for_certification(client, user, event["channel"], thread_ts):
                    client.chat_postMessage(channel=event["channel"], thread_ts=thread_ts, text="인증을 기다리는 항목이 없어요.")
                return True
        except httpx.HTTPError as error:
            logger.warning("loop command failed", exc_info=True)
            client.chat_postMessage(channel=event["channel"], thread_ts=thread_ts, text=f"{ERROR_PREFIX} ({type(error).__name__}).")
            return True
        return False


def model_router(baryon: BaryonClient) -> routing.Router:
    """The router with the model classifying free-form messages (SLACK_ROUTER_MODEL, on by default): one chat call
    without retrieval and without a gap; any failure falls back to the rules."""
    if os.getenv("SLACK_ROUTER_MODEL", "true").strip().lower() in {"0", "false", "no", "off"}:
        return routing.Router()

    def complete(prompt: str) -> str:
        return baryon.ask([{"role": "user", "content": prompt}], baryon.config.bot_user_id, thread_id="slack:router",
                          use_retrieval=False, record_gap=False).text

    return routing.Router(complete)


def create_app(config: Config | None = None):
    from slack_bolt import App

    config = config or Config.from_env()
    app = App(token=os.environ["SLACK_BOT_TOKEN"])
    auth = app.client.auth_test()
    bot_user_id = auth["user_id"]
    baryon = BaryonClient(config)
    from doc_intake import DocumentIntake, IntakeConfig

    from doc_intake import IntakeLedger

    intake = DocumentIntake(IntakeConfig.from_env(), baryon, os.environ["SLACK_BOT_TOKEN"], ledger=IntakeLedger.from_env())
    from guide import Guide

    responder = Responder(config, baryon, bot_user_id, intake=intake, guide=Guide.from_env(baryon))
    responder.team_domain = slack_domain(auth.get("url", ""))
    responder.directory = UserDirectory.from_web(app.client)  # users:read; without it names stay ids
    responder.directory.remember(bot_user_id, auth.get("user", ""), is_bot=True)  # "너(baryon)" even without users:read
    responder.states = thread_state.ThreadStateStore.from_env()
    responder.read_threads = True
    desk = _create_desk(responder)
    bridge = _create_bridge()
    loop_desk = _create_loop_desk(baryon, bot_user_id)
    archive = _create_archive(baryon, bot_user_id)
    if bridge:
        bridge.bot_user_id = bot_user_id  # its text is normalized like the rest (slack_text), names from one cache
        bridge.states = responder.states  # one work state per thread: what the secretary confirmed is what the bot says
    for part in (loop_desk, archive, bridge):
        if part:
            part.directory = responder.directory  # one name cache for the whole bot
    dispatcher = Dispatcher(responder, model_router(baryon), bridge=bridge, loop_desk=loop_desk, desk=desk, archive=archive)
    dispatcher.my_bot_id = auth.get("bot_id", "")
    snapshots, hold = _create_snapshots(responder.directory, bot_user_id, auth.get("bot_id", ""), dispatcher.release)
    dispatcher.snapshots, dispatcher.hold = snapshots, hold
    responder.snapshots = snapshots
    if loop_desk:
        loop_desk.snapshots = snapshots  # the loop reads the same thread snapshots (edits, deletions, reactions)
        loop_desk.states = responder.states

    app.event("app_mention")(lambda event, client: dispatcher.on_mention(event, client))
    app.event("message")(lambda event, client: dispatcher.on_message(event, client))

    def on_reaction(event):
        """A reaction added or removed: the thread snapshot counts it, and the archived day is rewritten."""
        snapshots.on_event(event)
        if archive:
            item = event.get("item") or {}
            archive.observe_reaction(event, snapshots.thread_of(item.get("channel", ""), item.get("ts", "")))

    app.event("reaction_added")(on_reaction)
    app.event("reaction_removed")(on_reaction)

    if bridge:
        from secretary_bridge import ACTION_NO, ACTION_YES

        app.action(ACTION_YES)(action_listener(lambda body, client: bridge.answer_confirmation(body, client, True)))
        app.action(ACTION_NO)(action_listener(lambda body, client: bridge.answer_confirmation(body, client, False)))

    if loop_desk:
        import loop_desk as loop_ui

        app.action(re.compile(r"^loop_(pick_\d|none|write|skip|certify|hold|reject)$"))(
            action_listener(lambda body, client: loop_desk.on_button(body, client)))
        for passive in (loop_ui.CHECKS, loop_ui.CONTEST):  # ticking a box is read when 서명 is pressed
            app.action(passive)(lambda ack: ack())

        @app.view(loop_ui.VIEW_ANSWER)
        def on_answer_view(ack, body, client):
            result = loop_desk.on_answer_view(body, client)
            if "item_id" not in result:
                ack(**result)
                return
            ack()
            loop_desk.after_answer(client, body["user"]["id"], result["item_id"])

        @app.view(loop_ui.VIEW_REASON)
        def on_reason_view(ack, body, client):
            errors = loop_desk.on_reason_view(body, client)
            ack(**errors) if errors else ack()

    app.loop_desk = loop_desk
    app.archive = archive
    app.dispatcher = dispatcher
    app.snapshots, app.hold = snapshots, hold
    if desk:
        from proactive import ACTION_DISCARD, ACTION_DISMISS, ACTION_PUBLISH

        for action_id, handler in ((ACTION_PUBLISH, desk.on_publish), (ACTION_DISCARD, desk.on_discard),
                                   (ACTION_DISMISS, desk.on_dismiss)):
            app.action(action_id)(action_listener(handler))
    app.desk = desk  # main() starts the scheduler once the socket is up
    return app


def action_listener(handler):
    """Bolt fills listener arguments by parameter name, so the handler is captured in a closure."""
    def on_action(ack, body, client):
        ack()
        handler(body, client)

    return on_action


def _create_bridge():
    """The voice secretary in Slack (secretary_bridge.py) is on when SECRETARY_URL, _API_KEY and _SLACK_USERS are set."""
    from secretary_bridge import BridgeConfig, SecretaryBridge

    config = BridgeConfig.from_env()
    if not config.enabled:
        return None
    logger.info("secretary bridge on for %d Slack user(s) → %s", len(config.users), config.url)
    return SecretaryBridge(config)


def _create_snapshots(directory: UserDirectory, bot_user_id: str, bot_id: str = "", handed_on=None):
    """Thread snapshots kept current from events, and the hold for messages that stop mid-sentence. A held message
    is handed to ``handed_on(channel, message, reason)`` (Dispatcher.release) once edited or the wait ends."""
    from thread_snapshot import HoldConfig, SnapshotStore, TruncationHold

    def logged(channel: str, message: Message, reason: str) -> None:
        if reason != "ready":
            logger.info("unfinished message %s/%s handed on (%s)", channel, message.ts, reason)

    hold = TruncationHold(handed_on or logged, HoldConfig.from_env())
    return SnapshotStore(directory, bot_user_id, bot_id, hold=hold), hold


def _create_archive(baryon: BaryonClient, bot_user_id: str):
    """Slack conversations as documents (conversation_archive.py): on when SLACK_ARCHIVE_CHANNELS is set."""
    from conversation_archive import ArchiveConfig, ConversationArchive

    config = ArchiveConfig.from_env()
    if not config.enabled:
        return None
    logger.info("archiving Slack conversations of %s after %.0f quiet minutes",
                "every channel the bot is in" if config.every_channel else f"{len(config.channels)} channel(s)", config.idle_minutes)
    return ConversationArchive(config, baryon, bot_user_id)


def _create_loop_desk(baryon: BaryonClient, bot_user_id: str):
    """The question–certification loop (loop_desk.py): on unless LOOP_ENABLED=false."""
    if os.getenv("LOOP_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return None
    from loop_desk import LoopConfig, LoopDesk

    config = LoopConfig.from_env()
    logger.info("question–certification loop on (recording %s, rounds every %s min)",
                "everyone" if config.everyone else f"{len(config.users)} user(s)", config.interval_minutes)
    return LoopDesk(config, baryon, bot_user_id)


def _create_desk(responder: Responder):
    """Proactive mode (proactive.py) is on when PROACTIVE_CHANNEL is set."""
    from proactive import GapDesk, KnowledgeClient, ProactiveConfig, ProactiveState

    config = ProactiveConfig.from_env()
    if not config.enabled:
        return None
    responder.baryon.auth.bot_roles = config.service_roles  # dev mode only; in OIDC the token's roles apply
    knowledge = KnowledgeClient(responder.baryon)
    return GapDesk(config, responder, knowledge, ProactiveState(config.state_path))


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    app = create_app()
    logger.info("baryon-ax Slack bot connecting over Socket Mode → %s", Config.from_env().api_url)
    if app.desk:
        from proactive import start_scheduler

        start_scheduler(app.desk, app.client)
        logger.info("proactive mode on: asking about knowledge gaps in %s every %.0f min",
                    app.desk.config.channel, app.desk.config.interval_minutes)
    if app.archive:
        from conversation_archive import start_archive_scheduler

        start_archive_scheduler(app.archive, app.client)
    if app.loop_desk and app.loop_desk.config.records:
        from loop_desk import start_loop_scheduler

        start_loop_scheduler(app.loop_desk, app.client)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
