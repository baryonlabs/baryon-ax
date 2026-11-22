"""Goal agents: parallel background work started from a conversation (fake agents, no Claude CLI)."""

from __future__ import annotations

import asyncio

import pytest

from secretary import goals as goals_module
from secretary.goals import Goal, GoalManager, goal_permission
from secretary.session import Session


class TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class ToolUseBlock:
    def __init__(self, name: str, tool_input: dict) -> None:
        self.name, self.input, self.id = name, tool_input, f"tu-{name}"


class AssistantMessage:
    def __init__(self, *blocks) -> None:
        self.content = list(blocks)


class ResultMessage:
    total_cost_usd = 0.05
    is_error = False
    result = ""


class FakeGoalAgent:
    """Works for `delay` seconds per step; a goal containing ASK-FIRST asks the user first."""

    running = 0
    peak = 0
    delay = 0.05

    def __init__(self, options: dict) -> None:
        self.options = options
        self.prompt = ""

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def receive_response(self):
        FakeGoalAgent.running += 1
        FakeGoalAgent.peak = max(FakeGoalAgent.peak, FakeGoalAgent.running)
        try:
            yield AssistantMessage(ToolUseBlock("Edit", {"file_path": "/w/app/login.py"}))
            await asyncio.sleep(self.delay)
            answer = ""
            if "ASK-FIRST" in self.prompt:
                answer = await self.options["ask"]("세션 만료 시간을 몇 분으로 할까요?")
            yield AssistantMessage(ToolUseBlock("Bash", {"command": "pytest -q", "description": "테스트 실행"}))
            await asyncio.sleep(self.delay)
            yield AssistantMessage(TextBlock(f"로그인 버그를 고쳤고 테스트 12개가 통과했어요.{' 답: ' + answer if answer else ''}"))
            yield ResultMessage()
        finally:
            FakeGoalAgent.running -= 1


def manager(tmp_path, events: list, **kwargs) -> GoalManager:
    def make_worktree(goal: Goal) -> None:
        goal.worktree = str(tmp_path / f"wt-{goal.id}")

    return GoalManager(tmp_path / "state", FakeGoalAgent, lambda goal, event: events.append((goal.id, event, goal.status)),
                       options_factory=lambda goal, can_use_tool, ask: {"can_use_tool": can_use_tool, "ask": ask},
                       make_worktree=make_worktree, **kwargs)


async def wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


def test_goals_run_in_parallel_up_to_the_limit_and_report(tmp_path) -> None:
    async def run() -> None:
        FakeGoalAgent.running = FakeGoalAgent.peak = 0
        events: list = []
        goals = manager(tmp_path, events, max_parallel=2)
        started = [goals.start(f"작업 {n}", f"버그 {n} 고치기", "테스트 통과", str(tmp_path)) for n in range(3)]
        assert all(goal.branch.startswith(f"goal/{goal.id}-") for goal in started)
        await wait_until(lambda: all(goal.status == "done" for goal in started))
        assert FakeGoalAgent.peak == 2  # never more than max_parallel at once
        assert "테스트 12개가 통과" in started[0].summary
        assert started[0].steps == ["login.py 수정", "명령: 테스트 실행"]
        assert sum(1 for _, event, _ in events if event == "done") == 3
    asyncio.run(run())


def test_goal_question_waits_for_the_users_answer(tmp_path) -> None:
    async def run() -> None:
        events: list = []
        goals = manager(tmp_path, events)
        goal = goals.start("세션 만료", "로그인 세션 만료 버그 고치기 ASK-FIRST", "", str(tmp_path))
        await wait_until(lambda: goal.status == "needs_input")
        assert goal.question == "세션 만료 시간을 몇 분으로 할까요?"
        assert not goals.answer("nope", "30분")
        assert goals.answer(goal.id, "30분")
        await wait_until(lambda: goal.status == "done")
        assert "답: 30분" in goal.summary and goal.question == ""
    asyncio.run(run())


