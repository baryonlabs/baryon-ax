"""Slack mrkdwn → tokens → readable text, shared by every part of the bot.

Slack sends mentions, links and channels as ``<…>`` markup. Deleting that markup loses who a message is
about ("<@U1> 는 <@U2> 이 …" read as "는 이 …"), so this module keeps it as data instead:

* :func:`tokenize` splits mrkdwn into tokens (user, channel, usergroup, special, link, emoji, code, block,
  quote, text).
* :class:`UserDirectory` turns user ids into display names and tells bots from people (``users.info``, cached).
* :func:`readable` renders tokens as plain text with mentions as ``@이름``. The bot's own mention is dropped
  when it only addresses the bot, and kept when it is part of the sentence ("@baryon 는 …").
* :class:`Message` is one Slack message normalized: author, readable text, mentions, links, files, reactions,
  edit and delete state, and whether it is addressed to the bot.
* :func:`looks_truncated` hints that a message stopped mid-sentence ("… baryon 가").
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from collections.abc import Callable, Iterable

logger = logging.getLogger("baryon.slack.text")

# --------------------------------------------------------------------------- tokens

_TOKEN_RE = re.compile(
    r"(?P<block>```[\s\S]*?```)"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<angle><[^<>\n]+>)"
    r"|(?P<quote>^(?:&gt;|>)[ \t]?)"
    r"|(?P<emoji>(?<![0-9A-Za-z:]):[a-z0-9_+'-]+(?:::skin-tone-\d)?:(?![0-9A-Za-z]))",
    re.MULTILINE,
)
_USER_RE = re.compile(r"@([UWB][A-Z0-9_]+)(?:\|([^>]*))?$")
_CHANNEL_RE = re.compile(r"#([CGD][A-Z0-9]+)(?:\|([^>]*))?$")
_SUBTEAM_RE = re.compile(r"!subteam\^([A-Z0-9]+)(?:\|([^>]*))?$")
_SPECIAL_RE = re.compile(r"!(here|channel|everyone)(?:\|[^>]*)?$")
_DATE_RE = re.compile(r"!date\^[^|]*\|(.*)$")

KINDS = ("text", "user", "channel", "usergroup", "special", "link", "emoji", "code", "block", "quote")


@dataclass(frozen=True)
class Token:
    kind: str           # one of KINDS
    raw: str            # the markup as Slack sent it
    id: str = ""        # user / channel / usergroup id, or here|channel|everyone
    label: str = ""     # the text after "|" (a user name, channel name, link text)
    url: str = ""       # links only

    @property
    def text(self) -> str:
        """Plain text of a text, code or block token (HTML entities decoded)."""
        return unescape(self.raw)


def _angle(raw: str) -> Token:
    inner = raw[1:-1]
    for kind, pattern in (("user", _USER_RE), ("channel", _CHANNEL_RE), ("usergroup", _SUBTEAM_RE)):
        match = pattern.match(inner)
        if match:
            return Token(kind, raw, id=match.group(1), label=unescape(match.group(2) or "").lstrip("@#"))
    match = _SPECIAL_RE.match(inner)
    if match:
        return Token("special", raw, id=match.group(1))
    match = _DATE_RE.match(inner)
    if match:
        return Token("text", unescape(match.group(1)))
    if inner.startswith("!"):  # an unknown command: keep its fallback text or nothing
        return Token("text", unescape(inner.split("|", 1)[1]) if "|" in inner else "")
    url, _, label = inner.partition("|")
    return Token("link", raw, label=unescape(label), url=unescape(url))


def tokenize(text: str | None) -> list[Token]:
    """Split Slack mrkdwn into tokens. Plain text between markup becomes ``text`` tokens."""
    text = text or ""
    tokens: list[Token] = []
    position = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() > position:
            tokens.append(Token("text", text[position:match.start()]))
        kind = match.lastgroup or "text"
        raw = match.group(0)
        tokens.append(_angle(raw) if kind == "angle" else Token(kind, raw))
        position = match.end()
    if position < len(text):
        tokens.append(Token("text", text[position:]))
    return tokens


# --------------------------------------------------------------------------- names


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    is_bot: bool = False


def _person(user_id: str, data: dict | None) -> Person | None:
    if not data:
        return None
    profile = data.get("profile") or {}
    name = (profile.get("display_name") or profile.get("real_name") or data.get("real_name") or data.get("name") or "").strip()
    if not name:
        return None
    return Person(user_id, name, bool(data.get("is_bot") or data.get("is_app_user")))


class UserDirectory:
    """User id → display name and bot flag, from ``users.info`` (needs ``users:read``), cached for ``ttl`` seconds.

    ``lookup`` takes a user id and returns the Slack user object (or raises). Without one, or when the lookup
    fails, only names learned from markup (``<@U1|kim>``) and :meth:`remember` are known; unknown ids stay ids.
    """

    def __init__(self, lookup: Callable[[str], dict | None] | None = None, ttl: float = 3600.0,
                 failure_ttl: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.lookup = lookup
        self.ttl = ttl
        self.failure_ttl = failure_ttl
        self.clock = clock
        self._cache: dict[str, tuple[float, Person | None]] = {}  # users.info results (None: lookup failed)
        self._learned: dict[str, Person] = {}  # names seen in markup or bot profiles, used when a lookup has none
        self._lock = threading.Lock()

    @classmethod
    def from_web(cls, web, **kwargs) -> UserDirectory:
        """A directory backed by a Slack WebClient's ``users.info``."""
        return cls(lambda user_id: (web.users_info(user=user_id) or {}).get("user"), **kwargs)

    def remember(self, user_id: str, name: str, is_bot: bool = False) -> None:
        """Record a name seen elsewhere (a mention label, a bot profile). A users.info result still wins."""
        if user_id and name:
            with self._lock:
                self._learned[user_id] = Person(user_id, name, is_bot)

    def get(self, user_id: str) -> Person | None:
        if not user_id:
            return None
        now = self.clock()
        with self._lock:
            cached = self._cache.get(user_id)
        if not (cached and cached[0] > now):
            person, expires = None, now + self.failure_ttl
            if self.lookup:
                try:
                    person = _person(user_id, self.lookup(user_id))
                    if person:
                        expires = now + self.ttl
                except Exception as error:  # noqa: BLE001 - missing users:read, user_not_found, network: keep the id
                    logger.info("users.info %s failed (%s)", user_id, error)
            cached = (expires, person)
            with self._lock:
                self._cache[user_id] = cached
        with self._lock:
            return cached[1] or self._learned.get(user_id)

    def name(self, user_id: str, fallback: str = "") -> str:
        person = self.get(user_id)
        return person.name if person else (fallback or user_id)

    def is_bot(self, user_id: str) -> bool:
        person = self.get(user_id)
        return bool(person and person.is_bot)


