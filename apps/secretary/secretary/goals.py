"""Goal agents: background workers the resident secretary starts from a conversation.

"로그인 버그 고쳐서 테스트까지 통과시켜 줘" becomes a goal. A goal agent works on it on its
own while the conversation goes on, several at once:

* each goal gets its own git worktree and branch (goal/<id>-...), so parallel agents never
  write over each other or over the checkout you are working in;
* it plans, does the work and checks it against the "done when" criteria, then reports;
* shell commands run in the Claude Code sandbox (writes stay in the worktree, network only to
  package registries) and file edits outside the worktree are refused. Nothing leaves the
  machine: no push, no PR, no mail. Publishing the result is a separate, spoken request to the
  secretary, which asks for confirmation as usual;
* when it is stuck it asks a question (status "needs_input"); the secretary relays it and
  passes the answer back.

Goals are kept in a JSON file, so the list survives restarts of the secretary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .policy import DENY_BASH

logger = logging.getLogger("secretary.goals")

GOAL_PROMPT = """너는 목표 에이전트다. 사용자의 비서가 아래 목표를 너에게 맡겼고, 사용자는 지금 운전 중이라 너를 지켜보지 않는다.
목표: {goal}
완료 조건: {done_when}
작업 위치: {worktree} (브랜치 {branch}) — 이 안에서만 파일을 고친다.

