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

from baryon_auth import ApiAuth, TokenSource

logger = logging.getLogger("baryon.slack")

MENTION_RE = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>")
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

    def ask(self, messages: list[dict], slack_user: str, thread_id: str, use_retrieval: bool = True) -> Answer:
        body: dict[str, Any] = {"messages": messages, "stream": False, "thread_id": thread_id}
        if not use_retrieval:
            body["use_retrieval"] = False
        if self.config.model:
            body["model"] = self.config.model
        response = self.as_user("POST", "/v1/chat/completions", slack_user, json=body)
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        grounding = data.get("grounding") or {}
        return Answer(text=text, citations=data.get("citations") or [], trace_id=data.get("id", ""),
                      answered=bool(grounding.get("answered", True)))

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


def strip_mentions(text: str | None) -> str:
    return MENTION_RE.sub("", text or "").strip()


def to_mrkdwn(markdown: str) -> str:
    """Convert the model's CommonMark to Slack mrkdwn (bold, headings, links)."""
    # Slack only renders *bold* when the markers touch whitespace or punctuation, so
    # Korean text glued to them ("**15일**입니다") gets zero-width spaces around it.
    bold = "​*\\1*​"
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", markdown, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", bold, text)
    text = re.sub(r"__(.+?)__", bold, text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)
    return text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def thread_messages(replies: list[dict], bot_user_id: str, max_turns: int) -> list[dict]:
    """Slack thread → chat messages; the bot's own posts become assistant turns."""
    messages: list[dict] = []
    for reply in replies:
        from_bot = reply.get("user") == bot_user_id or bool(reply.get("bot_id"))
        if reply.get("subtype") and not from_bot:
            continue
        content = (reply.get("text") or "").strip() if from_bot else strip_mentions(reply.get("text"))
        if not content or (from_bot and content.startswith(STATUS_PREFIXES)):
            continue
        messages.append({"role": "assistant" if from_bot else "user", "content": content})
    messages = messages[-max_turns:]
    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)
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


