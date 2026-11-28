"""One Slack thread as it is now: every page of replies, edits in place, deletions as tombstones, reactions.

The bot used to read a thread with one ``conversations.replies`` call (``limit=50``): a long thread lost its newest
messages, an edit showed up only after the next fetch, a deleted message stayed in the archive until the day was
rewritten, and reactions were ignored. A :class:`ThreadSnapshot` keeps the thread as :class:`slack_text.Message`
objects and stays current from events:

* :func:`fetch_replies` pages through ``conversations.replies`` with the cursor until the thread is complete.
* ``message_changed`` replaces the message with the original ``ts`` and marks it ``edited``; the message keeps
  its original time (the day it belongs to is the day it was posted, not the day it was edited).
* ``message_deleted`` leaves a tombstone: same ``ts`` and author, no text, ``deleted=True``.
* ``reaction_added`` / ``reaction_removed`` update :attr:`Message.reactions` (emoji, who pressed it, count);
  :meth:`ThreadSnapshot.refresh_reactions` reads them again with ``reactions.get`` (``reactions:read``).
* Whatever changes, the author's kind (human, self_bot, other_bot, user_token_app) stays what it was when the
  message was first read: an edit event carries a thinner copy of the message and must not turn a bot into a person.

:class:`SnapshotStore` holds the snapshots the bot has read and routes events to them. :class:`TruncationHold`
waits a few seconds for an edit when a person's message stops mid-sentence ("… baryon 가") and then hands the
message on; what the bot does with it is decided elsewhere (S3).
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from slack_text import HUMAN, Message, Reaction, UserDirectory

logger = logging.getLogger("baryon.slack.snapshot")

PAGE_SIZE = 200      # conversations.replies allows up to 1000; Slack recommends at most 200
MAX_PAGES = 50       # 10 000 messages: a runaway cursor stops here

# Messages that are not part of the conversation (joins, topic changes …).
NOISE_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name",
                  "group_join", "group_leave", "pinned_item", "unpinned_item"}


def fetch_replies(web, channel: str, ts: str, page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES) -> list[dict]:
    """Every message of the thread ``ts`` (its first message included), oldest first, all pages.
    Errors (missing scope, not_in_channel) propagate, as a single ``conversations_replies`` call would."""
    messages: list[dict] = []
    seen: set[str] = set()
    cursor = ""
    for _ in range(max_pages):
        page = web.conversations_replies(channel=channel, ts=ts, limit=page_size, **({"cursor": cursor} if cursor else {}))
        for message in page.get("messages") or []:
            key = message.get("ts", "")
            if key and key in seen:  # every page repeats the thread's first message
                continue
            seen.add(key)
            messages.append(message)
        cursor = (page.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor or not page.get("has_more", True):
            break
    else:
        logger.warning("thread %s/%s has more than %d pages; read the first %d", channel, ts, max_pages, len(messages))
    return sorted(messages, key=lambda m: float(m.get("ts") or 0))


def _thread_of(message: dict) -> str:
    return message.get("thread_ts") or message.get("ts") or ""


def event_target(event: dict) -> tuple[str, str, str]:
    """(channel, thread_ts, ts) an event is about; thread_ts is "" when the event does not say (reactions)."""
    kind, subtype = event.get("type") or "message", event.get("subtype") or ""
    if kind in ("reaction_added", "reaction_removed"):
        item = event.get("item") or {}
        return item.get("channel", ""), "", item.get("ts", "")
    channel = event.get("channel", "")
    if subtype == "message_changed":
        inner = event.get("message") or {}
        previous = event.get("previous_message") or {}
        ts = inner.get("ts") or previous.get("ts") or ""
        return channel, inner.get("thread_ts") or previous.get("thread_ts") or ts, ts
    if subtype == "message_deleted":
        previous = event.get("previous_message") or {}
        ts = event.get("deleted_ts") or previous.get("ts") or ""
        return channel, previous.get("thread_ts") or event.get("thread_ts") or ts, ts
    ts = event.get("ts", "")
    return channel, event.get("thread_ts") or ts, ts


def reactions_of(items: Iterable[dict] | None) -> list[Reaction]:
    return [Reaction(r.get("name", ""), int(r.get("count") or len(r.get("users") or [])), tuple(r.get("users") or []))
            for r in items or [] if isinstance(r, dict) and r.get("name")]


def tombstone(message: Message) -> Message:
    """The message with its content removed: same ts, thread and author, deleted=True."""
    return dataclasses.replace(message, text_plain="", text_for_me="", mentions=[], links=[], files=[], reactions=[],
                               tokens=[], deleted=True)


class ThreadSnapshot:
    """The current state of one thread, as Messages keyed by ts."""

    def __init__(self, channel: str, thread_ts: str, directory: UserDirectory | None = None, me: str = "",
                 my_bot_id: str = "") -> None:
        self.channel = channel
        self.thread_ts = thread_ts
        self.directory = directory
        self.me = me
        self.my_bot_id = my_bot_id
        self._messages: dict[str, Message] = {}
        self.complete = False  # True once every page was read (fetch); events alone may have missed older messages
        self.lock = threading.RLock()

    # ---------------------------------------------------------------- reading

    @classmethod
    def fetch(cls, web, channel: str, thread_ts: str, directory: UserDirectory | None = None, me: str = "",
              my_bot_id: str = "", page_size: int = PAGE_SIZE) -> ThreadSnapshot:
        snapshot = cls(channel, thread_ts, directory, me, my_bot_id)
        snapshot.load(fetch_replies(web, channel, thread_ts, page_size=page_size))
        snapshot.complete = True
        return snapshot

    def load(self, raw: Iterable[dict]) -> None:
        """Replace the thread with these ``conversations.replies`` items (already edited; parents may be tombstones)."""
        messages = {}
        for item in raw:
            if item.get("ts"):
                messages[item["ts"]] = self._read(item)
        with self.lock:
            self._messages = messages

    def _read(self, item: dict) -> Message:
        return Message.from_event(item, self.directory, self.me, self.my_bot_id)

    @property
    def messages(self) -> list[Message]:
        """Every message, tombstones included, oldest first."""
        with self.lock:
            return sorted(self._messages.values(), key=lambda m: float(m.ts or 0))

    def visible(self) -> list[Message]:
        """Messages that still have content (no tombstones, no joins or topic changes), oldest first."""
        return [m for m in self.messages if not m.deleted and m.subtype not in NOISE_SUBTYPES]

    def human(self) -> list[Message]:
        """What people said: visible messages not written by a bot (apps writing as a person count as people)."""
        return [m for m in self.visible() if not m.author.is_bot]

    def get(self, ts: str) -> Message | None:
        with self.lock:
            return self._messages.get(ts)

    def __contains__(self, ts: str) -> bool:
        with self.lock:
            return ts in self._messages

    def __len__(self) -> int:
        with self.lock:
            return len(self._messages)

    # ---------------------------------------------------------------- events

    def apply(self, event: dict) -> Message | None:
        """Bring the snapshot up to date with one event. Returns the message it changed (or added), else None."""
        kind, subtype = event.get("type") or "message", event.get("subtype") or ""
        if kind == "reaction_added":
            return self._react(event, add=True)
        if kind == "reaction_removed":
            return self._react(event, add=False)
        if kind != "message":
            return None
        if subtype == "message_changed":
            return self._edit(event)
        if subtype == "message_deleted":
            return self._delete(event)
        if subtype in NOISE_SUBTYPES or not event.get("ts"):
            return None
        _, thread_ts, ts = event_target(event)
        if thread_ts != self.thread_ts:
            return None
        message = self._read(event)
        with self.lock:
            known = self._messages.get(ts)
            if known is not None and (known.deleted or known.edited):
                return None  # a late duplicate of a message already edited or deleted: keep the newer state
            self._messages[ts] = message
        return message

    def _edit(self, event: dict) -> Message | None:
        inner = event.get("message") or {}
        ts = inner.get("ts", "")
        if not ts:
            return None
        if inner.get("subtype") == "tombstone":  # a parent deleted while it had replies arrives as an edit
            return self._delete({"deleted_ts": ts, "previous_message": event.get("previous_message") or inner})
        edited = Message.from_event(event, self.directory, self.me, self.my_bot_id)  # reads event["message"]
        with self.lock:
            known = self._messages.get(ts)
            if known is None and _thread_of(inner) != self.thread_ts:
                return None
            if known is not None:
                if known.deleted:
                    return None  # an edit that arrives after the delete changes nothing
                # The event's copy may lack bot_id, files or reactions: keep what is known.
                edited = dataclasses.replace(
                    edited, author=known.author, from_me=known.from_me, thread_ts=edited.thread_ts or known.thread_ts,
                    reactions=edited.reactions if "reactions" in inner else known.reactions,
                    files=edited.files if "files" in inner else known.files)
            edited = dataclasses.replace(edited, ts=ts, edited=True, subtype=known.subtype if known else "")
            self._messages[ts] = edited
        return edited

    def _delete(self, event: dict) -> Message | None:
        previous = event.get("previous_message") or {}
        ts = event.get("deleted_ts") or previous.get("ts") or ""
        if not ts:
            return None
        with self.lock:
            known = self._messages.get(ts)
            if known is None:
                if _thread_of(previous or {"ts": ts}) != self.thread_ts:
                    return None
                known = Message.from_event({**previous, "ts": ts}, self.directory, self.me, self.my_bot_id)
            if known.deleted and not known.text_plain:
                return None
            gone = tombstone(known)
            self._messages[ts] = gone
        return gone

    def _react(self, event: dict, add: bool) -> Message | None:
        ts = (event.get("item") or {}).get("ts", "")
        name, user = event.get("reaction") or "", event.get("user") or ""
        with self.lock:
            known = self._messages.get(ts)
            if known is None or known.deleted or not name:
                return None
            reactions = list(known.reactions)
            index = next((i for i, r in enumerate(reactions) if r.name == name), None)
            if add:
                if index is None:
                    reactions.append(Reaction(name, 1, (user,) if user else ()))
                elif not user or user not in reactions[index].users:
                    old = reactions[index]
                    reactions[index] = Reaction(name, old.count + 1, old.users + ((user,) if user else ()))
                else:
                    return None  # the same person's reaction twice (a replayed event)
            else:
                if index is None:
                    return None
                old = reactions[index]
                if user and user not in old.users and old.users:
                    return None
                users = tuple(u for u in old.users if u != user)
                count = max(old.count - 1, 0)
                if count:
                    reactions[index] = Reaction(name, count, users)
                else:
                    reactions.pop(index)
            changed = dataclasses.replace(known, reactions=reactions)
            self._messages[ts] = changed
        return changed

    def refresh_reactions(self, web, ts: str) -> Message | None:
        """Read one message's reactions again with ``reactions.get`` (reactions:read). None when it fails."""
        try:
            response = web.reactions_get(channel=self.channel, timestamp=ts, full=True)
        except Exception as error:  # noqa: BLE001 - missing reactions:read, message gone
            logger.info("reactions.get %s failed (%s)", ts, error)
            return None
        item = response.get("message") or {}
        with self.lock:
            known = self._messages.get(ts)
            if known is None or known.deleted:
                return None
            changed = dataclasses.replace(known, reactions=reactions_of(item.get("reactions")))
            self._messages[ts] = changed
        return changed


