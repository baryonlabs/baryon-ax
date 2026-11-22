# Voice secretary

A hands-free, eyes-free assistant you talk to. It explains things in a few spoken
sentences and takes spoken commands. The goal is that you can direct work, coding
included, while driving.

It is built on the [live conversation](voice.md#live-conversation) pipeline:
on-prem speech-to-text, sentence-by-sentence speech synthesis and barge-in. The
document Q&A model is replaced by an agent session that has your tools.

```
🎙 you ─▶ STT ─▶ Baryon AX API /v1/assistant (owner only, audited by metadata)
                       │
                       ▼
           apps/secretary on your workstation
           one long-lived Claude Agent SDK session per conversation
           ├─ runs without asking:      PR/CI status, git history, mail and calendar search,
           │                            to-dos, DureClaw fleet presence and results, knowledge base
           ├─ asks "…할까요?" first:     DureClaw coding tasks, browser tasks, new to-dos,
           │                            sending mail, calendar changes, file edits, other commands
           └─ never runs:               rm -rf, force push, reset --hard, curl | sh, …
                       │
                       ▼
   spoken back: a filler while tools run ("깃허브를 확인하고 있어요"), then the answer
```

## What it does

| Say | What happens |
| --- | --- |
| "열린 PR이랑 CI 상태 알려줘" | Runs `gh pr list` and `gh run list` and says the outcome in two or three sentences. |
| "우선순위 높은 할 일 세 개" | Reads the to-do list and names the top three, then asks "더 들으실래요?". |
| "오늘 메일 중에 답해야 할 거 있어?" | Searches mail and summarizes without reading whole messages aloud. |
| "플릿에 누가 붙어 있어?" | Calls DureClaw `get_presence` and says which nodes can code. |
| "로그인 버그 고치라고 빌더한테 맡겨" | Picks a coding node and asks "builder@…에게 이 작업을 보낼까요?". After you say "응", it dispatches a task with the repository, goal and done criteria. |
| "그 작업 결과 어때?" | Reads the DureClaw mailbox and reports the result. |
| "아이디어인데, …" | Restates the idea briefly and offers to save it as a to-do. |
| "멈춰" / start speaking | Stops talking and cancels the current step. |

## Goal agents: parallel background work

The secretary is the **resident agent**: it stays up between calls and keeps the list of
goals. Long work goes to **goal agents**, which work on their own while the conversation goes on:

```
"로그인 버그 고쳐서 테스트 통과시켜 줘" → start_goal (spoken yes) → goal agent #1 ─┐
"문서 오타도 정리해 줘"               → start_goal               → goal agent #2 ─┤ in parallel (SECRETARY_MAX_GOALS, default 3)
"지금 뭐 하고 있어?"                  → list_goals                                 │
                       ◀── "목표 '로그인 버그' 작업이 끝났어요. …" (spoken as soon as the line is free)
```

- **Isolated branches:** each goal gets its own git worktree and branch (`goal/<id>-<slug>`), next to the repository in `.baryon-goals/`. Parallel agents never touch each other's files or your checkout.
- **Goal-driven:** the agent gets the goal and the "done when" criteria. It plans, makes changes, checks them against the criteria and commits to its branch. Its last message is a 2–3 sentence spoken report.
- **Confined:** shell commands run in the Claude Code sandbox. Writes stay in the worktree, and network access is limited to package registries. File edits outside the worktree are refused. A goal agent never pushes, opens a PR or sends anything. When the report says "push 필요", ask the secretary, which publishes with a spoken confirmation as usual.
- **Questions:** when a goal agent is stuck it calls `ask_user` and its status becomes `needs_input`. The secretary asks you as soon as the line is free, and your answer goes back through `answer_goal`.
- **Resident:** goals are stored in `SECRETARY_STATE_DIR` (default `~/.baryon-secretary/goals.json`). After a restart, unfinished goals show as `interrupted`, and their branches and worktrees are kept. `GET /v1/goals` lists them, and the app shows each one as a background task card.

A real run started two goals on a test repository at once ("fix add", "make average handle an empty list"):

- Both finished in about 25 s, for about $0.24 each.
- Each fixed the right function and committed on its own branch. The main checkout was unchanged.
- Neither could run pytest in the sandbox: pytest was not installed, and the attempt to install it failed. Both said so in their reports and checked the test functions directly instead.

## What it knows about you: profile, memory, connections

`secretary/profile.py` keeps a profile in `SECRETARY_STATE_DIR/profile.json` (mode 600, on the workstation):

- `about` (나에 대해) and `usage` (이렇게 써 줘): written by the owner in the app, added to the secretary's instructions.
- `memories`: short facts. The owner adds them in the app, or by voice ("이거 기억해 줘" → the `remember` tool). The 60 newest go into the instructions with their ids, so "그건 잊어" can delete one. The memory tools run without a confirmation, because they only touch this file.
- `mcp`: which MCP servers from `~/.claude.json` the secretary loads. It starts as `SECRETARY_MCP`, and then the app owns it. The API returns names and kinds only, never commands, URLs or keys.

API (same bearer key): `GET/PUT /v1/profile`, `POST /v1/memories`, `DELETE /v1/memories/{id}`. A running conversation keeps its tools and instructions; changes apply from the next call. The phone app reaches this through the gateway's settings channel ([voice-app.md](voice-app.md#protocol)).

## Confirmations

Confirmations are enforced in code, not left to the model. Every tool call
passes `secretary/policy.py`.

- **Read-only lookups** run at once. A shell command counts as read-only only when every part of a sequence such as `a; b && c` starts with a known lookup (`gh pr list`, `git log`, …). The only pipes allowed go into filters such as `jq`, `head` and `grep`. Command substitution and redirects are refused.
- **Anything else** pauses the agent. The secretary says one short question, for example "builder@mini에게 이 작업을 보낼까요? 로그인 버그…".
  - "응", "네", "보내줘" or "진행해" lets it run.
  - "아니" or "취소" refuses it.
  - Any other reply refuses it and is treated as your next request.
  - No answer within two minutes counts as "no".
- **Destructive commands** are refused without asking, so a misheard word can never trigger them.

## Setup

The secretary runs where your Claude Code sign-in and MCP connections live, usually
your own workstation or Mac mini. It never runs in the shared server stack.

```bash
cd apps/secretary
uv venv && uv pip install -r requirements.txt
SECRETARY_API_KEY=$(openssl rand -hex 24) \
SECRETARY_CWD=~/code/your-repo \
SECRETARY_MCP=dureclaw,baryon-todo \
uv run uvicorn secretary.server:app --host 127.0.0.1 --port 8765
```

Then point the Baryon AX API at it. Set these in the API's `.env`:

```
SECRETARY_URL=http://<workstation>:8765     # reach it over your tailnet or VPN, never the internet
SECRETARY_API_KEY=<the same key>
SECRETARY_OWNERS=<your Baryon AX user id>    # only these users can talk to it
```

In the live conversation panel, choose **비서**. The choice only appears for owners.
You also need [voice](voice.md) with a dictation model and Supertonic 3 installed.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SECRETARY_MODEL` | `sonnet` | Claude model alias for the agent. |
| `SECRETARY_MCP` | empty | MCP servers to import, by name, from `~/.claude.json`. Credentials stay there. |
| `SECRETARY_CWD` | current directory | Working directory, for example the repository you usually ask about. |
| `SECRETARY_MAX_SESSIONS` | `2` | Number of conversations at once. |
| `SECRETARY_IDLE_SECONDS` | `900` | Idle conversations are closed after this long. |
| `SECRETARY_CONFIRM_TIMEOUT` | `120` | A question unanswered for this long counts as "no". |
| `SECRETARY_MAX_GOALS` | `3` | Goal agents running at once. Others wait as `queued`. |
| `SECRETARY_STATE_DIR` | `~/.baryon-secretary` | Where the goal list is kept. |

Mail, calendar and drive come from the Claude account's connectors when they are
enabled there.

## Measured

The setup was sonnet with dureclaw and baryon-todo, on an Apple M-series workstation.

- Starting a conversation: under 1 s, because the agent process stays up for the whole conversation.
- First spoken audio: 2.3–2.4 s after you stop speaking. This is a short filler ("깃허브를 확인하고 있어요"); the answer follows a few seconds later.
- Cost: about $0.02–0.05 per turn. The session uses `setting_sources=[]`, which skips user hooks, plugins and CLAUDE.md. Loading them cost about $0.10 per turn and five seconds at startup.

## Privacy and audit

- The API audits `assistant.session_started`, `assistant.turn` (character count and how the turn was used) and `assistant.confirmation` (tool name and yes or no).
- What you said and what the secretary answered are not logged.
- Only `SECRETARY_OWNERS` can start a conversation. A session can be used only by the user who started it; even an admin cannot use someone else's.
- Audio is transcribed and synthesized on-prem as described in [voice.md](voice.md). The agent's reasoning uses the Claude model you configured.

## Phone call mode

To talk to the secretary while driving, call it. See [phone.md](phone.md).

## Tests

```bash
cd apps/secretary && uv run --no-project --with-requirements requirements.txt --with pytest --with httpx python -m pytest -q
cd apps/web && E2E_BASE_URL=… SECRETARY_SUMMARY_WAV=… SECRETARY_CONFIRM_WAV=… node e2e/secretary.mjs
```
