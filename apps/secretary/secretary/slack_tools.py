"""Slack for the secretary: find what people asked (for example a meeting time), and answer there.

    search_messages      search.messages      needs a user token (xoxp-) with search:read
    list_conversations   conversations.list   channels, DMs and group DMs the token can see
    read_channel         conversations.history  recent messages of one conversation (name or id)
    post_message         chat.postMessage     a reply, after the owner's spoken yes (policy.py)

SECRETARY_SLACK_TOKEN holds the token. A user token sees what the owner sees (DMs included); a bot
token (xoxb-) sees only channels the bot was invited to and cannot search. Uses the standard
library only, so the secretary gains no dependency.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable

API = "https://slack.com/api/"
Call = Callable[[str, dict[str, Any]], dict[str, Any]]


def slack_call(token: str) -> Call:
    def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        data = urllib.parse.urlencode({key: value for key, value in params.items() if value not in (None, "")}).encode()
        request = urllib.request.Request(API + method, data=data, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https endpoint
            return json.loads(response.read().decode())
    return call


def _line(message: dict[str, Any], names: dict[str, str]) -> str:
    who = names.get(message.get("user", ""), message.get("username") or message.get("user") or "?")
    text = " ".join(str(message.get("text", "")).split())
    return f"[{message.get('ts', '')}] {who}: {text[:400]}"


class SlackReader:
    def __init__(self, call: Call) -> None:
        self.call = call
        self._names: dict[str, str] = {}
        self._channels: dict[str, str] = {}

    def _user(self, user_id: str) -> str:
        if user_id and user_id not in self._names:
            info = self.call("users.info", {"user": user_id})
            profile = (info.get("user") or {}).get("profile") or {}
            self._names[user_id] = profile.get("display_name") or profile.get("real_name") or user_id
        return self._names.get(user_id, user_id)

    def conversations(self) -> list[dict[str, str]]:
        result = self.call("conversations.list", {"types": "public_channel,private_channel,im,mpim",
                                                  "exclude_archived": "true", "limit": 200})
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "slack error"))
        rows = []
        for item in result.get("channels", []):
            name = item.get("name") or (f"DM {self._user(item.get('user', ''))}" if item.get("is_im") else item.get("id"))
            self._channels[name] = item["id"]
            rows.append({"id": item["id"], "name": name})
        return rows

    def resolve(self, channel: str) -> str:
        channel = channel.lstrip("#")
        if channel[:1] in {"C", "D", "G"} and channel.isupper():
            return channel
        if channel not in self._channels:
            self.conversations()
        return self._channels.get(channel, channel)

    def read(self, channel: str, limit: int = 20) -> str:
        result = self.call("conversations.history", {"channel": self.resolve(channel), "limit": max(1, min(limit, 50))})
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "slack error"))
        messages = list(reversed(result.get("messages", [])))
        for message in messages:
            self._user(message.get("user", ""))
        return "\n".join(_line(message, self._names) for message in messages) or "메시지가 없어요."

    def search(self, query: str, count: int = 10) -> str:
        result = self.call("search.messages", {"query": query, "count": max(1, min(count, 20)), "sort": "timestamp"})
        if not result.get("ok"):
            if result.get("error") in {"not_allowed_token_type", "missing_scope"}:
                return "슬랙 검색은 사용자 토큰(search:read)이 있어야 해요. 채널 이름을 알려 주시면 그 채널을 읽을게요."
            raise RuntimeError(result.get("error", "slack error"))
        matches = (result.get("messages") or {}).get("matches", [])
        return "\n".join(
            f"#{(match.get('channel') or {}).get('name', '?')} {_line(match, {match.get('user', ''): match.get('username', '')})}"
            for match in matches) or "찾은 메시지가 없어요."

    def post(self, channel: str, text: str, thread_ts: str = "") -> str:
        result = self.call("chat.postMessage", {"channel": self.resolve(channel), "text": text, "thread_ts": thread_ts})
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "slack error"))
        return f"보냈어요 (ts {result.get('ts')})."


def slack_tools(reader: SlackReader | None = None):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    reader = reader or SlackReader(slack_call(os.environ["SECRETARY_SLACK_TOKEN"]))

    def reply(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}]}

    async def run(function, *args) -> dict:
        try:
            return reply(await asyncio.to_thread(function, *args))
        except Exception as error:  # noqa: BLE001 - a Slack hiccup is something to say, not to crash on
            return reply(f"슬랙에서 오류가 났어요: {error}")

    @tool("search_messages", "Search the owner's Slack for messages (Slack search syntax: words, from:@name, in:#channel, "
          "after:2026-11-20). Use it to find meeting requests and proposed times.", {"query": str})
    async def search_messages(args: dict) -> dict:
        return await run(reader.search, str(args.get("query", "")))

    @tool("list_conversations", "List Slack channels and direct messages the owner can read.", {})
    async def list_conversations(args: dict) -> dict:
        return await run(lambda: "\n".join(f"{row['name']} ({row['id']})" for row in reader.conversations()))

    @tool("read_channel", "Read recent messages of one Slack channel or DM, by name (#general, 'DM 김대표') or id.",
          {"channel": str, "limit": int})
    async def read_channel(args: dict) -> dict:
        return await run(reader.read, str(args.get("channel", "")), int(args.get("limit") or 20))

    @tool("post_message", "Post a message to a Slack channel or DM, optionally as a thread reply (thread_ts). "
          "The owner confirms by voice first.", {"channel": str, "text": str, "thread_ts": str})
    async def post_message(args: dict) -> dict:
        return await run(reader.post, str(args.get("channel", "")), str(args.get("text", "")), str(args.get("thread_ts", "")))

    return create_sdk_mcp_server("slack", tools=[search_messages, list_conversations, read_channel, post_message])
