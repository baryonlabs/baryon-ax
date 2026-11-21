# Discord @멘션 봇 — 근거 + 답변, 그리고 먼저 묻기

[`examples/slack-bot`](../slack-bot)의 Discord판입니다. baryon-ax에 올린 문서를 근거로 `@baryon 질문`에 답하고,
답하지 못한 질문은 사람에게 먼저 물어 문서로 만듭니다. **Gateway(밖으로 나가는 WebSocket)** 만 쓰므로
공인 IP·터널·Interactions Endpoint URL이 필요 없습니다.

```
Discord ──(Gateway, outbound WS)── baryon_discord_bot.py ──HTTP── baryon-ax API
```

## 동작

| 상황 | 봇 동작 |
|---|---|
| 채널에서 `@baryon 질문` | 그 메시지에 스레드를 열고 👀 → "준비 중" → 답변 + **근거** 임베드(문서명·청크·인용문, trace id) → ✅ |
| 그 스레드에서 멘션 없이 후속 질문 | 스레드 전체(첫 질문 포함)를 대화 이력으로 보내 이어서 답변 |
| 근거 없음 / API 오류 | "근거 문서를 찾지 못했습니다" 경고 / 오류 안내와 ❌ |
| **먼저 묻기 모드** (`PROACTIVE_CHANNEL`) | 답하지 못한 질문을 데스크 채널에 올리고 스레드를 엶 → 스레드 답글은 작성자 이름으로 초안 저장 → [문서로 등록] [취소] → 등록하면 색인·공백 해결, 원래 질문 스레드에 한 번 다시 답변. [해당 없음]은 공백 닫기 |

- Discord 사용자마다 `X-User-Id: discord:<USER_ID>`로 요청하므로 감사 로그·지식 공백이 사람별로 남습니다.
- 스레드 대화는 `thread_id = discord:<채널>:<스레드>`로 묶여 baryon-ax 메모리·트레이스와 연결됩니다.
- 등록 버튼은 답을 쓴 사람만 누를 수 있습니다(baryon-ax 암묵지 API가 작성자를 확인). 버튼은 재시작 후에도 동작합니다.

## 1. Discord 앱 만들기

1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** → **Reset Token** → 토큰 = `DISCORD_TOKEN`.
   같은 화면 **Privileged Gateway Intents**에서 **Message Content Intent**를 켭니다
   (멘션 없는 후속 질문과 데스크 답글을 읽는 데 필요. 멘션된 메시지는 이 설정 없이도 읽힘).
3. **OAuth2 → URL Generator**: Scopes `bot`, 권한은 아래를 고르거나 이 URL을 씁니다(`<APP_ID>`만 바꿈).
   `https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot&permissions=309237730368`
   - View Channels, Send Messages, Send Messages in Threads, Create Public Threads,
     Read Message History, Add Reactions, Embed Links
4. 서버에 초대한 뒤, 먼저 묻기 모드를 쓰려면 **사용자 설정 → 고급 → 개발자 모드**를 켜고
   데스크 채널을 우클릭 → **채널 ID 복사** → `PROACTIVE_CHANNEL`.

## 2. 실행

```bash
cd examples/discord-bot
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # DISCORD_TOKEN과 BARYON_API_URL 입력
python baryon_discord_bot.py
```

baryon-ax API가 먼저 떠 있어야 합니다. 검색 품질 설정(하이브리드 검색, 임베딩)은 API 쪽을 따릅니다.

## 3. 설정

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DISCORD_TOKEN` | — | 봇 토큰 |
| `BARYON_API_URL` / `BARYON_ORGANIZATION_ID` / `BARYON_ROLES` | `http://127.0.0.1:8000` / `local-org` / `member` | baryon-ax API와 Discord 사용자에게 줄 역할 |
| `DISCORD_SUBSCRIBE_THREADS` | `true` | 봇이 연 스레드의 후속 질문에 멘션 없이 답변 |
| `DISCORD_THREAD_TURNS` / `DISCORD_MAX_SOURCES` / `DISCORD_SHOW_SNIPPETS` | `10` / `4` / `true` | 이력 길이, 근거 수, 인용문 표시 |
| `PROACTIVE_CHANNEL` | — | 데스크 채널 ID. 비우면 먼저 묻기 모드 꺼짐 |
| `PROACTIVE_INTERVAL_MINUTES` / `PROACTIVE_HOURS` / `PROACTIVE_WEEKDAYS_ONLY` | `30` / — / `false` | 확인 주기와 시간대 (예: `9-18`, 평일만) |
| `PROACTIVE_MAX_PER_RUN` / `PROACTIVE_MIN_OCCURRENCES` | `3` / `1` | 한 번에 올릴 질문 수(자주 나온 것부터), 최소 등장 횟수 |
| `PROACTIVE_DISCORD_ONLY` | `false` | Discord에서 나온 질문만 묻기 |
| `PROACTIVE_MENTION` | — | 질문 앞 멘션. 예: `@here`, `<@&ROLE_ID>` |
| `PROACTIVE_REANSWER` | `true` | 등록 후 원래 스레드에 한 번 다시 답하기 |
| `PROACTIVE_STATE_PATH` | `.data/proactive_state.json` | 물어본 기록 (재시작해도 다시 묻지 않음) |

## 4. 테스트

```bash
pip install pytest && python -m pytest -q tests
```

Discord 접속 없이 가짜 채널·스레드·메시지 객체와 `httpx.MockTransport`로 멘션 → 스레드 → 답변/근거,
스레드 이력(첫 질문 포함), 오류 처리, 데스크 질문·초안·등록·재답변(한 번만)·해당 없음, 작성자만 등록을 검증합니다.

## Slack판과 다른 점

| | Slack | Discord |
|---|---|---|
| 연결 | Socket Mode (`xapp-` 토큰) | Gateway (봇 토큰) |
| 답변 위치 | 질문 메시지의 스레드 | 질문 메시지에서 연 스레드 |
| 근거 | Block Kit 섹션 | 임베드 |
| 버튼 | Block Kit actions (Interactivity 켜기) | Message components (추가 설정 없음) |
| 후속 질문 읽기 | `channels:history` 권한 | Message Content Intent |
