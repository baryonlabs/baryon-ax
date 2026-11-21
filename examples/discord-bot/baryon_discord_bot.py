"""Discord bot for baryon-ax: grounded answers with sources, and a desk that asks people first.

The Discord counterpart of ``examples/slack-bot``:

* ``@bot question`` in a channel opens a thread on that message and answers there:
  the answer, a "근거" (sources) embed with document, chunk and a quoted passage, and the trace id.
* Later messages in that thread are answered without another mention; the thread
  is sent as conversation history so follow-up questions work.
* Proactive mode (``PROACTIVE_CHANNEL``): questions the bot could not answer
  (baryon-ax knowledge gaps) are posted to a desk channel on a schedule.  A reply in
  the desk thread is saved as a tacit-knowledge draft for its author, the bot asks
  before publishing ([문서로 등록] [취소]), and after publishing it returns to the
  original thread once and answers again with a source.

Uses the Discord Gateway (an outbound WebSocket), so no public URL is needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from baryon_auth import ApiAuth, TokenSource

logger = logging.getLogger("baryon.discord")

MENTION_RE = re.compile(r"<@[!&]?\d+>")
METADATA_LINE_RE = re.compile(r"^\s*>\s*[^:\n]{1,20}:\s.*$", re.MULTILINE)
CONTENT_LIMIT = 1900      # Discord message content limit is 2000
EMBED_LIMIT = 4000        # embed description limit is 4096
SNIPPET_CHARS = 180
PENDING_TEXT = "⏳ 문서를 찾아 답변을 준비하고 있습니다…"
ERROR_PREFIX = "❌ 답변을 만들지 못했습니다"
STATUS_PREFIXES = (PENDING_TEXT, ERROR_PREFIX)
CUSTOM_ID_PREFIX = "baryon:"
REASON_LABELS = {"no_citations": "근거 문서 없음", "refusal": "답을 찾지 못함",
                 "no_retrieval": "검색 없이 답함", "low_confidence": "확신 낮음"}


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- config


@dataclass
class Config:
    api_url: str = "http://127.0.0.1:8000"
    organization_id: str = "local-org"
    roles: str = "member"
    model: str = ""
    timeout: float = 120.0
    bot_user_id: str = "discord-bot"   # the bot's own principal in dev mode (AUTH_MODE=dev)
    max_sources: int = 4
    thread_turns: int = 10
    subscribe_threads: bool = True
    show_snippets: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_url=os.getenv("BARYON_API_URL", cls.api_url).rstrip("/"),
            organization_id=os.getenv("BARYON_ORGANIZATION_ID", cls.organization_id),
            roles=os.getenv("BARYON_ROLES", cls.roles),
            model=os.getenv("BARYON_MODEL", ""),
            timeout=float(os.getenv("BARYON_TIMEOUT", cls.timeout)),
            max_sources=int(os.getenv("DISCORD_MAX_SOURCES", cls.max_sources)),
            thread_turns=int(os.getenv("DISCORD_THREAD_TURNS", cls.thread_turns)),
            subscribe_threads=_flag("DISCORD_SUBSCRIBE_THREADS", True),
            show_snippets=_flag("DISCORD_SHOW_SNIPPETS", True),
        )


@dataclass
class ProactiveConfig:
    channel: int = 0                 # desk channel id; 0 disables proactive mode
    interval_minutes: float = 30.0
    hours: str = ""                  # "9-18" = only ask between 09:00 and 17:59 local time
    weekdays_only: bool = False
    max_per_run: int = 3
    min_occurrences: int = 1
    platform_only: bool = False      # only gaps from questions asked on Discord
    mention: str = ""                # prepended to each ask, e.g. "@here" or "<@&ROLE_ID>"
    service_roles: str = "admin"     # dev mode: the bot's own roles; listing/dismissing gaps needs admin
    state_path: str = ".data/proactive_state.json"
    reanswer: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.channel)

    @classmethod
    def from_env(cls) -> "ProactiveConfig":
        return cls(
            channel=int(os.getenv("PROACTIVE_CHANNEL", "0") or 0),
            interval_minutes=float(os.getenv("PROACTIVE_INTERVAL_MINUTES", cls.interval_minutes)),
            hours=os.getenv("PROACTIVE_HOURS", "").strip(),
            weekdays_only=_flag("PROACTIVE_WEEKDAYS_ONLY", False),
            max_per_run=int(os.getenv("PROACTIVE_MAX_PER_RUN", cls.max_per_run)),
            min_occurrences=int(os.getenv("PROACTIVE_MIN_OCCURRENCES", cls.min_occurrences)),
            platform_only=_flag("PROACTIVE_DISCORD_ONLY", False),
            mention=os.getenv("PROACTIVE_MENTION", "").strip(),
            service_roles=os.getenv("PROACTIVE_SERVICE_ROLES", cls.service_roles),
            state_path=os.getenv("PROACTIVE_STATE_PATH", cls.state_path),
            reanswer=_flag("PROACTIVE_REANSWER", True),
        )

    def is_active(self, now: datetime) -> bool:
        if self.weekdays_only and now.weekday() >= 5:
            return False
        if self.hours:
            start, _, end = self.hours.partition("-")
            if not (int(start) <= now.hour < int(end or 24)):
                return False
        return True


# --------------------------------------------------------------------------- baryon-ax client


@dataclass
class Answer:
    text: str
    citations: list[dict] = field(default_factory=list)
    trace_id: str = ""


class BaryonClient:
    """Chat, search, knowledge-gap and tacit-knowledge endpoints. Synchronous; callers use asyncio.to_thread.

    Each Discord user is its own baryon-ax principal (``discord:<id>``), sent as
    ``X-On-Behalf-Of`` so permissions, gaps and audit logs are per person and the
    bot is recorded as ``acting_service``.  Gap listing and dismissal are admin
    calls the bot makes as itself (see baryon_auth.py).
    """

    def __init__(self, config: Config, service_roles: str = "admin", http: httpx.Client | None = None,
                 tokens: TokenSource | None = None) -> None:
        self.config = config
        self.http = http or httpx.Client(base_url=config.api_url, timeout=config.timeout)
        self.auth = ApiAuth(tokens or TokenSource.from_env(self.http), config.organization_id,
                            config.bot_user_id, service_roles)

    def as_user(self, method: str, path: str, discord_user: str | int, **kwargs: Any) -> httpx.Response:
        """A call made for a Discord user."""
        return self.auth.request(self.http, method, path, on_behalf_of=f"discord:{discord_user}",
                                 roles=self.config.roles, **kwargs)

    def as_bot(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """A call the bot makes as itself (no On-Behalf-Of header)."""
        return self.auth.request(self.http, method, path, **kwargs)

    def ask(self, messages: list[dict], discord_user: str | int, thread_id: str) -> Answer:
        body: dict[str, Any] = {"messages": messages, "stream": False, "thread_id": thread_id}
        if self.config.model:
            body["model"] = self.config.model
        response = self.as_user("POST", "/v1/chat/completions", discord_user, json=body)
        response.raise_for_status()
        data = response.json()
        return Answer(text=data["choices"][0]["message"]["content"], citations=data.get("citations") or [],
                      trace_id=data.get("id", ""))

    def snippets(self, query: str, discord_user: str | int) -> dict[tuple[str, int], str]:
        if len(query.strip()) < 2:
            return {}
        try:
            response = self.as_user("GET", "/v1/documents/search", discord_user, params={"q": query})
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("document search failed; answering without snippets", exc_info=True)
            return {}
        return {(item["document_id"], int(item.get("chunk", 0))): str(item.get("content") or "")
                for item in response.json().get("data", [])}

    def open_gaps(self, limit: int = 100) -> list[dict]:
        response = self.as_bot("GET", "/v1/admin/gaps", params={"status": "open", "limit": limit})
        response.raise_for_status()
        return response.json().get("data", [])

    def dismiss_gap(self, gap_id: str) -> None:
        self.as_bot("POST", f"/v1/admin/gaps/{gap_id}/dismiss").raise_for_status()

    def save_answer(self, discord_user: str | int, question: str, answer: str, gap_id: str) -> dict:
        body = {"question": question[:500], "answer": answer[:20000], "gap_id": gap_id, "source": "discord"}
        response = self.as_user("POST", "/v1/tacit/entries", discord_user, json=body)
        response.raise_for_status()
        return response.json()

    def publish(self, discord_user: str | int, entry_id: str) -> dict:
        response = self.as_user("POST", f"/v1/tacit/entries/{entry_id}/publish", discord_user)
        response.raise_for_status()
        return response.json()


# --------------------------------------------------------------------------- formatting


def strip_mentions(text: str | None) -> str:
    return MENTION_RE.sub("", text or "").strip()


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def quote_text(content: str) -> str:
    """Chunk text for a one-line quote, without "> key: value" metadata lines."""
    return " ".join(METADATA_LINE_RE.sub("", content).split())


def source_lines(citations: list[dict], snippets: dict[tuple[str, int], str], max_sources: int) -> list[str]:
    seen: set[tuple[str, int]] = set()
    lines: list[str] = []
    for item in citations:
        key = (item.get("document_id", ""), int(item.get("chunk", 0)))
        if key in seen:
            continue
        seen.add(key)
        line = f"**{len(lines) + 1}. {item.get('title') or key[0]}** · chunk {key[1]}"
        snippet = quote_text(snippets.get(key, ""))
        if snippet:
            line += f"\n> {clip(snippet, SNIPPET_CHARS)}"
        lines.append(line)
        if len(lines) >= max_sources:
            break
    return lines


def sources_embed(answer: Answer, snippets: dict[tuple[str, int], str], max_sources: int) -> dict:
    """Embed as a dict (discord.Embed.from_dict) so it is testable without a gateway."""
    lines = source_lines(answer.citations, snippets, max_sources)
    if lines:
        embed = {"title": "📑 근거", "description": clip("\n".join(lines), EMBED_LIMIT), "color": 0x1E293B}
    else:
        embed = {"description": "⚠️ 근거 문서를 찾지 못했습니다. 문서에 없는 내용일 수 있습니다.", "color": 0xB45309}
    if answer.trace_id:
        embed["footer"] = {"text": f"baryon-ax · trace {answer.trace_id}"}
    return embed


def thread_messages(history: list[Any], bot_id: int, max_turns: int) -> list[dict]:
    """Discord messages (oldest first) → chat messages; the bot's own posts become assistant turns."""
    messages: list[dict] = []
    for message in history:
        from_bot = getattr(message.author, "id", None) == bot_id
        if getattr(message.author, "bot", False) and not from_bot:
            continue
        content = (message.content or "").strip() if from_bot else strip_mentions(message.content)
        if not content or (from_bot and content.startswith(STATUS_PREFIXES)):
            continue
        messages.append({"role": "assistant" if from_bot else "user", "content": content})
    messages = messages[-max_turns:]
    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)
    return messages


