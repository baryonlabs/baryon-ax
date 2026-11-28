"""Slack conversations become knowledge: one document per channel per day, searchable and read into the ontology.

What people discuss in Slack ("견적 유효기간은 30일로 하죠", the answer someone gave in a thread) is company knowledge
that never reaches a document. With SLACK_ARCHIVE_CHANNELS set, the bot keeps each archived channel's day as one
Markdown document in baryon-ax:

* every human message of the day in that channel, oldest first, bot posts left out (they come from documents already);
* one ``## 스레드`` section per thread (its replies under its first message, filed under the day it started) and one
  ``## 채널 대화`` section for each run of messages without replies, so a chunk never holds the end of one thread and
  the start of another (the indexer starts a new chunk at every heading and repeats the heading in each chunk);
* threads read in full (every page), edited messages in their edited form marked "(수정됨)", reactions as
  "(반응: :+1: 3)", deleted messages left out (thread_snapshot.py);
* written when the channel has been quiet for SLACK_ARCHIVE_IDLE_MINUTES (default 30) after new messages, edits,
  deletions or reactions, replacing that day's earlier version, so a day is one document however often people talk
  and a deleted message leaves the document at the next rewrite. An edit or a deletion counts for the day the message
  was posted, not the day it was changed;
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
from thread_snapshot import ThreadSnapshot, event_target, fetch_replies

logger = logging.getLogger("baryon.slack.archive")

SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "bot_message",
                 "pinned_item", "unpinned_item", "group_join", "group_leave", "tombstone"}
CHANGE_SUBTYPES = {"message_changed", "message_deleted"}


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
        """Remember that a channel's day changed. Cheap: called for every message event.
        An edit or deletion marks the day its message was posted (the thread's first message), not today."""
        channel = event.get("channel", "")
        if not self.config.enabled or event.get("channel_type") not in ("channel", "group") or not self.config.archives(channel):
            return False
        subtype = event.get("subtype") or ""
        # the message itself: for an edit or a deletion, the message as it was posted
        message = (event.get("message") or event.get("previous_message") or {}) if subtype in CHANGE_SUBTYPES else event
        if (message.get("bot_id") or message.get("user") == self.bot_user_id or message.get("subtype") == "bot_message"
                or (subtype in SKIP_SUBTYPES)):
            return False
        _, thread_ts, ts = event_target(event)
        day = self.day_of(thread_ts or ts or time.time())
        with self.lock:
            self.pending[(channel, day)] = Pending(channel, day, private=event.get("channel_type") == "group")
        return True

    def observe_reaction(self, event: dict, thread_ts: str = "") -> bool:
        """A reaction was added or removed: rewrite the day it belongs to, if that day was archived before.
        ``thread_ts`` (from the snapshot store) files a reply's reaction under its thread's day."""
        item = event.get("item") or {}
        channel, ts = item.get("channel", ""), item.get("ts", "")
        if not self.config.enabled or not ts or not self.config.archives(channel):
            return False
        day = self.day_of(thread_ts or ts)
        known = self.state.get(f"{channel}:{day}")
        if not known:
            return False  # reaction events do not say whether the channel is private; only rewrite known days
        with self.lock:
            self.pending[(channel, day)] = Pending(channel, day, private=bool(known.get("private")))
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

    def _human(self, message: Message) -> bool:
        return not (message.author.is_bot or message.deleted or message.subtype in SKIP_SUBTYPES)

    def _when(self, ts: str) -> str:
        return datetime.fromtimestamp(float(ts or 0), self.zone).strftime("%H:%M")

    def _line(self, said: Message, indent: str = "") -> str:
        """"- 10:02 이름: 내용": mentions as "@이름", links as "label (url)" (slack_text), then edits, files, reactions."""
        text = said.text_plain.replace("\n", "\n" + indent + "  ")
        notes = []
        if said.edited:
            notes.append("수정됨")
        files = ", ".join(f.name for f in said.files if f.name)
        if files:
            notes.append(f"첨부: {files}")
        reactions = ", ".join(f":{r.name}: {r.count}" for r in said.reactions if r.count > 0)
        if reactions:
            notes.append(f"반응: {reactions}")
        return f"{indent}- {self._when(said.ts)} {said.author.name or '?'}: {text}" + "".join(f" ({note})" for note in notes)

    def thread(self, web, channel: str, parent: dict) -> ThreadSnapshot:
        """The whole thread a channel message starts (every page), or just that message when it has no replies."""
        snapshot = ThreadSnapshot(channel, parent["ts"], self.directory, self.bot_user_id)
        raw = [parent]
        if int(parent.get("reply_count") or 0):
            try:
                raw = fetch_replies(web, channel, parent["ts"]) or raw
            except Exception:  # noqa: BLE001 - the thread is unreadable: keep its first message
                logger.info("could not read the thread %s/%s", channel, parent["ts"], exc_info=True)
        snapshot.load(raw)
        return snapshot

    def _title(self, first: Message | None, ts: str) -> str:
        if first is None or first.deleted or not self._human(first):
            who = "삭제된 글" if first is None or first.deleted else f"{first.author.name or '봇'} (봇)"
            return f"## 스레드 {self._when(ts)} · {who}로 시작"
        words = " ".join(first.text_plain.split())
        words = words if len(words) <= 40 else words[:39].rstrip() + "…"
        return f"## 스레드 {self._when(ts)} · {first.author.name or '?'}: {words}"

    def sections(self, web, channel: str, day: str) -> list[str]:
        """The day as Markdown sections: one per thread, one per run of messages without replies."""
        sections: list[str] = []
        loose: list[Message] = []  # consecutive channel messages that started no thread

        def close_loose() -> None:
            if loose:
                span = self._when(loose[0].ts) + ("" if len(loose) == 1 else "–" + self._when(loose[-1].ts))
                sections.append("\n\n".join([f"## 채널 대화 {span}"] + [self._line(m) for m in loose]))
                loose.clear()

        for parent in self._messages(web, channel, day):
            if parent.get("thread_ts") and parent.get("thread_ts") != parent.get("ts"):
                continue  # a reply shown in the channel: it is listed under its thread
            snapshot = self.thread(web, channel, parent)
            first = snapshot.get(parent["ts"])
            replies = [m for m in snapshot.human() if m.ts != parent["ts"]]
            if not replies:
                if first is not None and self._human(first):
                    loose.append(first)
                continue
            close_loose()
            lines = [self._title(first, parent["ts"])]
            if first is not None and self._human(first):
                lines.append(self._line(first))
            lines += [self._line(reply, "  " if first is not None and self._human(first) else "") for reply in replies]
            sections.append("\n\n".join(lines))  # one block per message: a long thread splits into chunks, all under its heading
        close_loose()
        return sections

    def render(self, web, channel: str, day: str) -> tuple[str, str]:
        """(channel name, Markdown) of one channel's day; Markdown is empty when nobody said anything."""
        if self.directory is None:
            self.directory = UserDirectory.from_web(web)
        try:
            name = (web.conversations_info(channel=channel).get("channel") or {}).get("name") or channel
        except Exception:  # noqa: BLE001 - channels:read is optional
            name = channel
        sections = self.sections(web, channel, day)
        if not sections:
            return name, ""
        body = (f"# 슬랙 대화: #{name} · {day}\n\n> 슬랙 채널 #{name}에서 {day}에 오간 대화입니다(봇 글 제외, 스레드는 시작한 날에 묶음). "
                "대화는 결정의 근거가 아니라 참고 자료입니다.\n\n" + "\n\n".join(sections) + "\n")
        return name, body

    def write_day(self, web, channel: str, day: str, private: bool) -> bool:
        name, markdown = self.render(web, channel, day)
        key = f"{channel}:{day}"
        previous = self.state.get(key, {}).get("document_id")
        if not markdown:
            if previous:  # everything people said that day was deleted: so is the document
                self.baryon.as_bot("DELETE", f"/v1/documents/{previous}").raise_for_status()
                self.state.pop(key, None)
                self._save_state()
                logger.info("removed the archive of %s %s: nothing left", channel, day)
            return False
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
        self.state[key] = {"document_id": document_id, "written_at": time.time(), "chars": len(markdown), "private": private}
        self._save_state()
        logger.info("archived #%s %s (%d chars)", name, day, len(markdown))
        return True

    def _save_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.config.state_path) or ".", exist_ok=True)
            with open(self.config.state_path, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, indent=1)
        except OSError:
            logger.warning("could not save %s", self.config.state_path)


def start_archive_scheduler(archive: ConversationArchive, web, stop: threading.Event | None = None) -> threading.Thread:
    stop = stop or threading.Event()

    def run() -> None:
        while not stop.wait(60):
            archive.flush(web)

    thread = threading.Thread(target=run, name="baryon-archive", daemon=True)
    thread.start()
    return thread