class SnapshotStore:
    """The threads the bot has read, kept current from events; at most ``max_threads`` (least recently used out).

    ``get`` reads a thread once (every page) and returns the cached snapshot after that. ``on_event`` applies
    a message, edit, delete or reaction event to the snapshot it belongs to and returns that snapshot (None when
    the thread was never read: there is nothing to keep current, and the next ``get`` reads it as it is then).
    A thread's first message starts a snapshot by itself, since nothing came before it.
    """

    def __init__(self, directory: UserDirectory | None = None, me: str = "", my_bot_id: str = "", max_threads: int = 500,
                 hold: TruncationHold | None = None) -> None:
        self.directory = directory
        self.me = me
        self.my_bot_id = my_bot_id
        self.max_threads = max_threads
        self.hold = hold
        self._threads: OrderedDict[tuple[str, str], ThreadSnapshot] = OrderedDict()
        self._where: dict[tuple[str, str], str] = {}  # (channel, ts) → thread_ts, for reactions
        self.lock = threading.Lock()

    def _keep(self, snapshot: ThreadSnapshot) -> None:
        key = (snapshot.channel, snapshot.thread_ts)
        with self.lock:
            self._threads[key] = snapshot
            self._threads.move_to_end(key)
            for message in snapshot.messages:
                self._where[(snapshot.channel, message.ts)] = snapshot.thread_ts
            while len(self._threads) > self.max_threads:
                (channel, thread_ts), old = self._threads.popitem(last=False)
                for message in old.messages:
                    self._where.pop((channel, message.ts), None)

    def cached(self, channel: str, thread_ts: str) -> ThreadSnapshot | None:
        with self.lock:
            return self._threads.get((channel, thread_ts))

    def get(self, web, channel: str, thread_ts: str, refresh: bool = False) -> ThreadSnapshot:
        snapshot = None if refresh else self.cached(channel, thread_ts)
        if snapshot is None or not snapshot.complete:
            snapshot = ThreadSnapshot.fetch(web, channel, thread_ts, self.directory, self.me, self.my_bot_id)
        self._keep(snapshot)
        return snapshot

    def thread_of(self, channel: str, ts: str) -> str:
        with self.lock:
            return self._where.get((channel, ts), "")

    def on_event(self, event: dict) -> ThreadSnapshot | None:
        channel, thread_ts, ts = event_target(event)
        if not channel or not ts:
            return None
        thread_ts = thread_ts or self.thread_of(channel, ts)
        snapshot = self.cached(channel, thread_ts) if thread_ts else None
        kind, subtype = event.get("type") or "message", event.get("subtype") or ""
        is_new = kind == "message" and subtype not in ("message_changed", "message_deleted")
        if snapshot is None and is_new and thread_ts == ts and subtype not in NOISE_SUBTYPES:
            snapshot = ThreadSnapshot(channel, ts, self.directory, self.me, self.my_bot_id)
            snapshot.complete = True  # a new thread: this message is all there is
        changed = snapshot.apply(event) if snapshot is not None else None
        if snapshot is not None:
            self._keep(snapshot)
        if self.hold:
            self._feed_hold(channel, event, changed, is_new)
        return snapshot if changed is not None else None

    def _feed_hold(self, channel: str, event: dict, changed: Message | None, is_new: bool) -> None:
        subtype = event.get("subtype") or ""
        if subtype == "message_deleted":
            self.hold.cancel(channel, event_target(event)[2])
            return
        message = changed if changed is not None else None
        if message is None and (event.get("type") or "message") == "message" and subtype != "message_deleted":
            if subtype in NOISE_SUBTYPES:
                return
            message = Message.from_event(event, self.directory, self.me, self.my_bot_id)  # a thread never read
            if subtype == "message_changed":
                message = dataclasses.replace(message, edited=True)
        if message is None:
            return
        if subtype == "message_changed":
            self.hold.on_edit(channel, message)
        elif is_new:
            self.hold.submit(channel, message)


