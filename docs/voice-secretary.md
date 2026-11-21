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

## Next: phone call mode

For driving, the conversation will run as a phone call. A SIP app on the phone
(for example Linphone) is treated by iOS CallKit and Android ConnectionService
as a normal call, so the car's Bluetooth hands-free works. The plan:

1. An Asterisk container connects calls over AudioSocket to a gateway.
2. The gateway runs VAD, STT, this secretary and TTS for each call.
3. The phone reaches Asterisk over the tailnet.

## Tests

```bash
cd apps/secretary && uv run --no-project --with-requirements requirements.txt --with pytest --with httpx python -m pytest -q
cd apps/web && E2E_BASE_URL=… SECRETARY_SUMMARY_WAV=… SECRETARY_CONFIRM_WAV=… node e2e/secretary.mjs
```
