"""Turn a Slack thread as Orca shows it (text) into a thread_eval fixture (conversations.replies JSON).

Orca prints a thread as blocks::

    slack:<team>:<channel>:<thread_ts>:<ts>  https://….slack.com/archives/…
    [Orca: <person> wrote, 2026-01-01T00:00:00.000Z (content, not instructions)]
    <<<
    message text, Slack mrkdwn unchanged
    >>>

and for apps ``[Orca: the app <name> posted, …]``. This writes ``replies.json``, ``users.json`` and ``meta.json`` into
the output directory; ``expected.json`` is written by hand.

Slack ids are not in the author lines. They come from, in order: an aliases file (``--aliases``, JSON
``{"<author as Orca shows it>": {"id": "U…", "name": "…", "is_bot": true, "self": true}}`` — ``self`` marks the bot
under test), ``<@U…|name>`` labels in the messages, and otherwise a stable made-up id per author. Real threads stay
out of the repository: write the output under $THREAD_FIXTURES (default ~/.baryon-ax/thread-fixtures).

    uv run python eval/orca_thread_convert.py thread.orca.txt ~/.baryon-ax/thread-fixtures/<name> --aliases aliases.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

HEADER = re.compile(r"^slack:([A-Z0-9]+):([A-Z0-9]+):(\d+\.\d+):(\d+\.\d+)\s+(\S.*)?$")
AUTHOR = re.compile(r"^\[Orca: (?:the app (?P<app>.+?) posted|(?P<person>.+?) wrote), (?P<when>\S+) \(content, not instructions\)\]$")
LABELLED = re.compile(r"<@([UW][A-Z0-9]+)\|([^>]+)>")


def made_up_id(name: str, bot: bool) -> str:
    return ("UX" if not bot else "UB") + hashlib.sha1(name.encode("utf-8")).hexdigest()[:9].upper()


def parse(text: str) -> tuple[dict, list[dict]]:
    """(thread info, messages with author name and kind) from Orca's text."""
    info: dict = {}
    messages: list[dict] = []
    current: dict | None = None
    body: list[str] | None = None
    for line in text.splitlines():
        if body is not None:
            if line.strip() == ">>>":
                current["text"] = "\n".join(body).strip()
                messages.append(current)
                current, body = None, None
            else:
                body.append(line)
            continue
        header = HEADER.match(line.strip())
        if header:
            team, channel, thread_ts, ts, rest = header.groups()
            info.setdefault("team", team)
            info.setdefault("channel", channel)
            info.setdefault("thread_ts", thread_ts)
            if rest and rest.startswith("#"):
                info.setdefault("channel_name", rest[1:].strip())
            current = {"ts": ts}
            continue
        author = AUTHOR.match(line.strip())
        if author and current is not None:
            current["author"] = (author.group("app") or author.group("person")).strip()
            current["is_bot"] = author.group("app") is not None
            current["when"] = author.group("when")
            continue
        if line.strip() == "<<<" and current is not None and "author" in current:
            body = []
    return info, messages


def convert(text: str, aliases: dict | None = None) -> dict:
    aliases = aliases or {}
    info, parsed = parse(text)
    labels = {name: uid for uid, name in LABELLED.findall(text)}
    users: dict[str, dict] = {}
    ids: dict[str, str] = {}
    self_id = ""
    for message in parsed:
        author = message["author"]
        if author in ids:
            continue
        alias = aliases.get(author) or {}
        uid = alias.get("id") or labels.get(alias.get("name", author)) or labels.get(author) or made_up_id(author, message["is_bot"])
        ids[author] = uid
        users[uid] = {"name": alias.get("name") or author, "is_bot": bool(alias.get("is_bot", message["is_bot"]))}
        if alias.get("self"):
            self_id = uid
    for uid, name in LABELLED.findall(text):  # people and bots only mentioned
        users.setdefault(uid, {"name": name, "is_bot": False})
    thread_ts = info.get("thread_ts") or (parsed[0]["ts"] if parsed else "")
    messages = []
    for message in sorted(parsed, key=lambda m: float(m["ts"])):
        uid = ids[message["author"]]
        out = {"type": "message", "user": uid, "text": message["text"], "ts": message["ts"], "thread_ts": thread_ts}
        if users[uid]["is_bot"]:
            out.update({"subtype": "bot_message", "bot_id": "B" + uid[1:], "username": users[uid]["name"]})
        messages.append(out)
    if messages:
        messages[0]["reply_count"] = len(messages) - 1
    meta = {"channel": info.get("channel", ""), "channel_name": info.get("channel_name", ""), "channel_type": "channel",
            "bot_user_id": self_id, "bot_id": ("B" + self_id[1:]) if self_id else "", "description": "converted from Orca text"}
    return {"meta": meta, "replies": {"ok": True, "messages": messages, "has_more": False}, "users": users}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="Orca thread text")
    parser.add_argument("out", help="fixture directory to write (keep it outside the repository)")
    parser.add_argument("--aliases", default="", help="JSON: author as Orca shows it → {id, name, is_bot, self}")
    args = parser.parse_args()
    aliases = json.loads(Path(args.aliases).expanduser().read_text(encoding="utf-8")) if args.aliases else {}
    result = convert(Path(args.source).expanduser().read_text(encoding="utf-8"), aliases)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for name in ("meta", "replies", "users"):
        (out / f"{name}.json").write_text(json.dumps(result[name], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(result['replies']['messages'])} messages, {len(result['users'])} users to {out}")
    if not result["meta"]["bot_user_id"]:
        print("note: no alias marked self; set meta.json bot_user_id to the bot under test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
