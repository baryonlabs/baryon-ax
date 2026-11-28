"""Slack conversations become knowledge: one document per channel per day, searchable and read into the ontology.

What people discuss in Slack ("견적 유효기간은 30일로 하죠", the answer someone gave in a thread) is company knowledge
that never reaches a document. With SLACK_ARCHIVE_CHANNELS set, the bot keeps each archived channel's day as one
Markdown document in baryon-ax:

* every human message of the day in that channel, oldest first, each thread's replies under its first message (the
  thread is filed under the day it started); bot posts are left out (they come from documents already);
* written when the channel has been quiet for SLACK_ARCHIVE_IDLE_MINUTES (default 30) after new messages, replacing
  that day's earlier version, so a day is one document however often people talk;
* indexed like any upload (chunks, vectors) and read by the question–certification loop's rounds into the ontology;
* public channels are readable by members; private channels only by SLACK_ARCHIVE_PRIVATE_ROLES (default admin), and
  the loop builds its shared ontology from member-readable documents only. DMs are never archived.

SLACK_ARCHIVE_CHANNELS: comma-separated channel ids, or ``*`` for every channel the bot is in. Archiving a channel
should be announced to its members. Empty (the default) archives nothing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from slack_text import Message, UserDirectory

logger = logging.getLogger("baryon.slack.archive")

SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "bot_message",
                 "message_deleted", "pinned_item", "group_join", "group_leave"}


@dataclass
class ArchiveConfig:
    channels: frozenset[str] = frozenset()
    every_channel: bool = False
    idle_minutes: float = 30.0
    private_roles: str = "admin"
    utc_offset_hours: float = 9.0  # the day boundary (KST by default)
    state_path: str = ".data/archive_state.json"

    @property
    def enabled(self) -> bool:
        return self.every_channel or bool(self.channels)

    def archives(self, channel: str) -> bool:
        return self.every_channel or channel in self.channels

    @classmethod
    def from_env(cls) -> "ArchiveConfig":
        raw = os.getenv("SLACK_ARCHIVE_CHANNELS", "").strip()
        return cls(channels=frozenset(c.strip() for c in raw.split(",") if c.strip() and c.strip() != "*"), every_channel=raw == "*",
                   idle_minutes=float(os.getenv("SLACK_ARCHIVE_IDLE_MINUTES", "30")),
                   private_roles=os.getenv("SLACK_ARCHIVE_PRIVATE_ROLES", "admin"),
                   utc_offset_hours=float(os.getenv("SLACK_ARCHIVE_UTC_OFFSET", "9")),
                   state_path=os.getenv("SLACK_ARCHIVE_STATE_PATH", ".data/archive_state.json"))


@dataclass
class Pending:
    channel: str
    day: str
    private: bool
    last_message: float = field(default_factory=time.time)


class ConversationArchive:
    def __init__(self, config: ArchiveConfig, baryon, bot_user_id: str) -> None:
        self.config = config
        self.baryon = baryon
        self.bot_user_id = bot_user_id
        self.directory: UserDirectory | None = None  # user names; create_app shares the bot's, else made from web
        self.zone = timezone(timedelta(hours=config.utc_offset_hours))
        self.pending: dict[tuple[str, str], Pending] = {}
        self.lock = threading.Lock()
        self.state: dict[str, dict] = {}
        try:
            with open(config.state_path, encoding="utf-8") as handle:
                self.state = json.load(handle)
        except (OSError, ValueError):
            pass

    def day_of(self, ts: str) -> str:
        return datetime.fromtimestamp(float(ts), self.zone).strftime("%Y-%m-%d")

    # ---------------------------------------------------------------- noticing

    def observe(self, event: dict) -> bool:
        """Remember that a channel's day changed. Cheap: called for every message event."""
        channel = event.get("channel", "")
        if (not self.config.enabled or event.get("channel_type") not in ("channel", "group") or not self.config.archives(channel)
                or event.get("bot_id") or event.get("user") == self.bot_user_id or event.get("subtype") in SKIP_SUBTYPES):
            return False
        day = self.day_of(event.get("thread_ts") or event.get("ts") or time.time())
        with self.lock:
            self.pending[(channel, day)] = Pending(channel, day, private=event.get("channel_type") == "group")
        return True

    # ---------------------------------------------------------------- writing

    def flush(self, web, force: bool = False) -> int:
        """Write the days that have been quiet long enough. Returns how many documents were written."""
        now = time.time()
        with self.lock:
            ready = [p for p in self.pending.values() if force or now - p.last_message >= self.config.idle_minutes * 60]
            for item in ready:
                self.pending.pop((item.channel, item.day), None)
        written = 0
        for item in ready:
            try:
                written += 1 if self.write_day(web, item.channel, item.day, item.private) else 0
            except Exception:  # noqa: BLE001 - try again on the next message
                logger.exception("could not archive %s %s", item.channel, item.day)
        return written

    def _messages(self, web, channel: str, day: str) -> list[dict]:
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=self.zone).timestamp()
        messages: list[dict] = []
        cursor = None
        while True:
            page = web.conversations_history(channel=channel, oldest=str(start), latest=str(start + 86400), inclusive=True,
                                             limit=200, **({"cursor": cursor} if cursor else {}))
            messages += page.get("messages", [])
            cursor = (page.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return sorted(messages, key=lambda m: float(m.get("ts", 0)))

    def _human(self, message: dict) -> bool:
        return not (message.get("bot_id") or message.get("user") == self.bot_user_id or message.get("subtype") in SKIP_SUBTYPES)

    def _line(self, message: dict, indent: str = "") -> str:
        """"- 10:02 이름: 내용": mentions as "@이름", links as "label (url)" (slack_text)."""
        when = datetime.fromtimestamp(float(message.get("ts", 0)), self.zone).strftime("%H:%M")
        said = Message.from_event(message, self.directory)
        text = said.text_plain.replace("\n", "\n" + indent + "  ")
        files = ", ".join(f.name for f in said.files if f.name)
        return f"{indent}- {when} {said.author.name or "?"}: {text}" + (f" (첨부: {files})" if files else "")

    def render(self, web, channel: str, day: str) -> tuple[str, str]:
        """(channel name, Markdown) of one channel's day; Markdown is empty when nobody said anything."""
        if self.directory is None:
            self.directory = UserDirectory.from_web(web)
        try:
            name = (web.conversations_info(channel=channel).get("channel") or {}).get("name") or channel
        except Exception:  # noqa: BLE001 - channels:read is optional
            name = channel
        lines: list[str] = []
        for message in self._messages(web, channel, day):
            if message.get("thread_ts") and message.get("thread_ts") != message.get("ts"):
                continue  # a reply shown in the channel: it is listed under its thread
            replies = []
            if int(message.get("reply_count") or 0):
                try:
                    found = web.conversations_replies(channel=channel, ts=message["ts"], limit=200).get("messages", [])
                    replies = [r for r in found[1:] if self._human(r)]
                except Exception:  # noqa: BLE001
                    replies = []
            if self._human(message):
                lines.append(self._line(message))
            elif replies:
                lines.append("- (봇이 시작한 스레드)")
            lines += [self._line(reply, "  ") for reply in replies]
        if not lines:
            return name, ""
        body = (f"# 슬랙 대화: #{name} · {day}\n\n> 슬랙 채널 #{name}에서 {day}에 오간 대화입니다(봇 글 제외, 스레드는 시작한 날에 묶음). "
                "대화는 결정의 근거가 아니라 참고 자료입니다.\n\n" + "\n".join(lines) + "\n")
        return name, body

    def write_day(self, web, channel: str, day: str, private: bool) -> bool:
        name, markdown = self.render(web, channel, day)
        if not markdown:
            return False
        key = f"{channel}:{day}"
        previous = self.state.get(key, {}).get("document_id")
        roles = self.config.private_roles if private else "member,admin"
        safe = re.sub(r"[^0-9A-Za-z가-힣_-]", "-", name)[:40]
        response = self.baryon.as_bot("POST", "/v1/documents", files={"file": (f"slack-{safe}-{day}.md", markdown.encode("utf-8"))},
                                      data={"allowed_roles": roles, "category": "slack-conversation",
                                            "tags": f"slack,slack-conversation,channel:{channel},day:{day}"})
        response.raise_for_status()
        document_id = response.json().get("id", "")
        if previous and previous != document_id:
            try:
                self.baryon.as_bot("DELETE", f"/v1/documents/{previous}")  # the day's earlier version
            except httpx.HTTPError:
                logger.warning("could not delete the earlier version %s", previous)
        self.state[key] = {"document_id": document_id, "written_at": time.time(), "chars": len(markdown)}
        try:
            os.makedirs(os.path.dirname(self.config.state_path) or ".", exist_ok=True)
            with open(self.config.state_path, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, indent=1)
        except OSError:
            logger.warning("could not save %s", self.config.state_path)
        logger.info("archived #%s %s (%d chars)", name, day, len(markdown))
        return True


def start_archive_scheduler(archive: ConversationArchive, web, stop: threading.Event | None = None) -> threading.Thread:
    stop = stop or threading.Event()

    def run() -> None:
        while not stop.wait(60):
            archive.flush(web)

    thread = threading.Thread(target=run, name="baryon-archive", daemon=True)
    thread.start()
    return thread