# --------------------------------------------------------------------------- rendering

# Korean particles that make a mention part of the sentence ("<@U1> 는 …", "<@U1> 이랑 …").
PARTICLES = ("께서", "에게", "한테", "에서", "으로", "이랑", "하고", "님", "은", "는", "이", "가", "을", "를", "의",
             "에", "와", "과", "도", "만", "로", "랑", "께")
_PARTICLE_AFTER_RE = re.compile(r"^[ \t]*(?:" + "|".join(PARTICLES) + r")(?=$|[\s.,!?~:;)\]])")


def _user_name(token: Token, directory: UserDirectory | None) -> str:
    if directory:
        if token.label:
            directory.remember(token.id, token.label)
        return directory.name(token.id, token.label)
    return token.label or token.id


def _link_text(token: Token) -> str:
    url, label = token.url, token.label
    if url.startswith("mailto:"):
        return label or url[len("mailto:"):]
    if not label or label in (url, re.sub(r"^https?://", "", url)):
        return url
    return f"{label} ({url})"


def render(tokens: Iterable[Token], directory: UserDirectory | None = None, me: str = "") -> str:
    """Tokens → readable text. Mentions become ``@이름``; the mention of ``me`` is dropped when it only
    addresses the bot and kept when a particle follows it (then it is the subject or object of the sentence)."""
    tokens = list(tokens)
    parts: list[str] = []
    skip_space = False
    for index, token in enumerate(tokens):
        kind = token.kind
        if kind == "user":
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if me and token.id == me and not (following and following.kind == "text" and _PARTICLE_AFTER_RE.match(following.text)):
                skip_space = True
                continue
            parts.append("@" + _user_name(token, directory))
        elif kind == "channel":
            parts.append("#" + (token.label or token.id))
        elif kind == "usergroup":
            parts.append("@" + (token.label or token.id))
        elif kind == "special":
            parts.append("@" + token.id)
        elif kind == "link":
            parts.append(_link_text(token))
        elif kind == "quote":
            parts.append("> ")
        elif kind in ("emoji",):
            parts.append(token.raw)
        else:  # text, code, block
            text = token.text
            if skip_space:
                text = text.lstrip(" \t")
            parts.append(text)
        skip_space = False
    text = "".join(parts)
    text = re.sub(r"[ \t]{2,}", " ", text) if "```" not in text else text
    return "\n".join(line.rstrip() for line in text.strip().split("\n"))


