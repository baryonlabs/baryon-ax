# 전체 e2e와 맥미니 self-hosted runner

## 한 번에 전부 테스트하기

```bash
docker compose up -d --build api web     # API :8000, 웹 :3001
scripts/e2e_all.sh                       # 모든 테스트 묶음 + 실행 중인 스택 대상 라이브 점검
```

`scripts/e2e_all.sh`가 하는 일:

| 묶음 | 내용 |
| --- | --- |
| 단위 테스트 | API, 음성 비서, 전화 게이트웨이, STT 사이드카, 슬랙 봇(비서 브리지 포함), repo-sync, 웹(단위·빌드), 음성 앱(Flutter, 있으면) |
| 라이브 API (`scripts/e2e_live.py`) | 문서 올리기 → 근거 답변, 인사엔 근거 없음, 모르는 질문은 지식 공백, MCP(initialize·도구 목록·검색), 내부 주소 차단, 링크 읽기, 사이트 크롤링, 음성 비서·게이트웨이(있으면) |
| 라이브 웹 | Playwright 스모크·버튼·커넥터 시나리오 |

- 라이브 점검은 일회용 조직(`e2e-<시각>`)에서 돌고 끝나면 만든 문서를 지웁니다. 쓰고 있는 스택에 돌려도 됩니다.
- `E2E_LIVE=0`은 단위 테스트만, `E2E_NETWORK=0`은 공개 웹 페이지 읽기를 건너뜁니다. `E2E_REQUIRE_LIVE=1`이면 스택이 없을 때 실패합니다.
- 결과는 표로 나오고, 각 단계 로그 경로를 알려 줍니다.

## 맥미니에서 CI로 돌리기

GitHub 호스팅 러너는 Apple silicon에서 Compose 스택, Flutter, 웹 e2e를 함께 돌리지 못합니다. 팀 맥미니에 self-hosted runner를
두고 `.github/workflows/e2e-selfhosted.yml`이 위 스크립트를 돌립니다.

```bash
# 맥미니에서 한 번 (gh는 저장소 관리자로 로그인, Docker Desktop 실행 중)
scripts/setup_selfhosted_runner.sh
gh workflow run e2e-selfhosted.yml        # 바로 한 번 돌려 보기
```

- **언제 도나**: main에 머지될 때, 매일 03:30(KST), 손으로 실행할 때.
- **스택 분리**: 별도 Compose 프로젝트(`baryon-e2e`)와 포트(API 18000, 웹 13001)를 써서 같은 맥미니에서 쓰는 스택을 건드리지 않고, 끝나면 내립니다.
- **설치 스크립트**: 공식 runner 릴리스를 받아 릴리스 노트의 SHA-256으로 확인하고, `baryon-e2e` 라벨로 등록한 뒤 launchd 서비스로 띄웁니다(재부팅 후에도 동작). `--remove`로 해제합니다.

### 공개 저장소에서 지켜야 할 것

- self-hosted runner를 쓰는 워크플로에는 **`pull_request`·`pull_request_target` 트리거를 넣지 않습니다.** 넣으면 누구나 포크에서 PR을 열어 맥미니에서 코드를 실행할 수 있습니다. 지금 워크플로는 main 푸시·일정·수동 실행만 받습니다.
- 러너는 개인 키·토큰이 없는 별도 macOS 사용자로 돌리는 것이 좋습니다.
- 체크아웃은 커밋 SHA로 고정했고 자격 증명을 남기지 않습니다(`persist-credentials: false`).