def build_blocks(answer: Answer, snippets: dict[tuple[str, int], str], max_sources: int, note: str = "") -> list[dict]:
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": part}} for part in answer_sections(to_mrkdwn(answer.text))]
    if note:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": note}]})
    lines = source_lines(answer.citations, snippets, max_sources) if answer.answered else []
    blocks.append({"type": "divider"})
    if not answer.answered:
        # Retrieval may still return passages, but they did not answer the question: listing them as sources misleads.
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
            ":mag: 문서에서 답을 찾지 못해 지식 공백으로 남겼습니다. 담당자가 답하면 다음부터는 바로 답합니다."}]})
    elif lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip("*:bookmark_tabs: 근거*\n" + "\n".join(lines), SECTION_LIMIT)}})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ":warning: 근거 문서를 찾지 못했습니다. 문서에 없는 내용일 수 있습니다."}]})
    if answer.trace_id:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"baryon-ax · trace `{answer.trace_id}`"}]})
    return blocks


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
                 intake=None) -> None:
        self.config = config
        self.baryon = baryon
        self.bot_user_id = bot_user_id
        self.subscriptions = subscriptions or ThreadSubscriptions()
        self.intake = intake  # doc_intake.DocumentIntake: "문서로 추가" requests
        self.team_domain = ""  # the bot's workspace ("acme" of acme.slack.com); links elsewhere cannot be read

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
                    found = web.conversations_replies(channel=channel, ts=ts, limit=50).get("messages", [])
                    lines = [f"- {strip_mentions(m.get('text'))}" for m in found if strip_mentions(m.get("text"))]
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
            replies = web.conversations_replies(channel=channel, ts=thread_ts, limit=50).get("messages", [])
        except Exception as error:  # missing channels:history scope, private channel, …
            logger.info("thread history unavailable (%s); answering the single message", error)
            replies = []
        messages = thread_messages(replies, self.bot_user_id, self.config.thread_turns)
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": fallback})
        return messages

    def answer(self, event: dict, web) -> None:
        channel, user = event["channel"], event.get("user", "unknown")
        question = strip_mentions(event.get("text"))
        thread_ts = event.get("thread_ts") or event["ts"]
        if not question and event.get("files"):
            return  # files posted without a question: wait for the question ("분석해줘") that usually follows
        if not question:
            usage = ("무엇을 찾아드릴까요? 질문을 그대로 보내 주세요. 예: `연차휴가는 며칠이야?`" if event.get("channel_type") == "im"
                     else "무엇을 찾아드릴까요? `@봇 질문` 형태로 물어봐 주세요. 예: `@baryon 연차휴가는 며칠이야?`")
            web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=usage)
            return
        if self.config.subscribe_threads:
            self.subscriptions.add(channel, thread_ts)
        if self.intake and self.intake.config.enabled:
            from doc_intake import thread_files, wants_file_reading, wants_intake

            if wants_intake(question):
                self.add_documents(event, web, question)
                return
            # "분석하라", "엑셀 읽어봐" in a thread where people shared files: read those files instead of guessing.
            if wants_file_reading(question) and (event.get("thread_ts") or event.get("files")):
                context = self._intake_context(web, event)
                if thread_files(context + [event], self.bot_user_id):
                    self.add_documents(event, web, question, analyze=True, messages=context)
                    return
        self._react(web, channel, event["ts"], "eyes", add=True)
        # Read the thread before posting the placeholder so it is not part of the history.
        messages = self._history(web, channel, thread_ts, question) if event.get("thread_ts") else [{"role": "user", "content": question}]
        messages, link_notes = self.expand_links(web, messages)
        pending = web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PENDING_TEXT)
        try:
            result = self.baryon.ask(messages, user, thread_id=f"slack:{channel}:{thread_ts}")
            snippets = (self.baryon.cited_snippets(question, result.citations[: self.config.max_sources * 2], user)
                        if self.config.show_snippets and result.citations and result.answered else {})
            note = " ".join(link_notes + ([ATTACHMENT_NOTE] if event.get("files") else []))
            web.chat_update(channel=channel, ts=pending["ts"], text=_clip(result.text, TEXT_LIMIT),
                            blocks=build_blocks(result, snippets, self.config.max_sources, note=note))
            self._react(web, channel, event["ts"], "white_check_mark", add=True)
        except Exception as error:
            logger.exception("baryon-ax answer failed")
            detail = f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
            web.chat_update(channel=channel, ts=pending["ts"], text=f"{ERROR_PREFIX} ({detail}). 잠시 후 다시 시도해 주세요.")
            self._react(web, channel, event["ts"], "x", add=True)
        finally:
            self._react(web, channel, event["ts"], "eyes", add=False)

    def _intake_context(self, web, event: dict) -> list[dict]:
        """The thread, or for a top-level request the person's message just above it."""
        channel = event["channel"]
        try:
            if event.get("thread_ts"):
                return web.conversations_replies(channel=channel, ts=event["thread_ts"], limit=200).get("messages", [])
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
            result = self.intake.collect(messages, event, self.bot_user_id, strip_mentions, user)
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

    @staticmethod
    def _react(web, channel: str, ts: str, name: str, add: bool) -> None:
        try:
            (web.reactions_add if add else web.reactions_remove)(channel=channel, timestamp=ts, name=name)
        except Exception:
            pass  # reactions:write is optional


# --------------------------------------------------------------------------- slack app


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
    responder = Responder(config, baryon, bot_user_id, intake=intake)
    responder.team_domain = slack_domain(auth.get("url", ""))
    desk = _create_desk(responder)

    @app.event("app_mention")
    def on_mention(event, client):
        responder.answer(event, client)

    @app.event("message")
    def on_message(event, client):
        gap_id = desk.answer_gap_id(event) if desk else None
        if gap_id:
            desk.on_answer(event, gap_id, client)
        elif responder.is_direct_message(event) or responder.should_follow_up(event):
            responder.answer(event, client)

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
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