def readable(text: str | None, me: str = "", directory: UserDirectory | None = None) -> str:
    """Slack mrkdwn → readable text (see :func:`render`)."""
    return render(tokenize(text), directory, me)


# --------------------------------------------------------------------------- truncation hint

_CLOSED_END_RE = re.compile(r"[.?!…~)\]」』\"'”’]$|:[a-z0-9_+'-]+:$|[요다까죠줘해네니나지자라어야군걸게래요임음함됨듯봐]$")
_OPEN_END_RE = re.compile(r"[,:;/\-→+&(]$")
CONJUNCTIONS = {"그리고", "그래서", "근데", "그런데", "그러면", "하지만", "그러나", "및", "또", "또는", "혹은",
                "and", "or", "but", "with", "to", "the", "a", "of", "for", "is"}
# A word ending in these is almost never a finished Korean sentence ("…를", "…에게", "하는").
_HANGUL_OPEN_SUFFIXES = ("께서", "에게", "한테", "에서", "으로", "를", "을", "는", "은")
# These also end ordinary nouns or question endings in Hangul ("회의", "사과", "인가"), so they count only after
# Latin letters or digits ("baryon가", "API는").
_LATIN_OPEN_SUFFIXES = _HANGUL_OPEN_SUFFIXES + ("이랑", "하고", "가", "이", "의", "에", "와", "과", "도", "로", "랑")


def _word_is_open(word: str) -> bool:
    if word.lower() in CONJUNCTIONS or word in PARTICLES:
        return True
    if _OPEN_END_RE.search(word):
        return True
    if _CLOSED_END_RE.search(word):
        return False
    for suffix in _LATIN_OPEN_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix):
            stem = word[: -len(suffix)]
            if re.search(r"[0-9A-Za-z]$", stem):
                return True
            if suffix in _HANGUL_OPEN_SUFFIXES and re.search(r"[가-힣]$", stem):
                return True
    return False


def looks_truncated(text: str | None) -> bool:
    """Heuristic: the message stops mid-sentence, e.g. it ends with a particle ("… baryon 가"), a conjunction
    ("그리고"), a comma, or a mention that is part of an unfinished phrase ("이 내용은 <@U1>").
    A message that ends with a closing mark or a sentence ending ("…해요", "…다", "?") is not truncated,
    and a bare ping ("<@U1>") or "확인 부탁해요 <@U1>" is not truncated either."""
    tokens = [t for t in tokenize(text) if not (t.kind == "text" and not t.text.strip())]
    if not tokens:
        return False
    last = tokens[-1]
    if last.kind in ("block", "code", "link", "emoji", "channel"):
        return False
    if last.kind in ("user", "usergroup", "special"):
        before = render(tokens[:-1]).strip()
        while before and re.search(r"@\S+$", before):  # "<@A> 랑 <@B>": look past the trailing mentions
            before = re.sub(r"\s*@\S+$", "", before)
        if not before:
            return False
        words = before.split()
        return _word_is_open(words[-1]) if words else False
    words = render(tokens).split()
    return _word_is_open(words[-1]) if words else False


# --------------------------------------------------------------------------- messages


@dataclass(frozen=True)
class Author:
    id: str                 # user id ("U…"), or the bot id ("B…") for bot posts without a user
    name: str
    is_bot: bool = False
    bot_id: str = ""
    app_name: str = ""


@dataclass(frozen=True)
class Mention:
    kind: str               # user | usergroup | special
    id: str
    name: str
    is_bot: bool = False
    is_me: bool = False


@dataclass(frozen=True)
class Link:
    url: str
    label: str = ""


@dataclass(frozen=True)
class File:
    id: str
    name: str
    mimetype: str = ""
    filetype: str = ""


@dataclass(frozen=True)
class Reaction:
    name: str
    count: int
    users: tuple[str, ...] = ()


