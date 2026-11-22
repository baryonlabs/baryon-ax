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

    def ask(self, messages: list[dict], slack_user: str, thread_id: str) -> Answer:
        body: dict[str, Any] = {"messages": messages, "stream": False, "thread_id": thread_id}
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


# --------------------------------------------------------------------------- formatting


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


def build_blocks(answer: Answer, snippets: dict[tuple[str, int], str], max_sources: int) -> list[dict]:
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": _clip(to_mrkdwn(answer.text), SECTION_LIMIT)}}]
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

    def __init__(self, config: Config, baryon: BaryonClient, bot_user_id: str, subscriptions: ThreadSubscriptions | None = None) -> None:
        self.config = config
        self.baryon = baryon
        self.bot_user_id = bot_user_id
        self.subscriptions = subscriptions or ThreadSubscriptions()

    def should_follow_up(self, event: dict) -> bool:
        """Plain thread message (no mention) in a subscribed thread."""
        if not self.config.subscribe_threads or event.get("subtype") or event.get("bot_id"):
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
        if not question:
            web.chat_postMessage(channel=channel, thread_ts=thread_ts,
                                 text="무엇을 찾아드릴까요? `@봇 질문` 형태로 물어봐 주세요. 예: `@baryon 연차휴가는 며칠이야?`")
            return
        if self.config.subscribe_threads:
            self.subscriptions.add(channel, thread_ts)
        self._react(web, channel, event["ts"], "eyes", add=True)
        # Read the thread before posting the placeholder so it is not part of the history.
        messages = self._history(web, channel, thread_ts, question) if event.get("thread_ts") else [{"role": "user", "content": question}]
        pending = web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PENDING_TEXT)
        try:
            result = self.baryon.ask(messages, user, thread_id=f"slack:{channel}:{thread_ts}")
            snippets = self.baryon.snippets(question, user) if self.config.show_snippets and result.citations and result.answered else {}
            web.chat_update(channel=channel, ts=pending["ts"], text=result.text,
                            blocks=build_blocks(result, snippets, self.config.max_sources))
            self._react(web, channel, event["ts"], "white_check_mark", add=True)
        except Exception as error:
            logger.exception("baryon-ax answer failed")
            detail = f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
            web.chat_update(channel=channel, ts=pending["ts"], text=f"{ERROR_PREFIX} ({detail}). 잠시 후 다시 시도해 주세요.")
            self._react(web, channel, event["ts"], "x", add=True)
        finally:
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
    bot_user_id = app.client.auth_test()["user_id"]
    responder = Responder(config, BaryonClient(config), bot_user_id)
    desk = _create_desk(responder)

    @app.event("app_mention")
    def on_mention(event, client):
        responder.answer(event, client)

    @app.event("message")
    def on_message(event, client):
        gap_id = desk.answer_gap_id(event) if desk else None
        if gap_id:
            desk.on_answer(event, gap_id, client)
        elif responder.should_follow_up(event):
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
