"""What the voice secretary may do on its own, what needs a spoken "yes", and what never runs.

The user is often driving, so the rules are conservative:

* **auto**: read-only lookups (PR/CI status, git history, mail and calendar search,
  to-dos, fleet presence, knowledge base). These run without asking.
* **confirm**: anything that changes something (dispatching a coding task to the
  DureClaw fleet, creating a to-do, sending mail, editing files, other shell
  commands). The secretary asks a short question aloud and waits for "응/네".
* **deny**: destructive commands that a misheard word must never trigger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Read-only tools that run without asking. Bash entries are command prefixes.
AUTO_TOOLS = {
    "Read", "Grep", "Glob", "LS", "WebSearch", "WebFetch", "TodoWrite", "ToolSearch",
}
AUTO_BASH_PREFIXES = (
    # Google mail and calendar through gogcli (`gog`), read-only commands only
    "gog gmail search ", "gog gmail get ", "gog gmail thread get ", "gog gmail url ", "gog gmail labels list",
    "gog calendar events", "gog calendar event ", "gog calendar calendars",
    "gh pr list", "gh pr view", "gh pr checks", "gh pr status", "gh pr diff", "gh run list", "gh run view",
    "gh issue list", "gh issue view", "gh repo view",
    "git status", "git log", "git diff", "git show", "git branch --show-current", "git branch -a", "git branch -v", "git remote -v", "git fetch",
    "date", "ls", "pwd", "which ",
)
# MCP tools are matched by the end of their name (mcp__<server>__<tool>).
AUTO_MCP_SUFFIXES = (
    "__get_presence", "__read_mailbox", "__read_state", "__get_version", "__get_notifications",
    "__list_tasks", "__get_task", "__dashboard_summary", "__list_projects", "__get_task_history", "__list_task_messages",
    "__search_threads", "__get_thread", "__get_message", "__list_labels", "__list_drafts",
    "__list_events", "__search_events", "__get_event", "__list_calendars", "__suggest_time",
    "__search_files", "__read_file_content", "__get_file_metadata", "__list_recent_files",
    "__kb_query", "__kb_documents", "__notion-search", "__notion-fetch",
    "__list_issues", "__get_issue", "__list_comments",
    "__list_goals", "__answer_goal", "__cancel_goal",  # the user's own background agents
    "__memory__remember", "__memory__list_memories", "__memory__forget",  # the secretary's own notes about its owner (profile.py)
)

DENY_BASH = re.compile(
    r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r|--force\b|\bpush\s+-f\b|reset\s+--hard|\bclean\s+-[a-z]*f"
    r"|\bgog\b.*\b(?:batch\s+delete|delete-calendar)\b"  # permanent mail deletion, removing a calendar
    r"|\bmkfs\b|\bdd\s+if=|\bshutdown\b|\breboot\b|:\(\)\s*\{|\bchmod\s+-R\s+777|\bcurl\b[^|]*\|\s*(?:ba|z)?sh",
)
_UNSAFE_SHELL = re.compile(r"[`$<>]|(?<![&])&(?![&])")  # substitution, redirects, background jobs
_SEQUENCE = re.compile(r"\s*(?:;|&&|\|\||\n)\s*")
# Read-only filters a lookup may be piped into (gh ... | jq ... | head).
SAFE_FILTERS = ("jq", "head", "tail", "wc", "grep", "sort", "uniq", "cut", "tr", "column")


@dataclass(frozen=True)
class Decision:
    action: str          # auto | confirm | deny
    reason: str = ""


def _read_only_shell(command: str) -> bool:
    """True when every command in a sequence (a; b && c) is a read-only lookup, optionally piped
    into read-only filters. Quoted arguments (gh --jq '.[] | ...') and stderr redirects are ignored."""
    plain = re.sub(r"'[^']*'|\"[^\"$`]*\"", "''", command)
    plain = re.sub(r"\s2>(?:&1|/dev/null)", "", plain)
    if _UNSAFE_SHELL.search(plain):
        return False
    for step in filter(None, _SEQUENCE.split(plain.strip())):
        if re.fullmatch(r"cd(?:\s+\S+)?", step.strip()):
            continue  # changing directory first ("cd repo && gh pr list") changes nothing
        stages = [stage.strip() for stage in step.split("|")]
        if not stages[0].startswith(AUTO_BASH_PREFIXES):
            return False
        if not all(stage.split(" ", 1)[0] in SAFE_FILTERS for stage in stages[1:]):
            return False
    return True


def decide(tool: str, tool_input: dict) -> Decision:
    if tool == "Bash":
        command = str(tool_input.get("command", "")).strip()
        if DENY_BASH.search(command):
            return Decision("deny", "destructive command")
        if _read_only_shell(command):
            return Decision("auto")
        return Decision("confirm")
    if tool in AUTO_TOOLS:
        return Decision("auto")
    if tool.startswith("mcp__") and tool.endswith(AUTO_MCP_SUFFIXES):
        return Decision("auto")
    return Decision("confirm")


def _short(value: object, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _spoken_steps(value: object) -> str:
    """Make task markers and URLs readable aloud: "[OPEN] https://www.naver.com" -> "열기 naver.com"."""
    text = re.sub(r"\[(?:OPEN|NAV)\]\s*", "열기 ", str(value or ""))
    text = re.sub(r"\[(?:SHELL)\]\s*", "명령 ", text)
    text = re.sub(r"\[[A-Z]+\]\s*", "", text)
    return re.sub(r"https?://(?:www\.)?([^/\s]+)\S*", r"\1", text)


def _flag(command: str, name: str) -> str:
    match = re.search(rf"--{name}[= ](?:'([^']*)'|\"([^\"]*)\"|(\S+))", command)
    return next((group for group in match.groups() if group), "") if match else ""


def _gog_send_prompt(command: str) -> str:
    """Read the mail back before it goes: who gets it, the subject, and how it starts."""
    to = _flag(command, "to") or ("답장 받는 사람들" if "--reply-all" in command else "")
    subject = _flag(command, "subject") or ("같은 스레드 답장" if "--thread-id" in command or "--reply-to" in command else "")
    body = _flag(command, "body")
    parts = [f"받는 사람 {_short(to, 50)}" if to else "", f"제목 {_short(subject, 40)}" if subject else "",
             f"내용 {_short(body, 60)}" if body else ""]
    return "메일을 보낼까요? " + ", ".join(part for part in parts if part)


def confirmation_prompt(tool: str, tool_input: dict) -> str:
    """One short spoken question that says what will happen."""
    name = tool.rsplit("__", 1)[-1]
    if name == "send_task":
        target = tool_input.get("to") or "플릿"
        return f"{target}에게 이 작업을 보낼까요? {_short(_spoken_steps(tool_input.get('instructions')))}"
    if name in {"browser_run", "browser_screenshot"}:
        steps = _spoken_steps(tool_input.get("instructions") or tool_input.get("url"))
        return f"브라우저 노드에서 이 작업을 할까요? {_short(steps)}"
    if name == "start_goal":
        return f"목표 에이전트에게 맡길까요? {_short(tool_input.get('title') or tool_input.get('goal'))}"
    if name in {"create_task", "create_dynamic_task"}:
        return f"할 일을 만들까요? {_short(tool_input.get('title') or tool_input.get('description'))}"
    if name in {"update_task"}:
        return f"할 일을 수정할까요? {_short(tool_input.get('title') or tool_input.get('id'))}"
    if name in {"send_message", "reply", "forward"}:
        return f"메일을 보낼까요? 받는 사람 {_short(tool_input.get('to') or tool_input.get('recipient'), 40)}"
    if name in {"create_draft", "update_draft"}:
        return "메일 초안을 저장할까요?"
    if name in {"create_event", "update_event"}:
        return f"일정을 등록할까요? {_short(tool_input.get('summary') or tool_input.get('title'))}"
    if name in {"delete_event", "trash_message", "trash_thread", "delete_draft"}:
        return "삭제할까요? 되돌리기 어려울 수 있어요."
    if tool == "Bash" and re.search(r"\bgog\s+gmail\s+send\b", str(tool_input.get("command", ""))):
        return _gog_send_prompt(str(tool_input.get("command", "")))
    if tool == "Bash":
        command = re.sub(r"^\s*cd\s+\S+\s*(?:&&|;)\s*", "", str(tool_input.get("command", "")))
        command = re.sub(r"(?:/[\w.~-]+){2,}/?", "", command)  # long paths are noise when read aloud
        return f"이 명령을 실행할까요? {_short(command)}"
    if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        path = str(tool_input.get("file_path", "")).rsplit("/", 1)[-1]
        return f"{path or '파일'} 파일을 수정할까요?"
    label = name.replace("_", " ").replace("-", " ")
    return f"{label} 작업을 실행할까요?"


_YES_WORD = re.compile(r"^(?:응|어|네|예|넹|그래|좋아|좋습니다|진행|진행해|해|해줘|보내|보내줘|실행|실행해|오케이|ok|okay|yes|yeah|sure|go)"
                       r"(?:요|줘|주세요|해요|합시다)?$")
_NO_WORD = re.compile(r"^(?:아니|아니요|아뇨|안돼|하지마|취소|그만|멈춰|됐어|no|nope|cancel|stop)(?:요|해|해요|해줘)?$")


def parse_answer(text: str) -> bool | None:
    """True for a spoken yes ("응", "네 보내줘"), False for no ("아니", "하지 마"), None otherwise."""
    compact = re.sub(r"[.,!?~]+", " ", text.strip().lower())
    compact = re.sub(r"(하지|안)\s+(마|돼)", r"\1\2", compact)  # "하지 마" -> "하지마"
    compact = re.sub(r"(해|보내)\s+(줘|주세요)", r"\1\2", compact)  # "해 줘" -> "해줘"
    words = compact.split()
    if not words or len(words) > 3:
        return None
    if _NO_WORD.match(words[0]):
        return False
    if all(_YES_WORD.match(word) for word in words):
        return True
    return None


STATUS_LABELS = (
    ("send_task", "작업을 보내고 있어요."),
    ("get_presence", "플릿 상태를 보고 있어요."),
    ("read_mailbox", "작업 결과를 확인하고 있어요."),
    ("search_threads", "메일을 확인하고 있어요."),
    ("get_thread", "메일을 읽고 있어요."),
    ("list_events", "일정을 확인하고 있어요."),
    ("search_events", "일정을 확인하고 있어요."),
    ("list_tasks", "할 일을 보고 있어요."),
    ("dashboard_summary", "할 일을 보고 있어요."),
    ("kb_query", "사내 지식을 찾고 있어요."),
    ("WebSearch", "검색하고 있어요."),
)


def status_label(tool: str, tool_input: dict) -> str:
    """A short spoken filler while a tool runs, so the line is never silent for long."""
    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if command.startswith(("gh pr", "gh run")):
            return "깃허브를 확인하고 있어요."
        if command.startswith("git "):
            return "저장소를 보고 있어요."
        if command.startswith("gog gmail send"):
            return "메일을 보내고 있어요."
        if command.startswith("gog gmail"):
            return "메일을 확인하고 있어요."
        if command.startswith("gog calendar"):
            return "일정을 확인하고 있어요."
        return "명령을 실행하고 있어요."
    for key, label in STATUS_LABELS:
        if tool.endswith(key):
            return label
    if tool in {"Read", "Grep", "Glob"}:
        return "코드를 살펴보고 있어요."
    return "확인하고 있어요."
