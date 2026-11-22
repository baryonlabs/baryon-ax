"""Notifications to the owner's phone: now, or at a set time (a reminder), with or without an alarm.

The secretary keeps them; the phone gateway listens on GET /v1/notifications/stream and passes each one
to the app, which rings (and, for an alarm, wakes the screen) even when the phone is asleep. No push
service (FCM) is involved: the app keeps its own connection to the gateway over the tailnet.

A notification stays "sent" until the phone acknowledges it, and is sent again whenever the gateway
reconnects, so one that went out while the phone was offline is not lost. The app ignores repeats by id.

    scheduled  waiting for its time
    sent       its time came; given to every listening gateway, not yet acknowledged
    acked      the phone showed it
    cancelled
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.getenv("SECRETARY_TZ", "Asia/Seoul"))
KEEP_SECONDS = 7 * 24 * 3600  # finished notifications are forgotten after a week
MAX_PENDING = 200


def parse_when(when: str | int | float | None, now: float | None = None) -> float:
    """Epoch seconds for "now", a number of minutes from now, "HH:MM" (today, or tomorrow if past),
    or an ISO date-time in the secretary's time zone ("2026-11-24 09:00", "2026-11-24T09:00:00+09:00")."""
    now = time.time() if now is None else now
    if when in (None, "", "now", "지금"):
        return now
    if isinstance(when, (int, float)):
        return now + float(when) * 60
    text = str(when).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return now + float(text) * 60
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if match:
        local = datetime.fromtimestamp(now, TZ).replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
        if local.timestamp() <= now:
            local += timedelta(days=1)
        return local.timestamp()
    moment = datetime.fromisoformat(text.replace(" ", "T"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return moment.timestamp()


def spoken_time(at: float) -> str:
    local = datetime.fromtimestamp(at, TZ)
    return f"{local.month}월 {local.day}일 {local.hour}시" + (f" {local.minute}분" if local.minute else "")


class Notifier:
    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / "notifications.json"
        self.items: list[dict[str, Any]] = []
        self.listeners: set[asyncio.Queue] = set()
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text())
            except (ValueError, OSError):
                self.items = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.items, ensure_ascii=False, indent=1))
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def add(self, title: str, text: str = "", when: str | int | float | None = None, alarm: bool = False,
            source: str = "secretary") -> dict[str, Any]:
        title = " ".join(str(title).split())[:120]
        if not title:
            raise ValueError("title required")
        if sum(item["status"] in {"scheduled", "sent"} for item in self.items) >= MAX_PENDING:
            raise ValueError("too many pending notifications")
        item = {"id": uuid.uuid4().hex[:12], "title": title, "text": " ".join(str(text).split())[:600],
                "at": parse_when(when), "alarm": bool(alarm), "source": source[:40], "status": "scheduled",
                "created": time.time()}
        self.items.append(item)
        self._save()
        self.tick()
        return item

    def cancel(self, notification_id: str) -> bool:
        for item in self.items:
            if item["id"] == notification_id and item["status"] in {"scheduled", "sent"}:
                item["status"] = "cancelled"
                self._save()
                return True
        return False

    def ack(self, notification_id: str) -> bool:
        for item in self.items:
            if item["id"] == notification_id and item["status"] == "sent":
                item["status"] = "acked"
                item["acked"] = time.time()
                self._save()
                return True
        return False

    def pending(self) -> list[dict[str, Any]]:
        return sorted((item for item in self.items if item["status"] in {"scheduled", "sent"}), key=lambda item: item["at"])

    def unacked(self) -> list[dict[str, Any]]:
        return [item for item in self.pending() if item["status"] == "sent"]

    def tick(self, now: float | None = None) -> list[dict[str, Any]]:
        """Send what is due, and forget what finished long ago."""
        now = time.time() if now is None else now
        due = [item for item in self.items if item["status"] == "scheduled" and item["at"] <= now]
        for item in due:
            item["status"] = "sent"
            item["sent"] = now
        before = len(self.items)
        self.items = [item for item in self.items
                      if item["status"] in {"scheduled", "sent"} or now - item.get("acked", item["at"]) < KEEP_SECONDS]
        if due or len(self.items) != before:
            self._save()
        for item in due:
            self.publish(item)
        return due

    def publish(self, item: dict[str, Any]) -> None:
        for queue in list(self.listeners):
            queue.put_nowait(public(item))

    def listen(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for item in self.unacked():  # what went out while nobody was listening
            queue.put_nowait(public(item))
        self.listeners.add(queue)
        return queue

    def unlisten(self, queue: asyncio.Queue) -> None:
        self.listeners.discard(queue)

    async def run(self, every: float = 2.0) -> None:
        while True:
            self.tick()
            await asyncio.sleep(every)


def public(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "notify", "id": item["id"], "title": item["title"], "text": item["text"],
            "alarm": item["alarm"], "at": item["at"]}


def notify_tools(notifier: Notifier):
    """The secretary's tools: tell the owner on the phone now, or remind them later."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def reply(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}]}

    @tool("notify_phone",
          "Ring the owner's phone with a notification, now or at a time: a reminder ('9시에 회의 알려 줘'), "
          "a heads-up when a long task finishes, or a wake-up alarm. "
          "when: empty for now, minutes from now (e.g. 10), 'HH:MM' (next occurrence), or 'YYYY-MM-DD HH:MM' "
          "in the owner's time zone. alarm: true rings like an alarm clock and turns the screen on, for wake-ups and "
          "things that must not be missed; false is a normal notification sound.",
          {"title": str, "text": str, "when": str, "alarm": bool})
    async def notify_phone(args: dict) -> dict:
        try:
            item = notifier.add(str(args.get("title", "")), str(args.get("text", "")), args.get("when") or None,
                                bool(args.get("alarm")))
        except ValueError as error:
            return reply(f"알림을 만들지 못했어요: {error}")
        kind = "알람" if item["alarm"] else "알림"
        if item["at"] <= time.time() + 1:
            return reply(f"지금 폰으로 {kind}을 보냈어요 (id {item['id']}).")
        return reply(f"{spoken_time(item['at'])}에 {kind}을 울릴게요 (id {item['id']}).")

    @tool("list_reminders", "List the notifications and alarms still waiting to ring on the owner's phone.", {})
    async def list_reminders(args: dict) -> dict:
        rows = [f"{item['id']} {spoken_time(item['at'])} {'알람' if item['alarm'] else '알림'}: {item['title']}"
                for item in notifier.pending()]
        return reply("\n".join(rows) or "예약된 알림이 없어요.")

    @tool("cancel_reminder", "Cancel a waiting notification or alarm by id (from list_reminders).", {"id": str})
    async def cancel_reminder(args: dict) -> dict:
        return reply("취소했어요." if notifier.cancel(str(args.get("id", ""))) else "그 알림을 찾지 못했어요.")

    return create_sdk_mcp_server("phone", tools=[notify_phone, list_reminders, cancel_reminder])
