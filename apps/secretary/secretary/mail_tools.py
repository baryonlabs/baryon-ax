"""Mail drafts for the secretary, through gogcli (`gog`), without a shell in between.

"메일 초안으로 만들어 줘" should put a real draft in the owner's Gmail, and "보내" should send exactly that draft.
Shell commands are a poor fit: a multi-line body splits into several "commands" and quotes in the text break
them, so every draft became an unreadable confirmation. These tools pass arguments as a list.

    save_draft   create (or update) a Gmail draft — nothing is sent, so it runs without asking
    list_drafts  the newest drafts
    send_draft   send a saved draft; the owner confirms first (policy.py), and the tool refuses when the
                 recipients or subject it was told do not match the draft, so the confirmation read aloud
                 is what goes out

The account is gog's default (or SECRETARY_GOG_ACCOUNT). Sending can be switched off entirely with
SECRETARY_MAIL_SEND=off.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from typing import Any, Callable

Runner = Callable[[list[str]], dict]


def gog_runner(args: list[str]) -> dict:
    account = os.getenv("SECRETARY_GOG_ACCOUNT", "")
    command = ["gog", "gmail", *args, "-j", *(["--account", account] if account else [])]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)  # noqa: S603 - fixed program, list args
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:300] or f"gog exited {result.returncode}")
    try:
        return json.loads(result.stdout or "{}")
    except ValueError:
        return {"text": result.stdout.strip()[:500]}


def headers(draft: dict) -> dict[str, str]:
    message = (draft.get("draft") or draft).get("message") or {}
    return {item.get("name", "").lower(): item.get("value", "") for item in (message.get("payload") or {}).get("headers", [])}


def body_text(draft: dict) -> str:
    """The draft's plain-text body (Gmail keeps it base64url-encoded in a text/plain part)."""
    import base64

    def walk(part: dict) -> str:
        if part.get("mimeType") == "text/plain" and (part.get("body") or {}).get("data"):
            data = part["body"]["data"]
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        for child in part.get("parts") or []:
            found = walk(child)
            if found:
                return found
        return ""

    return walk(((draft.get("draft") or draft).get("message") or {}).get("payload") or {})


def addresses(value: str) -> set[str]:
    return {address.lower() for address in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value or "")}


def draft_link(message_id: str, account: str = "") -> str:
    """Open the draft in Gmail's web compose window, in the right account."""
    where = f"?authuser={account}" if account else "u/0/"
    return f"https://mail.google.com/mail/{where}#drafts?compose={message_id}" if message_id else ""