def test_cancel_and_restart_keep_an_honest_list(tmp_path) -> None:
    async def run() -> None:
        FakeGoalAgent.delay = 0.5
        try:
            events: list = []
            goals = manager(tmp_path, events)
            first = goals.start("오래 걸리는 일", "리팩터링", "", str(tmp_path))
            second = goals.start("다른 일", "문서 정리", "", str(tmp_path))
            await wait_until(lambda: first.status == "running")
            assert goals.cancel(first.id) and first.status == "cancelled"
            assert not goals.cancel(first.id)
            # the secretary restarts while the second goal is still running
            reloaded = manager(tmp_path, [])
            assert reloaded.goals[first.id].status == "cancelled"
            assert reloaded.goals[second.id].status == "interrupted"
            goals.cancel(second.id)
        finally:
            FakeGoalAgent.delay = 0.05
    asyncio.run(run())


@pytest.mark.parametrize("tool,tool_input,action", [
    ("Edit", {"file_path": "/w/goal/app.py"}, "allow"),
    ("Edit", {"file_path": "app.py"}, "allow"),                       # relative: inside the worktree
    ("Write", {"file_path": "/w/goal/../other/app.py"}, "deny"),      # escapes the worktree
    ("Write", {"file_path": "/Users/me/.zshrc"}, "deny"),
    ("Bash", {"command": "pytest -q"}, "allow"),
    ("Bash", {"command": "git commit -am 'fix login'"}, "allow"),
    ("Bash", {"command": "git push origin goal/abc"}, "deny"),        # leaving the machine is the secretary's job
    ("Bash", {"command": "gh pr create --fill"}, "deny"),
    ("Bash", {"command": "curl -X POST https://example.com/hook"}, "deny"),
    ("Bash", {"command": "rm -rf /"}, "deny"),
    ("mcp__goal__ask_user", {"question": "?"}, "allow"),
    ("mcp__claude_ai_Gmail__send_message", {}, "deny"),
])
def test_goal_agent_permissions(tool: str, tool_input: dict, action: str) -> None:
    goal = Goal(id="g1", title="t", goal="g", done_when="d", repo="/w/repo", worktree="/w/goal")
    assert goal_permission(goal, tool, tool_input)[0] == action


# --- the resident secretary announces background results ----------------------------------

class QuietClient:
    """A secretary agent that echoes each prompt; slow when asked to be."""

    def __init__(self, options) -> None:
        self.prompts: list[str] = []
        self._current = ""

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    async def query(self, text: str) -> None:
        self.prompts.append(text)
        self._current = text

    async def receive_response(self):
        if "천천히" in self._current:
            await asyncio.sleep(0.2)
        yield ResultMessage()


def test_notice_is_spoken_when_the_line_is_free_not_over_the_caller() -> None:
    async def run() -> None:
        session = Session(QuietClient, owner="me", cwd=".", model="t", mcp_names=[],
                          options_factory=lambda can_use_tool, **_: {"can_use_tool": can_use_tool})
        await session.start()
        await session.turn("천천히 설명해")
        await asyncio.sleep(0.02)
        session.notify("목표 '로그인 버그' 작업이 끝났어요.")
        assert len(session._client.prompts) == 1  # not while the caller's turn runs
        await wait_until(lambda: len(session._client.prompts) == 2)
        assert session._client.prompts[1].startswith("[알림") and "로그인 버그" in session._client.prompts[1]
        session.notify("목표 '문서' 에이전트가 물어봐요: 어디에 둘까요?")  # idle now: spoken right away
        await wait_until(lambda: len(session._client.prompts) == 3)
    asyncio.run(run())


def test_slug_and_steps() -> None:
    assert goals_module.slug("Fix login timeout!") == "fix-login-timeout"
    assert goals_module.slug("로그인 버그") == "work"
    assert goals_module.describe_step("Bash", {"command": "npm test"}) == "명령: npm test"
