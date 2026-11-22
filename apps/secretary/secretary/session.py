"""One live conversation with the secretary: a long-lived Claude Agent SDK session.

Keeping the agent process alive for the whole call avoids several seconds of startup
per turn. Each turn's output is turned into small events that a voice client speaks:

    {"type": "text", "delta": "..."}         answer text as it streams
    {"type": "status", "text": "..."}        spoken filler while a tool runs
    {"type": "confirm", "id", "prompt"}      a yes/no question before a side effect
    {"type": "confirmed", "id", "allow"}     the user's answer was applied
    {"type": "done", "cost_usd", "ms"}       the turn finished
    {"type": "error", "message"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .policy import confirmation_prompt, decide, parse_answer, status_label

logger = logging.getLogger("secretary")

CONFIRM_TIMEOUT = float(os.getenv("SECRETARY_CONFIRM_TIMEOUT", "120"))
INTERRUPT_GRACE = 5.0  # seconds to let an interrupted turn wind down before the next starts
SILENT_TOOLS = {"ToolSearch", "TodoWrite"}  # internal bookkeeping: nothing worth saying aloud
STOP_WORDS = {"멈춰", "그만", "중지", "스톱", "stop", "취소"}

PERSONA = """너는 사용자의 음성 비서다. 사용자는 운전 중일 수 있고 화면을 볼 수 없다.
- 결론부터 2~3문장의 자연스러운 한국어 구어체로 말한다. 마크다운, 표, 코드 블록, URL, 이모지를 쓰지 않는다.
- 숫자와 이름은 귀로 듣기 쉽게 말한다. 목록은 "첫째, 둘째"처럼 말하고 세 개를 넘기지 않는다. 더 있으면 "더 들으실래요?"라고 묻는다.
- 도구를 쓰기 전에 무엇을 할지 한 문장으로 먼저 말한다. 변경이 생기는 작업은 시스템이 사용자에게 확인을 받으니, 거절되면 이유를 묻지 말고 다른 방법을 제안한다.
- 코딩 지시(버그 수정, 기능 구현, 테스트, 리뷰)는 DureClaw 플릿에 send_task로 맡긴다. 먼저 get_presence로 코딩 가능한 노드를 고르고, 지시문에는 저장소, 목표, 완료 조건(테스트 통과, PR 생성 등)을 구체적으로 쓴다. 보낸 뒤에는 task_id를 기억했다가 "결과 어때?"라는 질문에 read_mailbox로 확인한다.
- 요약 요청(메일, 일정, 할 일, PR과 CI, 아이디어)은 핵심 세 가지만 말한다.
- 조회는 빠르게: gh, git 명령은 한 줄로 쓰고 결과 가공은 jq, head, grep까지만 쓴다. python이나 스크립트로 가공하지 않는다(확인 절차가 생겨 느려진다). PR과 CI는 `gh pr list`와 `gh run list --limit 5`로 충분하다.
- 아이디어를 말하면 짧게 정리해 되읽어 주고, 저장을 원하면 할 일로 만든다.
- 잘 못 알아들었으면 짐작하지 말고 짧게 되묻는다.
오늘은 {today}이다."""


def imported_mcp_servers(names: list[str]) -> dict[str, dict]:
    """Copy MCP server definitions the user already configured for Claude Code (~/.claude.json),
    so credentials stay where they are and nothing secret lives in this repository."""
    if not names:
        return {}
    path = Path(os.getenv("SECRETARY_CLAUDE_CONFIG", str(Path.home() / ".claude.json")))
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        logger.warning("cannot read %s; no MCP servers imported", path)
        return {}
    found: dict[str, dict] = dict(data.get("mcpServers", {}))
    for project in data.get("projects", {}).values():
        for name, spec in project.get("mcpServers", {}).items():
            found.setdefault(name, spec)
    return {name: found[name] for name in names if name in found}


def build_options(can_use_tool: Callable, *, cwd: str, model: str, mcp_names: list[str]):
    from claude_agent_sdk import ClaudeAgentOptions

    today = datetime.now(ZoneInfo(os.getenv("SECRETARY_TZ", "Asia/Seoul"))).strftime("%Y년 %m월 %d일 %A %H시 %M분")
    return ClaudeAgentOptions(
        model=model,
        cwd=cwd,
        include_partial_messages=True,
        can_use_tool=can_use_tool,
        setting_sources=[],  # lean: no user hooks/plugins/CLAUDE.md, which add seconds and cost per turn
        mcp_servers=imported_mcp_servers(mcp_names),
        system_prompt={"type": "preset", "preset": "claude_code", "append": PERSONA.format(today=today)},
    )


class Session:
    def __init__(self, client_factory: Callable[[Any], Any], *, owner: str, cwd: str, model: str, mcp_names: list[str],
                 options_factory: Callable[..., Any] = build_options) -> None:
        self.id = uuid.uuid4().hex
        self.owner = owner
        self.created = self.last_active = time.time()
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.pending: dict | None = None
        self.busy = False
        self.backlog: list[str] = []
        self.turns = 0
        self.closed = False
        self._task: asyncio.Task | None = None
        self._last_status = ""
        self._after_tool = False
        self._client = client_factory(options_factory(self._can_use_tool, cwd=cwd, model=model, mcp_names=mcp_names))

    async def start(self) -> None:
        await self._client.connect()

    async def close(self) -> None:
        self.closed = True
        self._resolve_pending(False)
        if self._task and not self._task.done():
            self._task.cancel()
        try:
            await self._client.disconnect()
        except Exception:  # the CLI may already be gone
            logger.debug("disconnect failed", exc_info=True)

    def emit(self, event: dict) -> None:
        self.events.put_nowait(event)

    # --- user input -------------------------------------------------------------------

    async def turn(self, text: str) -> str:
        """Handle one utterance. Returns how it was used: confirm | answer | interrupt | queued."""
        self.last_active = time.time()
        text = text.strip()
        if self.pending:
            answer = parse_answer(text)
            if answer is not None:
                self._resolve_pending(answer)
                return "confirm"
            self._resolve_pending(False)  # anything else counts as "no", then becomes the next request
        if self.busy:
            await self.interrupt()
            if text.replace(" ", "").rstrip(".!") in STOP_WORDS:
                return "interrupt"
            # In a conversation the newest request wins: the old answer is dropped, not queued.
            if self._task:
                try:
                    await asyncio.wait_for(asyncio.shield(self._task), INTERRUPT_GRACE)
                except (asyncio.TimeoutError, Exception):
                    pass
        self._task = asyncio.create_task(self._run(text))
        return "answer"

    async def interrupt(self) -> None:
        self._resolve_pending(False)
        self.backlog.clear()
        if self.busy:
            try:
                await self._client.interrupt()
            except Exception:
                logger.debug("interrupt failed", exc_info=True)

    # --- agent loop -------------------------------------------------------------------

    async def _run(self, text: str) -> None:
        self.busy = True
        self.turns += 1
        started = time.perf_counter()
        self._last_status = ""
        try:
            await self._client.query(text)
            async for message in self._client.receive_response():
                self._translate(message, started)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # report to the caller instead of dropping the line silently
            logger.exception("turn failed")
            self.emit({"type": "error", "message": str(error)[:200]})
            self.emit({"type": "done", "cost_usd": None, "ms": int((time.perf_counter() - started) * 1000)})
        finally:
            self.busy = False
        if self.backlog and not self.closed:
            self._task = asyncio.create_task(self._run(self.backlog.pop(0)))

    def _translate(self, message: Any, started: float) -> None:
        kind = type(message).__name__
        if kind == "StreamEvent":
            event = message.event
            if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                delta = event["delta"]["text"]
                if self._after_tool and delta and not delta[0].isspace():
                    delta = "\n" + delta  # text resumed after a tool call: keep sentences apart
                self._after_tool = False
                self.emit({"type": "text", "delta": delta})
        elif kind == "AssistantMessage":
            for block in message.content:
                if type(block).__name__ == "ToolUseBlock":
                    self._after_tool = True
                    if block.name in SILENT_TOOLS:
                        continue
                    label = status_label(block.name, block.input or {})
                    if not self._last_status:  # one spoken filler per turn is enough
                        self.emit({"type": "status", "text": label, "tool": block.name})
                        self._last_status = label
        elif kind == "ResultMessage":
            self.emit({"type": "done", "cost_usd": getattr(message, "total_cost_usd", None),
                       "ms": int((time.perf_counter() - started) * 1000)})

    # --- permissions ------------------------------------------------------------------

    async def _can_use_tool(self, tool: str, tool_input: dict, context: Any = None):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        decision = decide(tool, tool_input)
        logger.info("tool %s -> %s %s", tool, decision.action, json.dumps(tool_input, ensure_ascii=False)[:300])
        if decision.action == "auto":
            return PermissionResultAllow()
        if decision.action == "deny":
            self.emit({"type": "status", "text": "그 작업은 안전을 위해 막아 두었어요.", "tool": tool})
            return PermissionResultDeny(message=f"Blocked by the voice secretary policy: {decision.reason}. Do not retry.")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        confirm_id = uuid.uuid4().hex[:8]
        prompt = confirmation_prompt(tool, tool_input)
        self.pending = {"id": confirm_id, "tool": tool, "prompt": prompt, "future": future}
        self.emit({"type": "confirm", "id": confirm_id, "prompt": prompt, "tool": tool})
        try:
            allowed = await asyncio.wait_for(future, CONFIRM_TIMEOUT)
        except asyncio.TimeoutError:
            allowed = False
        finally:
            if self.pending and self.pending["id"] == confirm_id:
                self.pending = None
        self.emit({"type": "confirmed", "id": confirm_id, "allow": allowed, "tool": tool})
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user said no (or did not answer). Do not run it; ask what to do instead.")

    def _resolve_pending(self, allow: bool) -> None:
        if self.pending and not self.pending["future"].done():
            self.pending["future"].set_result(allow)
        self.pending = None