일하는 방식:
1. 먼저 저장소를 살펴 짧게 계획한다(TodoWrite).
2. 작게 고치고, 테스트·빌드로 확인하며 반복한다. 완료 조건을 실제로 확인하기 전에는 끝났다고 하지 않는다.
3. 끝나면 이 브랜치에 커밋한다. push, PR, 메일, 배포는 하지 않는다(필요하면 최종 보고에 "push 필요"라고 쓴다).
4. 결정할 수 없는 일(요구가 모호함, 위험한 변경)은 ask_user 도구로 한 문장 질문을 하고 답을 기다린다.
5. 마지막 메시지는 비서가 사용자에게 읽어 줄 보고다: 한국어 2~3문장, 결과와 확인 방법(테스트 결과), 브랜치 이름. 마크다운 금지.
"""

ALWAYS_OK = {"Read", "Grep", "Glob", "LS", "TodoWrite", "ToolSearch", "WebSearch", "WebFetch", "NotebookRead"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Leaving the machine is the secretary's job, with a spoken yes. A goal agent never does it.
OUTWARD = re.compile(r"\bgit\s+push\b|\bgh\s+(?:pr|release|issue|repo)\s+(?:create|merge|edit|close|delete)|\bnpm\s+publish\b"
                     r"|\btwine\s+upload\b|\bdocker\s+push\b|\bssh\b|\bscp\b|\brsync\b.*:|\bcurl\b.*(?:-X\s*(?:POST|PUT|DELETE)|--data|-d\s)")
PACKAGE_REGISTRIES = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "pub.dev", "storage.googleapis.com"]
STATUSES = ("queued", "running", "needs_input", "done", "failed", "cancelled", "interrupted")


@dataclass
class Goal:
    id: str
    title: str
    goal: str
    done_when: str
    repo: str
    branch: str = ""
    worktree: str = ""
    status: str = "queued"
    steps: list[str] = field(default_factory=list)
    question: str = ""
    summary: str = ""
    cost_usd: float | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def brief(self) -> dict:
        """What the secretary and the app need: no paths."""
        return {"id": self.id, "title": self.title, "status": self.status, "step": self.steps[-1] if self.steps else "",
                "question": self.question, "summary": self.summary, "branch": self.branch}


def slug(text: str) -> str:
    ascii_words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return ascii_words[:24] or "work"


def goal_permission(goal: Goal, tool: str, tool_input: dict) -> tuple[str, str]:
    """allow | deny | ask, with a reason. Shell commands are also confined by the sandbox."""
    if tool in ALWAYS_OK or tool.endswith("__ask_user"):
        return "allow", ""
    if tool in EDIT_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        root = os.path.realpath(goal.worktree)
        target = os.path.realpath(path if os.path.isabs(path) else os.path.join(goal.worktree, path))
        if target == root or target.startswith(root + os.sep):
            return "allow", ""
        return "deny", "Edit files only inside your worktree."
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if DENY_BASH.search(command):
            return "deny", "Destructive command refused."
        if OUTWARD.search(command):
            return "deny", "Goal agents never push, open PRs or send anything. Commit locally and say 'push 필요' in your report."
        return "allow", ""
    return "deny", f"{tool} is not available to goal agents."


class GoalManager:
    def __init__(self, state_dir: Path, client_factory: Callable[[Any], Any], notify: Callable[[Goal, str], None], *,
                 model: str = "sonnet", max_parallel: int = 3, options_factory: Callable[..., Any] | None = None,
                 make_worktree: Callable[[Goal], None] | None = None) -> None:
        self.state_file = state_dir / "goals.json"
        self.client_factory = client_factory
        self.notify = notify  # (goal, event) -> None; event: started | step | needs_input | done | failed | cancelled
        self.model = model
        self.slots = asyncio.Semaphore(max_parallel)
        self.options_factory = options_factory or self._options
        self.make_worktree = make_worktree or self._make_worktree
        self.goals: dict[str, Goal] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.answers: dict[str, asyncio.Future] = {}
        self._load()

    # --- persistence ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            for raw in json.loads(self.state_file.read_text()):
                goal = Goal(**raw)
                if goal.status in ("queued", "running", "needs_input"):
                    goal.status = "interrupted"  # the secretary restarted; the worktree is still there
                self.goals[goal.id] = goal
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(goal) for goal in self.goals.values()], ensure_ascii=False, indent=1))
        tmp.replace(self.state_file)

    def _update(self, goal: Goal, event: str, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(goal, key, value)
        goal.updated = time.time()
        self._save()
        try:
            self.notify(goal, event)
        except Exception:
            logger.exception("notify failed")

    # --- the secretary's tools ----------------------------------------------------------

    def start(self, title: str, goal: str, done_when: str, repo: str) -> Goal:
        item = Goal(id=uuid.uuid4().hex[:6], title=title.strip()[:80], goal=goal.strip()[:2000],
                    done_when=(done_when.strip() or "요청한 변경이 동작하고 기존 테스트가 통과한다")[:500], repo=repo)
        item.branch = f"goal/{item.id}-{slug(title)}"
        self.goals[item.id] = item
        self._save()
        self.tasks[item.id] = asyncio.create_task(self._run(item))
        return item

    def list(self, active_only: bool = False) -> list[Goal]:
        goals = sorted(self.goals.values(), key=lambda goal: goal.created, reverse=True)
        return [goal for goal in goals if goal.status in ("queued", "running", "needs_input")] if active_only else goals[:10]

    def answer(self, goal_id: str, text: str) -> bool:
        future = self.answers.get(goal_id)
        if not future or future.done():
            return False
        future.set_result(text)
        return True

    def cancel(self, goal_id: str) -> bool:
        task = self.tasks.get(goal_id)
        goal = self.goals.get(goal_id)
        if not goal or goal.status not in ("queued", "running", "needs_input"):
            return False
        if task:
            task.cancel()
        self._update(goal, "cancelled", status="cancelled", question="")
        return True

    # --- running one goal ---------------------------------------------------------------

    async def _run(self, goal: Goal) -> None:
        async with self.slots:  # at most max_parallel goal agents at once; the rest wait as "queued"
            if goal.status == "cancelled":
                return
            try:
                await asyncio.to_thread(self.make_worktree, goal)
            except Exception as error:
                self._update(goal, "failed", status="failed", summary=f"작업 공간을 만들지 못했어요: {error}"[:200])
                return
            self._update(goal, "started", status="running")
            client = self.client_factory(self.options_factory(goal, self._permission(goal), self._ask_tool(goal)))
            text = ""
            try:
                await client.connect()
                await client.query(GOAL_PROMPT.format(goal=goal.goal, done_when=goal.done_when,
                                                      worktree=goal.worktree, branch=goal.branch))
                async for message in client.receive_response():
                    kind = type(message).__name__
                    if kind == "AssistantMessage":
                        for block in message.content:
                            name = type(block).__name__
                            if name == "TextBlock":
                                text = block.text
                            elif name == "ToolUseBlock" and block.name not in ("TodoWrite", "ToolSearch"):
                                step = describe_step(block.name, block.input or {})
                                self._update(goal, "step", steps=(goal.steps + [step])[-20:])
                    elif kind == "ResultMessage":
                        goal.cost_usd = getattr(message, "total_cost_usd", None)
                        if getattr(message, "is_error", False):
                            raise RuntimeError(getattr(message, "result", "") or "agent error")
                self._update(goal, "done", status="done", question="", summary=re.sub(r"\s+", " ", text).strip()[:600])
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("goal %s failed", goal.id)
                self._update(goal, "failed", status="failed", summary=f"실패했어요: {error}"[:300])
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    def _permission(self, goal: Goal):
        async def can_use_tool(tool: str, tool_input: dict, context: Any = None):
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

            action, reason = goal_permission(goal, tool, tool_input)
            logger.info("goal %s tool %s -> %s", goal.id, tool, action)
            return PermissionResultAllow() if action == "allow" else PermissionResultDeny(message=reason)
        return can_use_tool

    def _ask_tool(self, goal: Goal):
        """The ask_user tool: pause this goal until the user answers through the secretary."""
        async def ask(question: str) -> str:
            future: asyncio.Future = asyncio.get_running_loop().create_future()
            self.answers[goal.id] = future
            self._update(goal, "needs_input", status="needs_input", question=question.strip()[:300])
            try:
                answer = await asyncio.wait_for(future, 3600)
            except asyncio.TimeoutError:
                answer = "(사용자가 한 시간 동안 답하지 않았다. 가장 안전한 선택으로 진행하거나 멈추고 보고하라.)"
            finally:
                self.answers.pop(goal.id, None)
            self._update(goal, "step", status="running", question="", steps=(goal.steps + [f"답을 받음: {answer[:40]}"])[-20:])
            return answer
        return ask

    def _options(self, goal: Goal, can_use_tool, ask):
        from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

        @tool("ask_user", "Ask the user one short question (they answer by voice through the secretary). Waits for the answer.",
              {"question": str})
        async def ask_user(args: dict) -> dict:
            return {"content": [{"type": "text", "text": await ask(str(args.get("question", "")))}]}

        git_dir = subprocess.run(["git", "-C", goal.repo, "rev-parse", "--git-common-dir"], capture_output=True,
                                 text=True).stdout.strip()
        git_dir = git_dir if os.path.isabs(git_dir) else os.path.join(goal.repo, git_dir)
        return ClaudeAgentOptions(
            model=self.model,
            cwd=goal.worktree,
            add_dirs=[git_dir],  # commits write into the main repository's .git
            setting_sources=[],
            can_use_tool=can_use_tool,
            mcp_servers={"goal": create_sdk_mcp_server("goal", tools=[ask_user])},
            sandbox={"enabled": True, "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False,
                     "network": {"allowedDomains": PACKAGE_REGISTRIES}},
            system_prompt={"type": "preset", "preset": "claude_code"},
            max_turns=200,
        )

    @staticmethod
    def _make_worktree(goal: Goal) -> None:
        repo = Path(goal.repo)
        if not (repo / ".git").exists():
            raise RuntimeError(f"{repo.name} is not a git repository")
        base = repo.parent / ".baryon-goals"
        base.mkdir(exist_ok=True)
        goal.worktree = str(base / f"{repo.name}-{goal.id}")
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", goal.branch, goal.worktree, "HEAD"],
                       check=True, capture_output=True, text=True)


def describe_step(tool: str, tool_input: dict) -> str:
    """A short Korean line for the app's progress view."""
    if tool in ("Edit", "Write", "MultiEdit"):
        return f"{Path(str(tool_input.get('file_path', ''))).name} 수정"
    if tool == "Read":
        return f"{Path(str(tool_input.get('file_path', ''))).name} 읽기"
    if tool in ("Grep", "Glob"):
        return "코드 찾기"
    if tool == "Bash":
        command = str(tool_input.get("description") or tool_input.get("command", ""))
        return ("명령: " + command)[:60]
    if tool.endswith("__ask_user"):
        return "사용자에게 질문"
    return tool.rsplit("__", 1)[-1]
