"""What the secretary knows about its owner, editable from the phone app and by voice.

    about      자기 소개: who the owner is (name, role, team, projects, the people they work with)
    usage      활용: how the owner wants the secretary to work (tone, length, what to check first)
    memories   short facts to keep ("이거 기억해 줘"), added by voice or in the app
    mcp        which MCP connections (from ~/.claude.json) the secretary may use

Saved as JSON in SECRETARY_STATE_DIR/profile.json on the workstation. MCP connections are
listed by name and kind only; their commands, URLs and credentials never leave ~/.claude.json.
A change applies from the next call (a running agent keeps the tools it started with).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

MAX_TEXT = 2000
MAX_MEMORY = 300
MAX_MEMORIES = 200
IN_PROMPT = 60  # newest memories put in the secretary's instructions


def claude_mcp_servers() -> dict[str, dict]:
    """MCP servers the owner configured for Claude Code (user scope and every project)."""
    path = Path(os.getenv("SECRETARY_CLAUDE_CONFIG", str(Path.home() / ".claude.json")))
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    found: dict[str, dict] = dict(data.get("mcpServers", {}))
    for project in data.get("projects", {}).values():
        for name, spec in project.get("mcpServers", {}).items():
            found.setdefault(name, spec)
    return found


def mcp_kind(spec: dict) -> str:
    kind = str(spec.get("type") or ("stdio" if spec.get("command") else "http"))
    return kind if kind in {"stdio", "http", "sse"} else "other"


class Profile:
    def __init__(self, state_dir: Path, default_mcp: list[str]) -> None:
        self.path = Path(state_dir) / "profile.json"
        self._lock = threading.Lock()
        self.data: dict[str, Any] = {"about": "", "usage": "", "memories": [], "mcp": list(default_mcp)}
        try:
            saved = json.loads(self.path.read_text())
            self.data.update({key: saved[key] for key in self.data if key in saved})
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        os.chmod(temporary, 0o600)  # personal notes
        temporary.replace(self.path)

    # --- reading -------------------------------------------------------------------------

    @property
    def mcp_enabled(self) -> list[str]:
        return list(self.data["mcp"])

    def public(self) -> dict:
        """For the app: the texts, the memories, and every MCP connection with on/off."""
        available = claude_mcp_servers()
        enabled = set(self.data["mcp"])
        names = sorted(set(available) | enabled)
        return {
            "about": self.data["about"],
            "usage": self.data["usage"],
            "memories": list(reversed(self.data["memories"])),  # newest first
            "mcp": [{"name": name, "kind": mcp_kind(available.get(name, {})), "enabled": name in enabled,
                     "installed": name in available} for name in names],
        }

    def prompt(self) -> str:
        """The part of the secretary's instructions that is about its owner."""
        parts = []
        if self.data["about"].strip():
            parts.append(f"사용자 소개(사용자가 직접 쓴 것):\n{self.data['about'].strip()}")
        if self.data["usage"].strip():
            parts.append(f"사용자가 원하는 활용 방식(이대로 따른다):\n{self.data['usage'].strip()}")
        memories = self.data["memories"][-IN_PROMPT:]
        if memories:
            parts.append("기억해 둔 것(id: 내용):\n" + "\n".join(f"- {m['id']}: {m['text']}" for m in memories))
        return "\n\n".join(parts)

    # --- changing ------------------------------------------------------------------------

    def update(self, *, about: str | None = None, usage: str | None = None, mcp: list[str] | None = None) -> None:
        with self._lock:
            if about is not None:
                self.data["about"] = about.strip()[:MAX_TEXT]
            if usage is not None:
                self.data["usage"] = usage.strip()[:MAX_TEXT]
            if mcp is not None:
                known = set(claude_mcp_servers()) | set(self.data["mcp"])
                self.data["mcp"] = sorted({name for name in mcp if name in known})
            self._save()

    def remember(self, text: str, source: str = "app") -> dict:
        text = " ".join(text.split())[:MAX_MEMORY]
        if not text:
            raise ValueError("empty memory")
        with self._lock:
            memory = {"id": uuid.uuid4().hex[:6], "text": text, "at": int(time.time()), "source": source}
            self.data["memories"] = (self.data["memories"] + [memory])[-MAX_MEMORIES:]
            self._save()
        return memory

    def forget(self, memory_id: str) -> bool:
        with self._lock:
            before = len(self.data["memories"])
            self.data["memories"] = [m for m in self.data["memories"] if m["id"] != memory_id]
            if len(self.data["memories"]) == before:
                return False
            self._save()
        return True


def memory_tools(profile: Profile):
    """By voice: "이거 기억해 줘", "뭐 기억하고 있어?", "그건 잊어"."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def reply(text: str) -> dict:
        return {"content": [{"type": "text", "text": text}]}

    @tool("remember", "Save a short fact about the user or their work that should be kept for future calls "
          "(only when the user asks you to remember something, or states a lasting preference).", {"text": str})
    async def remember(args: dict) -> dict:
        try:
            memory = profile.remember(str(args.get("text", "")), source="voice")
        except ValueError:
            return reply("기억할 내용이 비어 있어요.")
        return reply(f"기억했어요 (id {memory['id']}).")

    @tool("list_memories", "List what you remember about the user, newest first.", {})
    async def list_memories(args: dict) -> dict:
        memories = profile.public()["memories"]
        return reply("\n".join(f"{m['id']}: {m['text']}" for m in memories) or "기억해 둔 것이 없어요.")

    @tool("forget", "Delete one remembered fact by its id.", {"id": str})
    async def forget(args: dict) -> dict:
        return reply("지웠어요." if profile.forget(str(args.get("id", ""))) else "그런 기억이 없어요.")

    return create_sdk_mcp_server("memory", tools=[remember, list_memories, forget])