@dataclass
class Message:
    ts: str
    thread_ts: str
    author: Author
    text_plain: str          # readable text, every mention as "@이름"
    text_for_me: str         # the same without a mention that only addresses the bot
    mentions: list[Mention] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    files: list[File] = field(default_factory=list)
    reactions: list[Reaction] = field(default_factory=list)
    edited: bool = False
    deleted: bool = False
    subtype: str = ""
    from_me: bool = False    # posted by the bot itself
    tokens: list[Token] = field(default_factory=list, repr=False)

    @property
    def mentions_me(self) -> bool:
        return any(m.is_me for m in self.mentions)

    @property
    def addressed_to_me(self) -> bool:
        """False when the message mentions other people or bots (or a user group) but not the bot.
        A message without mentions, or with only @here/@channel, may be for anyone, so it counts as True."""
        if self.mentions_me:
            return True
        return not any(m.kind in ("user", "usergroup") for m in self.mentions)

    @property
    def looks_truncated(self) -> bool:
        return looks_truncated("".join(t.raw for t in self.tokens) if self.tokens else self.text_plain)

    @property
    def speaker(self) -> str:
        """"이름" or "이름 (봇)", for "speaker: text" lines in a prompt ("" when the author is unknown)."""
        name = self.author.name
        return f"{name or '봇'} (봇)" if self.author.is_bot else name

    def line(self, for_me: bool = True) -> str:
        """"이름: 내용" (just the text when the author is unknown)."""
        text = self.text_for_me if for_me else self.text_plain
        return f"{self.speaker}: {text}" if self.speaker else text

    @classmethod
    def from_event(cls, event: dict, directory: UserDirectory | None = None, me: str = "") -> Message:
        """A Slack message event or ``conversations.replies`` item → Message.
        ``message_changed`` reads the edited message; ``message_deleted`` and tombstones are marked deleted."""
        subtype = event.get("subtype") or ""
        inner: dict[str, Any] = event
        if subtype == "message_changed" and isinstance(event.get("message"), dict):
            inner = event["message"]
        elif subtype == "message_deleted" and isinstance(event.get("previous_message"), dict):
            inner = event["previous_message"]
        deleted = subtype == "message_deleted" or inner.get("subtype") == "tombstone"
        tokens = [] if deleted else tokenize(inner.get("text"))
        user_id = inner.get("user") or ""
        bot_id = inner.get("bot_id") or ""
        profile = inner.get("bot_profile") or {}
        app_name = profile.get("name") or inner.get("username") or ""
        person = directory.get(user_id) if directory and user_id else None
        is_bot = bool(bot_id) or inner.get("subtype") == "bot_message" or bool(person and person.is_bot)
        name = (person.name if person else "") or app_name or user_id or bot_id
        if directory and user_id and app_name and not person:
            directory.remember(user_id, app_name, is_bot=True)
        author = Author(user_id or bot_id, name, is_bot, bot_id, app_name)

        mentions: list[Mention] = []
        seen: set[tuple[str, str]] = set()
        links: list[Link] = []
        for token in tokens:
            if token.kind in ("user", "usergroup", "special") and (token.kind, token.id) not in seen:
                seen.add((token.kind, token.id))
                if token.kind == "user":
                    known = _user_name(token, directory)
                    mentions.append(Mention("user", token.id, known, bool(directory and directory.is_bot(token.id)),
                                            bool(me) and token.id == me))
                else:
                    mentions.append(Mention(token.kind, token.id, token.label or token.id))
            elif token.kind == "link":
                links.append(Link(token.url, token.label))
        files = [File(f.get("id", ""), f.get("name") or f.get("title") or "", f.get("mimetype", ""), f.get("filetype", ""))
                 for f in inner.get("files") or [] if isinstance(f, dict)]
        reactions = [Reaction(r.get("name", ""), int(r.get("count") or len(r.get("users") or [])), tuple(r.get("users") or []))
                     for r in inner.get("reactions") or [] if isinstance(r, dict)]
        return cls(
            ts=inner.get("ts") or event.get("ts") or "",
            thread_ts=inner.get("thread_ts") or event.get("thread_ts") or "",
            author=author,
            text_plain=render(tokens, directory),
            text_for_me=render(tokens, directory, me),
            mentions=mentions,
            links=links,
            files=files,
            reactions=reactions,
            edited=subtype == "message_changed" or bool(inner.get("edited")),
            deleted=deleted,
            subtype=subtype,
            from_me=bool(me) and user_id == me,
            tokens=tokens,
        )
