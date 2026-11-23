# repo-sync — 코드 저장소를 baryon-ax 지식으로

"지금 무엇이 구현돼 있나", "그 API는 어디 있나", "최근에 무엇을 고쳤나" 같은 질문은 설계 문서만으로는 답할 수 없습니다.
`repo_sync.py`는 git 저장소에서 세 가지를 baryon-ax 문서로 올리고, 다시 실행하면 바뀐 것만 갱신합니다.

| 종류 | 내용 | 문서 제목 예 |
|---|---|---|
| 문서 | `--include`에 맞는 Markdown·텍스트(기본 `**/*.md`, `**/*.txt`) 원문 | `shop · docs › 설계.md` |
| 코드 지도 | 모듈(최상위 폴더, `--module-depth`)마다 폴더·파일·클래스·함수와 HTTP 엔드포인트 목록 (Java/Kotlin, TS/JS, Python) | `shop · 코드지도 server.md` |
| 변경 이력 | `--ref`의 최근 커밋·머지 리퀘스트 제목(`--history`개) | `shop · 최근 변경 이력.md` |

소스 파일 전체를 올리지 않는 이유: 질문은 자연어인데 코드 조각은 검색 잡음이 많습니다. 코드 지도로 "어디에 무엇이 있는지"를
알려 주고, 자세한 설계는 문서에서 찾게 합니다.

## 실행

```bash
cd examples/repo-sync
pip install httpx
python repo_sync.py --repo ../../../my-service --ref origin/main --fetch --name my-service \
  --api http://127.0.0.1:8000 --state .data/my-service.json
# {"uploaded": 412, "unchanged": 0, "deleted": 0, "failed": [], "failed_count": 0}
```

- 파일은 `git ls-tree`/`git cat-file`로 `--ref`에서 읽습니다. 작업 중인 브랜치를 바꾸지 않으므로 개발 중인 체크아웃을 그대로 써도 됩니다.
- 상태 파일(`--state`)이 경로별 git blob·문서 ID를 기억합니다. 바뀐 파일만 새로 올린 뒤 이전 문서를 지우고, 저장소에서 지운 파일은 문서도 지웁니다.
  올리기가 실패하면 이전 문서를 그대로 두고 다음 실행에서 다시 시도합니다.
- 문서 소유자는 `--user`(기본 `repo-sync`), 열람 역할은 `--allowed-roles`(기본 `member,admin`), 분류는 `--category code`,
  태그는 `repo:<이름>`, `kind:doc|code-map|history`, `ref:<ref>`입니다. OIDC 환경이면 `BARYON_API_TOKEN`을 넣습니다.
- 주기 실행은 cron·작업 스케줄러로 `--fetch`와 함께 돌리면 됩니다. 첫 실행은 문서 수만큼 임베딩하므로 몇 분 걸릴 수 있습니다.

## 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--include` / `--exclude` | `**/*.md **/*.txt` / `node_modules`, `dist`, `build`, `target`, `vendor` 등 | 문서로 올릴 경로 |
| `--module-depth` | `1` | 코드 지도 한 장의 단위가 되는 폴더 깊이. 큰 모노레포는 `2` |
| `--history` | `300` | 변경 이력에 넣을 커밋 수, `0`이면 끔 |
| `--no-docs` / `--no-code-map` | — | 한쪽만 올리기 |

## 테스트

```bash
python -m pytest -q tests
```
