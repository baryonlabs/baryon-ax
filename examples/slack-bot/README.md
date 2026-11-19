# Slack @멘션 봇 — 근거 + 답변

baryon-ax에 올린 사내 문서를 근거로, Slack에서 `@baryon 질문`에 답하는 확장 예제입니다.
동작 방식은 [CopilotKit/OpenTag](https://github.com/CopilotKit/OpenTag)의 멘션 게이트웨이 패턴을 따르되,
호스팅 서비스 없이 **Socket Mode + baryon-ax API**만으로 동작합니다(공인 IP·터널 불필요).

```
Slack ──(Socket Mode, outbound WS)── baryon_slack_bot.py ──HTTP── baryon-ax API (/v1/chat/completions, /v1/documents/search)
```

## 동작

| 상황 | 봇 동작 |
|---|---|
| 채널에서 `@baryon 질문` | 원문에 👀 → 스레드에 "준비 중" 메시지 → 답변 + **근거**(문서명·청크·인용문) + trace id로 교체 → ✅ |
| 그 스레드에서 멘션 없이 후속 질문 | 구독된 스레드라 바로 답변(OpenTag 방식). 스레드 전체를 대화 이력으로 보내 "그럼 반차는?" 같은 질문도 이어짐 |
| 다른 스레드·채널 일반 메시지 | 무시 |
| 근거 문서 없음 | "근거 문서를 찾지 못했습니다" 경고 표시 |
| API 오류 | 준비 중 메시지를 오류 안내로 바꾸고 ❌ |

사용 예시 (예시 데이터로 그린 화면이며, 실제 스크린샷이 아닙니다):

![Slack 스레드에서 @baryon 멘션 질문에 답변과 근거가 달리고, 멘션 없는 후속 질문에도 답하는 예시 화면](docs/slack-thread-example.svg)

- Slack 사용자마다 `X-User-Id: slack:<USER_ID>` 로 요청하므로 감사 로그·지식 공백 기록이 사람별로 남습니다.
- 문서 열람 권한은 `BARYON_ROLES`(기본 `member`)로 걸러집니다. 운영에서 OIDC를 켰다면 이 헤더를 넣어 주는 게이트웨이 뒤에 두거나 `BARYON_API_TOKEN`을 쓰세요.
- 스레드 대화는 `thread_id = slack:<채널>:<스레드 ts>` 로 묶여 baryon-ax 메모리·트레이스와 연결됩니다.

## AI 에이전트로 설치하기 (프롬프트)

Claude Code 같은 코딩 에이전트에게 아래 프롬프트를 그대로 붙여 넣으면 설치부터 Slack 연동 확인까지 진행합니다.
`< >` 부분만 환경에 맞게 바꾸세요. 토큰 발급·앱 설치 승인처럼 계정 권한이 필요한 단계는 에이전트가 사람에게 요청합니다.

````text
baryon-ax 문서 QA를 Slack @멘션 봇으로 연결하려고 한다. 저장소: <baryon-ax 경로>

목표: Slack에서 "@baryon 질문"을 하면 스레드에 답변과 근거(문서·청크·인용문)가 달리고,
같은 스레드에서는 멘션 없이 후속 질문에도 답하게 한다.

1. baryon-ax API 준비
   - API가 <http://127.0.0.1:8000> 에서 동작하는지 /health 로 확인한다. 없으면 README의 Quick start대로 띄운다.
   - LLM은 OpenAI 호환 엔드포인트 <MODEL_BASE_URL>, 모델 <MODEL_NAME> 을 .env에 설정한다.
   - 한국어 문서면 QDRANT_URL을 켜고 RETRIEVAL_MODE=hybrid,
     EMBEDDING_MODEL=intfloat/multilingual-e5-large, EMBEDDING_DIMENSION=1024,
     새 QDRANT_COLLECTION 이름과 QDRANT_REINDEX_ON_START=true(첫 실행만)로 설정한다.
2. 문서 등록
   - <문서 폴더>의 파일을 POST /v1/documents (multipart file, allowed_roles=member,admin) 로 올린다.
   - 파일명은 UTF-8로 보낸다(Windows에서는 curl 대신 Python으로).
   - 정답을 아는 질문 5~10개로 /v1/chat/completions 를 호출해 답과 citations를 확인하고 결과를 표로 보여 준다.
3. Slack 앱
   - examples/slack-bot/manifest.yml 로 앱을 만들거나, 기존 앱 <앱 이름>의 App Manifest에
     같은 bot 권한·event_subscriptions·socket_mode_enabled를 추가한다.
   - App-Level Token(connections:write)을 만들고 앱을 (재)설치한다. 이 두 단계는 나에게 승인을 받는다.
   - 봇을 <채널>에 초대한다.
4. 봇 실행
   - examples/slack-bot 에 venv를 만들고 requirements.txt 를 설치한다.
   - .env.example 을 .env 로 복사해 토큰과 BARYON_API_URL, BARYON_ORGANIZATION_ID, BARYON_ROLES 를 채운다.
   - python -m pytest -q tests 가 통과하는지 확인한 뒤 python baryon_slack_bot.py 를 백그라운드로 실행한다.
5. 연동 확인 (<채널>에서)
   - "@봇 <정답을 아는 질문>" → 👀, 준비 중 메시지, 답변 + 근거 + trace id, ✅ 순서인지 본다.
   - 같은 스레드에서 멘션과 함께 후속 질문 → 앞 대화를 이어받는지 본다.
   - 같은 스레드에서 멘션 없이 질문 → 구독 스레드로 답하는지 본다.
6. 정리
   - 토큰이 든 .env, 조직 전용 설정, 평가 질문과 결과는 저장소 루트 local/ 에 두고 커밋하지 않는다
     (.gitignore에 local/, .env.* 가 있다).
   - 공개 저장소에 올리는 변경에는 조직·과제 이름이나 내부 주소가 들어가지 않게 확인한다.
   - 무엇을 설정했고 어디서 확인했는지, 남은 일을 한국어로 보고한다.
````

## 1. Slack 앱 만들기

1. <https://api.slack.com/apps> → **Create New App → From a manifest** → 워크스페이스 선택 → [`manifest.yml`](manifest.yml) 붙여넣기 → Create.
2. **Basic Information → App-Level Tokens → Generate**: 범위 `connections:write` → `xapp-…` 토큰 = `SLACK_APP_TOKEN`.
3. **Install App → Install to Workspace** → `xoxb-…` 토큰 = `SLACK_BOT_TOKEN`.
4. 쓸 채널에서 `/invite @baryon` (초대하지 않은 채널에서는 Slack이 멘션 이벤트를 보내지 않습니다).

**이미 쓰는 Slack 앱을 재사용**해도 됩니다. 앱 설정의 **App Manifest**에서 위 `bot` 범위와 `event_subscriptions`를
추가하고 `socket_mode_enabled: true`로 저장한 뒤, App-Level Token을 만들고 **Reinstall to Workspace** 하면
기존 `xoxb-` 토큰이 새 범위를 갖게 됩니다.

권한: `app_mentions:read`, `chat:write`는 필수입니다. `channels:history`/`groups:history`가 없으면 스레드 맥락과
멘션 없는 후속 질문만 꺼지고, 멘션 질문은 그대로 동작합니다. `reactions:write`가 없으면 반응만 생략됩니다.

## 2. 실행

```bash
cd examples/slack-bot
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # 토큰과 BARYON_API_URL 입력
python baryon_slack_bot.py
```

`baryon-ax` API가 먼저 떠 있어야 합니다(`docker compose up` 또는 `uvicorn baryon.main:app`).
검색 품질은 API 쪽 설정을 그대로 따릅니다. 한국어 문서라면 `RETRIEVAL_MODE=hybrid`,
`EMBEDDING_MODEL=intfloat/multilingual-e5-large`(`EMBEDDING_DIMENSION=1024`)를 권장합니다.

## 3. 설정

| 변수 | 기본값 | 설명 |
|---|---|---|
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | — | 위 1단계 토큰 |
| `BARYON_API_URL` | `http://127.0.0.1:8000` | baryon-ax API |
| `BARYON_ORGANIZATION_ID` | `local-org` | 문서가 속한 조직 |
| `BARYON_ROLES` | `member` | Slack 사용자에게 줄 역할(문서 권한 필터) |
| `BARYON_MODEL` | 서버 기본값 | 모델 지정 |
| `SLACK_SUBSCRIBE_THREADS` | `true` | 멘션된 스레드의 후속 질문 자동 응답 |
| `SLACK_THREAD_TURNS` | `10` | 이력으로 보낼 스레드 메시지 수 |
| `SLACK_MAX_SOURCES` | `4` | 표시할 근거 수 |
| `SLACK_SHOW_SNIPPETS` | `true` | 근거 아래 인용문 표시 |

## 4. 테스트

```bash
pip install pytest && python -m pytest -q tests
```

Slack·API 없이 가짜 WebClient와 `httpx.MockTransport`로 멘션 → 준비 중 → 답변/근거 교체, 스레드 이력,
권한 없는 경우의 대체 동작, 구독 스레드 판별, API 오류 처리를 검증합니다.

## 확장 아이디어

- 답변 아래 👍/👎 버튼 → baryon-ax 피드백·지식 공백 API로 연결
- 채널별로 `BARYON_ROLES`/조직을 다르게 매핑(예: 경영지원 채널만 인사 문서 열람)
- 파일 공유 이벤트(`file_shared`)를 받아 `/v1/documents`로 바로 색인

## 조직별 설정과 활용사례

토큰이 담긴 `.env`와 특정 조직·과제의 활용 기록(평가 질문, 채널, 앱 ID 등)은 저장소 루트의 `local/`에 두세요.
`local/`과 `.env.*`(예제 파일 제외)는 `.gitignore`에 들어 있어 커밋되지 않습니다.