# --------------------------------------------------------------------------- unfinished messages


@dataclass
class HoldConfig:
    wait_seconds: float = 20.0  # 0 turns the hold off: every message is handed on at once

    @classmethod
    def from_env(cls) -> HoldConfig:
        try:
            return cls(wait_seconds=max(0.0, float(os.getenv("SLACK_TRUNCATION_WAIT_SECONDS", "20"))))
        except ValueError:
            return cls()


READY, EDITED, TIMEOUT = "ready", "edited", "timeout"


class TruncationHold:
    """A person's message that stops mid-sentence is held for ``wait_seconds``, waiting for its author to edit it.

    ``handler(channel, message, reason)`` gets every message passed to :meth:`submit`, once:

    * ``ready``: the message looked finished (or came from a bot, or the hold is off) and was handed on at once;
    * ``edited``: it looked unfinished and its author edited it within the wait (the edited text is passed);
    * ``timeout``: it looked unfinished and nobody edited it; it is handed on as it is.

    A deleted message is dropped without calling the handler. ``timer(seconds, callback)`` makes a started,
    cancellable timer (``threading.Timer`` by default; tests pass a fake clock).
    """

    def __init__(self, handler: Callable[[str, Message, str], None], config: HoldConfig | None = None,
                 timer: Callable[[float, Callable[[], None]], object] | None = None) -> None:
        self.handler = handler
        self.config = config or HoldConfig()
        self.timer = timer or _thread_timer
        self._pending: dict[tuple[str, str], tuple[Message, object]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def should_hold(message: Message) -> bool:
        return message.author.kind == HUMAN and not message.deleted and message.looks_truncated

    def held(self, channel: str, ts: str) -> bool:
        with self.lock:
            return (channel, ts) in self._pending

    def pending(self) -> list[tuple[str, str]]:
        """(channel, ts) of the messages still waiting for an edit."""
        with self.lock:
            return list(self._pending)

    def submit(self, channel: str, message: Message) -> bool:
        """Hand the message on now, or hold it when it looks unfinished. True when it is held."""
        key = (channel, message.ts)
        if self.config.wait_seconds <= 0 or not self.should_hold(message):
            self._call(channel, message, READY)
            return False
        with self.lock:
            if key in self._pending:
                return True
            timer = self.timer(self.config.wait_seconds, lambda: self._expire(key))
            self._pending[key] = (message, timer)
        logger.info("holding %s/%s for %.0fs: it looks unfinished", channel, message.ts, self.config.wait_seconds)
        return True

    def on_edit(self, channel: str, message: Message) -> bool:
        """An edit of a held message releases it with the edited text. True when a held message was released."""
        with self.lock:
            entry = self._pending.pop((channel, message.ts), None)
        if entry is None:
            return False
        _cancel(entry[1])
        self._call(channel, message, EDITED)
        return True

    def cancel(self, channel: str, ts: str) -> bool:
        """The message was deleted: forget it."""
        with self.lock:
            entry = self._pending.pop((channel, ts), None)
        if entry is None:
            return False
        _cancel(entry[1])
        return True

    def _expire(self, key: tuple[str, str]) -> None:
        with self.lock:
            entry = self._pending.pop(key, None)
        if entry is not None:
            self._call(key[0], entry[0], TIMEOUT)

    def _call(self, channel: str, message: Message, reason: str) -> None:
        try:
            self.handler(channel, message, reason)
        except Exception:  # noqa: BLE001 - one handler failure must not stop the event loop
            logger.exception("truncation hold handler failed for %s/%s", channel, message.ts)


def _thread_timer(seconds: float, callback: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


def _cancel(timer: object) -> None:
    cancel = getattr(timer, "cancel", None)
    if callable(cancel):
        cancel()