class Mail:
    def __init__(self, run: Runner = gog_runner, account: str | None = None) -> None:
        self.run = run
        self._account = account

    @property
    def account(self) -> str:
        """The Gmail address drafts go to: SECRETARY_GOG_ACCOUNT, or gog's default sign-in."""
        if self._account is None:
            self._account = os.getenv("SECRETARY_GOG_ACCOUNT", "")
            if not self._account:
                try:
                    listing = subprocess.run(["gog", "auth", "list", "-j"], capture_output=True, text=True, timeout=20)  # noqa: S603,S607
                    accounts = json.loads(listing.stdout or "{}").get("accounts") or []
                    self._account = next((item.get("email", "") for item in accounts if "gmail" in item.get("services", [])), "")
                except (OSError, ValueError, subprocess.SubprocessError):
                    self._account = ""
        return self._account

    def save_draft(self, to: str, subject: str, body: str, cc: str = "", draft_id: str = "",
                   reply_to_message_id: str = "") -> str:
        if draft_id and (not subject.strip() or not body.strip()):
            # Changing one thing (say, adding the recipient) keeps the rest of the draft as it is.
            current = self.run(["drafts", "get", draft_id])
            subject = subject.strip() or headers(current).get("subject", "")
            body = body if body.strip() else body_text(current)
            to = to if to.strip() else headers(current).get("to", "")
            cc = cc if cc.strip() else headers(current).get("cc", "")
        if not subject.strip() or not body.strip():
            return "제목과 본문이 있어야 초안을 만들 수 있어요."
        args = ["drafts", "update", draft_id] if draft_id else ["drafts", "create"]
        args += ["--subject", subject.strip(), "--body", body]
        if to.strip():
            args += ["--to", to.strip()]
        if cc.strip():
            args += ["--cc", cc.strip()]
        if reply_to_message_id.strip():
            args += ["--reply-to-message-id", reply_to_message_id.strip()]
        result = self.run(args)
        draft = result.get("draft") or result
        saved = draft.get("id") or draft_id
        message_id = (draft.get("message") or {}).get("id") or result.get("messageId", "")
        who = f"받는 사람 {to.strip()}" if to.strip() else "받는 사람은 아직 비어 있음"
        link = draft_link(message_id, self.account)
        return (f"Gmail 임시보관함에 초안을 저장했어요 (초안 id {saved}, {who}, 제목 '{subject.strip()}'). 아직 보내지 않았어요."
                + (f" Gmail에서 열기: {link}" if link else ""))

    def list_drafts(self, limit: int = 5) -> str:
        listing = self.run(["drafts", "list", "--max", str(max(1, min(limit, 20)))])
        rows = []
        for item in listing.get("drafts", [])[:limit]:
            detail = headers(self.run(["drafts", "get", item["id"]]))
            rows.append(f"{item['id']}: 받는 사람 {detail.get('to', '(없음)')}, 제목 {detail.get('subject', '(없음)')}")
        return "\n".join(rows) or "임시보관함에 초안이 없어요."

    def send_draft(self, draft_id: str, to: str, subject: str) -> str:
        if os.getenv("SECRETARY_MAIL_SEND", "on").strip().lower() in {"off", "0", "false", "no"}:
            return "메일 보내기는 꺼져 있어요(SECRETARY_MAIL_SEND=off). 초안은 임시보관함에 있어요."
        detail = headers(self.run(["drafts", "get", draft_id]))
        actual_to = addresses(detail.get("to", "") + "," + detail.get("cc", ""))
        if not actual_to:
            return "이 초안에는 받는 사람이 없어요. 받는 사람을 넣어 다시 저장한 뒤 보내세요."
        if not addresses(to) or addresses(to) != addresses(detail.get("to", "")):
            return f"확인한 받는 사람({to})이 초안의 받는 사람({detail.get('to', '')})과 달라서 보내지 않았어요."
        if subject.strip() and subject.strip() != detail.get("subject", "").strip():
            return f"확인한 제목이 초안 제목('{detail.get('subject', '')}')과 달라서 보내지 않았어요."
        self.run(["drafts", "send", draft_id])
        return f"보냈어요: {detail.get('to')}에게 '{detail.get('subject')}'."


def mail_tools(mail: Mail | None = None):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    mail = mail or Mail()

    def reply(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}]}

    async def run(function, *args: Any) -> dict:
        try:
            return reply(await asyncio.to_thread(function, *args))
        except Exception as error:  # noqa: BLE001 - a gog failure is something to say
            return reply(f"메일 작업이 실패했어요: {error}")

    @tool("save_draft", "Save an email as a draft in the owner's Gmail (Drafts). Nothing is sent. Use for '메일 초안 만들어 줘'. "
          "Leave 'to' empty if the address is unknown. Pass draft_id to change an existing draft: fields left empty keep "
          "their current value (to add only the recipient, pass draft_id and to). reply_to_message_id replies in a thread. "
          "Plain text body with line breaks.",
          {"to": str, "cc": str, "subject": str, "body": str, "draft_id": str, "reply_to_message_id": str})
    async def save_draft(args: dict) -> dict:
        return await run(mail.save_draft, str(args.get("to") or ""), str(args.get("subject") or ""), str(args.get("body") or ""),
                         str(args.get("cc") or ""), str(args.get("draft_id") or ""), str(args.get("reply_to_message_id") or ""))

    @tool("list_drafts", "List the newest Gmail drafts with their recipients and subjects.", {"limit": int})
    async def list_drafts(args: dict) -> dict:
        return await run(mail.list_drafts, int(args.get("limit") or 5))

    @tool("send_draft", "Send a saved Gmail draft. Give the draft id and the recipient and subject as they will be read back to "
          "the owner; the owner confirms by voice, and the tool refuses if they do not match the draft.",
          {"draft_id": str, "to": str, "subject": str})
    async def send_draft(args: dict) -> dict:
        return await run(mail.send_draft, str(args.get("draft_id") or ""), str(args.get("to") or ""), str(args.get("subject") or ""))

    return create_sdk_mcp_server("mail", tools=[save_draft, list_drafts, send_draft])