def button(label: str, custom_id: str, style: str = "secondary") -> dict:
    return {"label": label, "custom_id": custom_id, "style": style}


# --------------------------------------------------------------------------- thread subscriptions


class ThreadSubscriptions:
    def __init__(self, ttl_seconds: float = 24 * 3600) -> None:
        self.ttl = ttl_seconds
        self._threads: dict[int, float] = {}
        self._lock = threading.Lock()

    def add(self, thread_id: int) -> None:
        with self._lock:
            self._threads[thread_id] = time.monotonic()

    def __contains__(self, thread_id: int) -> bool:
        with self._lock:
            started = self._threads.get(thread_id)
            if started is None or time.monotonic() - started > self.ttl:
                self._threads.pop(thread_id, None)
                return False
            return True


# --------------------------------------------------------------------------- answering


class Responder:
    """discord.py-free core: works on duck-typed message/thread objects so it is testable with fakes."""

    def __init__(self, config: Config, baryon: BaryonClient, bot_id: int, ui=None) -> None:
        self.config = config
        self.baryon = baryon
        self.bot_id = bot_id
        self.ui = ui or DictUI()
        self.subscriptions = ThreadSubscriptions()

    def is_thread(self, channel: Any) -> bool:
        return getattr(channel, "parent_id", None) is not None

    def should_answer(self, message: Any) -> bool:
        if getattr(message.author, "bot", False):
            return False
        mentioned = any(getattr(user, "id", None) == self.bot_id for user in getattr(message, "mentions", []))
        if mentioned:
            return True
        channel = message.channel
        return self.config.subscribe_threads and self.is_thread(channel) and channel.id in self.subscriptions

    async def answer(self, message: Any) -> None:
        question = strip_mentions(message.content)
        if not question:
            await message.reply("무엇을 찾아드릴까요? `@봇 질문` 형태로 물어봐 주세요. 예: `@baryon 연차휴가는 며칠이야?`")
            return
        in_thread = self.is_thread(message.channel)
        thread = message.channel if in_thread else await message.create_thread(name=clip(question, 90))
        self.subscriptions.add(thread.id)
        await self._react(message, "👀")
        history = await self._history(thread) if in_thread else []
        messages = history if history and history[-1]["role"] == "user" else history + [{"role": "user", "content": question}]
        pending = await thread.send(PENDING_TEXT)
        thread_key = f"discord:{getattr(thread, 'parent_id', None) or message.channel.id}:{thread.id}"
        try:
            result = await asyncio.to_thread(self.baryon.ask, messages, message.author.id, thread_key)
            snippets = (await asyncio.to_thread(self.baryon.snippets, question, message.author.id)
                        if self.config.show_snippets and result.citations else {})
            await pending.edit(content=clip(result.text, CONTENT_LIMIT),
                               embed=self.ui.embed(sources_embed(result, snippets, self.config.max_sources)))
            await self._react(message, "✅")
        except Exception as error:
            logger.exception("baryon-ax answer failed")
            detail = f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
            await pending.edit(content=f"{ERROR_PREFIX} ({detail}). 잠시 후 다시 시도해 주세요.")
            await self._react(message, "❌")
        finally:
            await self._react(message, "👀", remove=True)

    async def _history(self, thread: Any) -> list[dict]:
        try:
            recent = [message async for message in thread.history(limit=50)]
        except Exception as error:  # missing Read Message History permission, …
            logger.info("thread history unavailable (%s)", error)
            return []
        recent.reverse()  # history() is newest first
        # A thread opened from a message does not contain that message; it is the first question.
        starter = getattr(thread, "starter_message", None)
        if starter is None and getattr(thread, "parent", None) is not None:
            try:
                starter = await thread.parent.fetch_message(thread.id)
            except Exception:
                starter = None
        if starter is not None and len(recent) < 50:
            recent.insert(0, starter)
        return thread_messages(recent, self.bot_id, self.config.thread_turns)

    async def _react(self, message: Any, emoji: str, remove: bool = False) -> None:
        try:
            if remove:
                await message.remove_reaction(emoji, self.ui.me)
            else:
                await message.add_reaction(emoji)
        except Exception:
            pass  # Add Reactions permission is optional


# --------------------------------------------------------------------------- proactive desk


class ProactiveState:
    """gap_id → where it was asked and what happened; persisted as JSON so restarts do not ask twice."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.asks: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                self.asks = json.loads(self.path.read_text(encoding="utf-8")).get("asks", {})
            except (OSError, ValueError):
                logger.warning("could not read %s; starting with empty state", self.path)

    def record(self, gap_id: str, **fields: Any) -> None:
        with self._lock:
            self.asks.setdefault(gap_id, {}).update(fields)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"asks": self.asks}, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, gap_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self.asks.get(gap_id) or {})

    def __contains__(self, gap_id: str) -> bool:
        with self._lock:
            return gap_id in self.asks

    def gap_for_thread(self, thread_id: int) -> str | None:
        with self._lock:
            for gap_id, item in self.asks.items():
                if item.get("thread") == thread_id:
                    return gap_id
        return None


def ask_text(gap: dict, mention: str = "") -> str:
    reasons = ", ".join(REASON_LABELS.get(reason, reason) for reason in gap.get("reasons") or [])
    asked_by = str(gap.get("user_id", ""))
    who = f"<@{asked_by.removeprefix('discord:')}>" if asked_by.startswith("discord:") else "누군가"
    count = int(gap.get("occurrences") or 1)
    lead = (mention + " ") if mention else ""
    quoted = "\n".join("> " + line for line in str(gap.get("question", "")).splitlines() or [""])
    context = f"-# {who} 님이 물어봄" + (f" · {count}번 나온 질문" if count > 1 else "") + (f" · {reasons}" if reasons else "")
    return (f"{lead}🙋 **제가 답하지 못한 질문이 있어요.** 아시는 분이 아래 스레드에 답을 남겨 주시면 문서로 정리해 둘게요.\n"
            f"{quoted}\n{context}")


class GapDesk:
    def __init__(self, config: ProactiveConfig, responder: Responder, state: ProactiveState, channels) -> None:
        self.config = config
        self.responder = responder
        self.baryon = responder.baryon
        self.ui = responder.ui
        self.state = state
        self.channels = channels  # async callable: channel id → channel/thread object

    async def run_once(self, now: datetime | None = None) -> int:
        if not self.config.enabled or not self.config.is_active(now or datetime.now()):
            return 0
        try:
            gaps = await asyncio.to_thread(self.baryon.open_gaps)
        except Exception:
            logger.exception("could not list knowledge gaps")
            return 0
        fresh = [gap for gap in gaps
                 if gap.get("id") not in self.state and int(gap.get("occurrences") or 1) >= self.config.min_occurrences
                 and (not self.config.platform_only or str(gap.get("user_id", "")).startswith("discord:"))]
        fresh.sort(key=lambda gap: (int(gap.get("occurrences") or 1), gap.get("last_seen", "")), reverse=True)
        desk = await self.channels(self.config.channel)
        asked = 0
        for gap in fresh[: self.config.max_per_run]:
            view = self.ui.view([button("해당 없음", f"{CUSTOM_ID_PREFIX}dismiss:{gap['id']}")])
            posted = await desk.send(ask_text(gap, self.config.mention), view=view)
            thread = await posted.create_thread(name=clip(f"답: {gap.get('question', '')}", 90))
            self.state.record(gap["id"], message=posted.id, thread=thread.id, status="asked",
                              question=gap.get("question", ""), asked_by=gap.get("user_id", ""),
                              origin_thread=gap.get("thread_id") or "", asked_at=datetime.now().isoformat(timespec="seconds"))
            asked += 1
        if asked:
            logger.info("asked about %d knowledge gap(s)", asked)
        return asked

    def answer_gap_id(self, message: Any) -> str | None:
        if getattr(message.author, "bot", False) or not self.responder.is_thread(message.channel):
            return None
        if any(getattr(user, "id", None) == self.responder.bot_id for user in getattr(message, "mentions", [])):
            return None  # a mention in the desk thread is a question, not an answer
        return self.state.gap_for_thread(message.channel.id)

    async def on_answer(self, message: Any, gap_id: str) -> None:
        item = self.state.get(gap_id)
        if item.get("status") in {"published", "dismissed"}:
            await message.reply("이 질문은 이미 정리됐어요. 고마워요!")
            return
        try:
            entry = await asyncio.to_thread(self.baryon.save_answer, message.author.id, item.get("question", ""),
                                            (message.content or "").strip(), gap_id)
        except Exception as error:
            logger.exception("saving tacit answer failed")
            await message.reply(f"❌ 답변을 저장하지 못했습니다 ({_detail(error)}).")
            return
        self.state.record(gap_id, status="answered")
        value = f"{entry['id']}:{gap_id}"
        view = self.ui.view([button("문서로 등록", f"{CUSTOM_ID_PREFIX}publish:{value}", "success"),
                             button("취소", f"{CUSTOM_ID_PREFIX}discard:{value}")])
        await message.reply(f"<@{message.author.id}> 님 답변을 초안으로 저장했어요. 문서로 등록하면 다음부터 이 질문에 "
                            "근거와 함께 답할 수 있습니다. 등록할까요?\n-# 답변을 쓴 사람만 등록할 수 있어요.", view=view)

    async def on_button(self, custom_id: str, user_id: int, message: Any, respond) -> bool:
        """Handle a desk button. respond(text) sends an ephemeral reply. Returns False for unknown ids."""
        if not custom_id.startswith(CUSTOM_ID_PREFIX):
            return False
        action, _, rest = custom_id[len(CUSTOM_ID_PREFIX):].partition(":")
        if action == "dismiss":
            return await self._dismiss(rest, user_id, message, respond)
        entry_id, _, gap_id = rest.partition(":")
        if action == "discard":
            self.state.record(gap_id, status="asked")
            await message.edit(content=f"등록하지 않았어요 (<@{user_id}> 님이 취소). 다시 답하려면 이 스레드에 새로 남겨 주세요.", view=None)
            return True
        if action != "publish":
            return False
        try:
            published = await asyncio.to_thread(self.baryon.publish, user_id, entry_id)
        except httpx.HTTPStatusError as error:
            await respond("답변을 쓴 사람만 등록할 수 있어요." if error.response.status_code == 403
                          else f"❌ 등록하지 못했습니다 ({_detail(error)}).")
            return True
        self.state.record(gap_id, status="published", entry_id=entry_id, published_by=user_id)
        await message.edit(content=f"✅ <@{user_id}> 님 답변을 문서로 등록했습니다. 고마워요!", view=None)
        item = self.state.get(gap_id)
        await self._update_desk_post(item, f"✅ **답이 등록됐어요** (<@{user_id}> 님)\n> {item.get('question', '')}")
        if self.config.reanswer and not item.get("reanswered") and await self._reanswer(item):
            self.state.record(gap_id, reanswered=True)
        logger.info("published tacit answer %s for gap %s (%s)", entry_id, gap_id, published.get("published_document_id"))
        return True

    async def _dismiss(self, gap_id: str, user_id: int, message: Any, respond) -> bool:
        try:
            await asyncio.to_thread(self.baryon.dismiss_gap, gap_id)
        except Exception as error:
            logger.exception("dismiss failed")
            await respond(f"❌ 처리하지 못했습니다 ({_detail(error)}).")
            return True
        item = self.state.get(gap_id)
        self.state.record(gap_id, status="dismissed", dismissed_by=user_id)
        await message.edit(content=f"➖ <@{user_id}> 님이 '해당 없음'으로 닫았어요.\n> {item.get('question', '')}", view=None)
        return True

    async def _update_desk_post(self, item: dict, text: str) -> None:
        try:
            desk = await self.channels(self.config.channel)
            post = await desk.fetch_message(item["message"])
            await post.edit(content=text, view=None)
        except Exception:
            logger.info("could not update desk post", exc_info=True)

    async def _reanswer(self, item: dict) -> bool:
        parts = str(item.get("origin_thread") or "").split(":")
        asked_by = str(item.get("asked_by") or "").removeprefix("discord:")
        if len(parts) != 3 or parts[0] != "discord" or not asked_by:
            return False  # the question came from somewhere other than Discord
        question = item.get("question", "")
        try:
            result = await asyncio.to_thread(self.baryon.ask, [{"role": "user", "content": question}], asked_by,
                                             item["origin_thread"])
            snippets = (await asyncio.to_thread(self.baryon.snippets, question, asked_by)
                        if self.responder.config.show_snippets and result.citations else {})
            thread = await self.channels(int(parts[2]))
            lead = f"<@{asked_by}> 지난번에 답하지 못했던 질문에 동료가 답을 남겨 줘서, 다시 답해 드려요.\n\n"
            await thread.send(clip(lead + result.text, CONTENT_LIMIT),
                              embed=self.ui.embed(sources_embed(result, snippets, self.responder.config.max_sources)))
            return True
        except Exception:
            logger.exception("re-answer failed")
            return False


def _detail(error: Exception) -> str:
    return f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__


# --------------------------------------------------------------------------- discord.py adapter


class DictUI:
    """Test stand-in: embeds and views stay plain dicts/lists."""
    me = None

    def embed(self, data: dict) -> dict:
        return data

    def view(self, buttons: list[dict]) -> list[dict]:
        return buttons


class DiscordUI:
    def __init__(self, client) -> None:
        self.client = client

    @property
    def me(self):
        return self.client.user

    def embed(self, data: dict):
        import discord
        return discord.Embed.from_dict(data)

    def view(self, buttons: list[dict]):
        import discord
        styles = {"success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger,
                  "secondary": discord.ButtonStyle.secondary}
        view = discord.ui.View(timeout=None)
        for item in buttons:
            view.add_item(discord.ui.Button(label=item["label"], custom_id=item["custom_id"],
                                            style=styles.get(item["style"], discord.ButtonStyle.secondary)))
        return view


def create_client(config: Config | None = None, proactive: ProactiveConfig | None = None):
    import discord

    config = config or Config.from_env()
    proactive = proactive or ProactiveConfig.from_env()
    intents = discord.Intents.default()
    intents.message_content = True  # privileged: needed for follow-ups without a mention
    client = discord.Client(intents=intents)
    baryon = BaryonClient(config, proactive.service_roles)
    state: dict[str, Any] = {}

    async def channels(channel_id: int):
        return client.get_channel(channel_id) or await client.fetch_channel(channel_id)

    @client.event
    async def on_ready():
        if "responder" in state:
            return  # reconnects fire on_ready again
        responder = Responder(config, baryon, client.user.id, DiscordUI(client))
        state["responder"] = responder
        if proactive.enabled:
            desk = GapDesk(proactive, responder, ProactiveState(proactive.state_path), channels)
            state["desk"] = desk
            client.loop.create_task(_schedule(desk))
            logger.info("proactive mode on: asking about knowledge gaps in %s every %.0f min",
                        proactive.channel, proactive.interval_minutes)
        logger.info("baryon-ax Discord bot ready as %s → %s", client.user, config.api_url)

    @client.event
    async def on_message(message):
        responder: Responder | None = state.get("responder")
        if not responder or message.author.id == client.user.id:
            return
        desk: GapDesk | None = state.get("desk")
        gap_id = desk.answer_gap_id(message) if desk else None
        if gap_id:
            await desk.on_answer(message, gap_id)
        elif responder.should_answer(message):
            await responder.answer(message)

    @client.event
    async def on_interaction(interaction):
        desk: GapDesk | None = state.get("desk")
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not desk or interaction.type != discord.InteractionType.component or not custom_id.startswith(CUSTOM_ID_PREFIX):
            return
        await interaction.response.defer()

        async def respond(text: str) -> None:
            await interaction.followup.send(text, ephemeral=True)

        await desk.on_button(custom_id, interaction.user.id, interaction.message, respond)

    return client


async def _schedule(desk: GapDesk) -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await desk.run_once()
        except Exception:
            logger.exception("proactive run failed")
        await asyncio.sleep(max(60.0, desk.config.interval_minutes * 60))


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    create_client().run(os.environ["DISCORD_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
