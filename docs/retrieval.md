# 검색: Qdrant 벡터 + 어휘 하이브리드

Baryon AX는 근거를 **Qdrant 벡터 검색과 어휘(lexical) 검색을 합친 하이브리드**로 찾습니다. 기본 `docker compose up`에 Qdrant가 함께 뜹니다. Qdrant에 닿지 못하면 어휘 검색으로 자동 전환되어, 검색 결과가 비지 않습니다.

## 왜 바꿨나

어휘 검색은 질문과 문서가 **같은 단어**를 써야 찾습니다. "1년 일하면 쉬는 날 며칠 받아요?"라는 질문은 "연차휴가는 입사일 기준 1년을 근무하면 15일이 부여"라는 문장을 못 찾습니다. 반복 테스트에서 이 문제가 병목으로 나왔습니다. 제3자가 만든 질문으로 300자 조각 원문을 검색했을 때 13문항 중 실질 답이 나온 문항은 0개였습니다.

벡터 검색은 뜻이 가까운 문장을 찾습니다. 대신 정확한 용어·코드·숫자 질문에는 어휘 검색이 더 강합니다. 그래서 둘을 합칩니다(아래 [벤치 결과](#bench)).

## 검색 경로

모든 검색은 `DocumentService.search` 하나를 거칩니다. 그래서 아래 기능이 모두 같은 하이브리드 검색을 씁니다.

| 쓰는 곳 | 경로 |
| --- | --- |
| 채팅 근거 | `POST /v1/chat/completions` (`use_retrieval: true`, 기본값) → LangGraph `retrieve` 단계. 대화가 여러 턴이면 [대화 맥락 검색](#conversation) |
| 문서 검색 API | `GET /v1/documents/search?q=` |
| MCP `search_documents` · `ask` | `POST /mcp` |
| 지식 공백 자동 해결 | 문서가 들어오면 열린 공백 질문을 다시 검색 |
| 온톨로지 꾸러미 검증 | 꾸러미의 수락 질문(acceptance questions)을 다시 검색해 커버리지 계산 |

```text
질문 ─┬─ VectorIndex.search ── Qdrant (organization_id 필수 + owner_id 또는 allowed_roles 필터)
      │                         └─ can_view 재확인 (문서 저장소 기준)
      └─ 어휘 검색 ──────────── can_view로 거른 조각의 단어 일치
                 ↓
      가중 RRF 결합 → 상위 4개 → 모델 프롬프트의 [검색 근거]
```

<a id="conversation"></a>
### 대화 맥락 검색 (후속 질문)

**문제**: 채팅은 예전에 **마지막 메시지만**으로 문서를 검색했습니다. "다시 해봐", "그거 요구사항 어디에 있어?",
"이 문서 안에 설명돼 있는지 확인해" 같은 후속 질문에는 주제어가 없습니다. 그래서 엉뚱한 조각(주간 보고, 목차)이
검색되고, 모델은 "문서에서 찾지 못했습니다"라고 답했습니다. 같은 주제어로 검색하면 1등으로 나오는 문서였습니다.

**동작**: 여러 턴 대화에서는 두 번 검색해 번갈아 합칩니다.

```text
검색 A: 마지막 메시지
검색 B: 첫 사용자 질문 + 최근 사용자 질문 2개 + 마지막 메시지   (각 300자까지)
결과 : A1, B1, A2, B2, …  (같은 조각은 한 번만) → 상위 4개
```

- **첫 사용자 질문**을 항상 넣습니다. 스레드의 첫 글이 보통 주제를 말하고, 중간 대화에는 주제어가 빠지기 쉽기 때문입니다.
- **번갈아 합치기**를 씁니다. 점수 결합(RRF)은 후속 질문의 단어("다시", "확인")와 대충 겹치는 조각이 두 목록에 모두
  들어가 1등이 되어, 이어받은 주제를 밀어냈습니다. 번갈아 합치면 이어받은 주제와 이번 턴의 새 주제가 모두 앞자리를 얻습니다.
- 한 턴짜리 질문은 예전처럼 한 번만 검색합니다. 문서 검색 API · MCP `search_documents` · 지식 공백 경로도 그대로입니다.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `RETRIEVAL_HISTORY_TURNS` | `2` | 검색 B에 넣을 최근 사용자 질문 수. `0`이면 마지막 메시지만(이전 동작) |
| `RETRIEVAL_HISTORY_CHARS` | `300` | 이전 질문을 자르는 길이. 붙여 넣은 긴 글이 질문을 덮지 않게 |

**측정**: 실제 업무 스레드 11턴(요구사항 점검 대화)을 그대로 재생해, 묻는 요구사항의 근거 조각이 인용 4개 안에
들어왔는지 셌습니다.

| 검색 | 근거가 들어온 턴 |
| --- | --- |
| 마지막 메시지만 | 6 / 11 |
| 대화 맥락 검색 | **11 / 11** |

"다시 해봐", "요구사항 문서 어디에 있어?" 같은 턴이 근거를 찾게 됐고, 짧은 용어 질문("Text-to-SQL")도 놓쳤던 요구사항
표를 새로 찾았습니다. 테스트: `tests/test_contextual_retrieval.py`.

### 무엇이 색인되나

- **업로드 문서**: 모든 파서(kordoc · docling · Tika · 텍스트)가 만든 구조 조각(heading · page · kind 메타데이터 포함)
- **암묵지 답변**: 전문가 답을 게시하면(`POST /v1/tacit/entries/{id}/publish`) `# 암묵지: {질문}` 문서로 같은 파이프라인을 거쳐 색인됩니다. 온톨로지 공백을 채운 지식은 이 경로로 검색에 들어옵니다.
- **Ossie 가져오기**: 데이터셋 · 필드 · 메트릭 · 관계 문서

온톨로지 꾸러미 자체(엔티티 · 슬롯 · 수락 질문)는 색인하지 않습니다. 꾸러미는 **아직 답이 없는 질문의 구조**라서, 색인하면 질문이 자기 자신을 근거로 인용하게 됩니다. 꾸러미 검증은 위 표처럼 하이브리드 검색으로 실제 근거가 생겼는지 확인합니다.

## 권한과 조직 분리

기존 동작을 그대로 지킵니다.

- Qdrant 질의에는 항상 `organization_id = x-organization-id` 필터가 붙습니다. admin이 아니면 `owner_id = 사용자` 또는 `allowed_roles ∈ x-roles` 조건도 붙습니다.
- Qdrant가 돌려준 결과도 문서 저장소 기준 `can_view`로 다시 거릅니다. 삭제된 문서의 남은 벡터나 색인 뒤 바뀐 권한이 새지 않습니다.
- 권한 변경(`PATCH` 역할)은 Qdrant payload에도 반영되고, 문서 삭제와 테넌트 삭제(purge)는 벡터도 지웁니다.
- 어휘 검색 쪽도 같은 `can_view`를 씁니다.

테스트: `tests/test_vector_retrieval.py`가 실제 Qdrant(in-process) 위에서 조직 분리 · 역할 · 소유자 · admin · 권한 변경 · 삭제 · purge · HTTP 헤더를 확인합니다.

## 설정

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `RETRIEVAL_MODE` | `hybrid` | `hybrid` 벡터 + 어휘 결합 · `vector` Qdrant만 · `lexical` 단어 일치만(이전 동작) |
| `QDRANT_URL` | Compose: `http://qdrant:6333` | 비우면 벡터 검색을 끕니다. `:memory:`(테스트용 in-process), `path:/dir`(내장 디스크)도 받습니다 |
| `QDRANT_URL_DOCKER` | `http://qdrant:6333` | Compose에서 API가 쓰는 주소. `QDRANT_URL_DOCKER=` 로 비우면 벡터 끔 |
| `QDRANT_COLLECTION` | `baryon_documents` | 임베딩 모델을 바꾸면 새 이름을 쓰거나 `--recreate` |
| `QDRANT_REINDEX_ON_START` | `auto` | `auto` 컬렉션이 비어 있으면 백그라운드로 채움 · `true` 시작할 때마다 전부 · `false` 안 함 |
| `QDRANT_RETRY_SECONDS` | `30` | Qdrant가 API보다 늦게 떴을 때 다시 연결을 시도하는 간격 |
| `QDRANT_BIND_HOST` / `QDRANT_HTTP_PORT` | `127.0.0.1` / `6333` | 호스트에 여는 주소. Qdrant는 인증이 없어 기본은 루프백만 엽니다 |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` / `384` | 한국어를 포함한 다국어 모델. 이미지에 미리 내려받습니다(`PRELOAD_EMBEDDINGS=true`) |
| `EMBEDDING_PROVIDER` | `fastembed` | `openai`면 OpenAI 호환 `/v1/embeddings`(`EMBEDDING_BASE_URL`) |
| `RETRIEVAL_MIN_SCORE` | `0.3` | 벡터 결과로 인정할 최소 코사인 유사도 |
| `RETRIEVAL_CANDIDATES` | 결과 수의 2배(8) | 결합 전에 검색기마다 가져오는 후보 수 |
| `RETRIEVAL_VECTOR_WEIGHT` / `RETRIEVAL_LEXICAL_WEIGHT` | `1.0` / `1.0` | 결합 가중치 |
| `RETRIEVAL_RRF_K` | `60` | RRF 상수 |

**점수 결합**: 코사인 유사도(0~1)와 단어 일치 수(정수)는 척도가 달라 그대로 더할 수 없습니다. 그래서 순위를 합칩니다(Reciprocal Rank Fusion): `score = Σ weight / (k + rank)`. 두 검색기가 모두 찾은 조각이 위로 올라옵니다. 가중치와 후보 수는 벤치로 골랐습니다. 벡터 가중치 1.5~2.0은 통제 질문 1개를 잃었고, 후보를 12~20개로 늘려도 결과가 같거나 나빠졌습니다.

`/health`는 `vector_backend`(`qdrant`/`lexical`)와 실제로 쓰는 `retrieval_mode`를 보여 줍니다.

<a id="migration"></a>
## 마이그레이션 (기존 문서 재색인)

어휘 검색만 쓰던 설치는 문서 JSON(`/data/documents`)만 있고 벡터가 없습니다. 문서 원본은 그대로 두고 벡터만 새로 만듭니다. 벡터는 문서에서 다시 만들 수 있는 **파생 데이터**라, 지워도 손실이 없습니다.

1. **자동**: `QDRANT_REINDEX_ON_START=auto`(기본)이면 Qdrant와 처음 연결되고 컬렉션이 비어 있을 때 백그라운드로 전부 색인합니다. API는 바로 healthy가 되고, 색인이 끝나기 전에는 하이브리드의 어휘 쪽이 답합니다.
2. **수동(권장, 결과 확인 가능)**: API 컨테이너 안에서 실행합니다. 같은 설정(`DOCUMENT_STORE`, `QDRANT_URL`, `EMBEDDING_*`)을 읽습니다.

```bash
docker compose exec api python -m baryon.reindex --dry-run     # 문서 수 · 현재 벡터 수만 확인
docker compose exec api python -m baryon.reindex               # 전부 upsert (반복 실행해도 안전)
docker compose exec api python -m baryon.reindex --prune       # 삭제된 문서의 남은 벡터 정리
docker compose exec api python -m baryon.reindex --recreate    # 컬렉션을 지우고 새로 (임베딩 모델 변경 시)
docker compose exec api python -m baryon.reindex --organization acme   # 한 조직만
```

출력 예: `done in 4.2s: indexed=19 failed=0 pruned=0 points_after=212`. 실패한 문서가 있으면 id를 찍고 종료 코드 1, Qdrant가 꺼져 있으면 2를 돌려줍니다. 관리자 API `POST /v1/admin/reindex`(admin 역할)도 같은 일을 합니다.

포인트 id는 `문서 id + 조각 번호`에서 만들어 upsert가 멱등입니다. 서비스 중에 돌려도 되고, 중간에 끊기면 다시 돌리면 됩니다. 문서를 다시 잘라 조각 수가 줄면 남는 조각 벡터도 지웁니다.

**임베딩 모델을 바꿀 때**: 차원이 다르면 API가 기존 컬렉션을 쓰지 않고 어휘 검색으로 전환하며 로그에 이유를 남깁니다. `QDRANT_COLLECTION`을 새 이름으로 바꾸거나(`auto`가 채움), `--recreate`로 다시 만듭니다.

<a id="bench"></a>
## 벤치 결과: 어휘 불일치 회귀 세트

`apps/api/eval/retrieval_paraphrase_ko.json`: 가상 조직의 규정 문서 15개(44조각)와 질문 43개입니다.

- **paraphrase 33문항**: 문서에 없는 말로 묻습니다. 예: "장가가면 회사에서 축의금 줘요?" → "본인 결혼 시 … 축하금 50만 원", "늦게 끝나서 집에 갈 차비는?" → "22시 이후 퇴근 시 택시비를 실비로 정산"
- **control 10문항**: 문서와 같은 단어로 묻습니다. 하이브리드가 어휘 검색이 이미 잘 찾던 것을 잃지 않는지 봅니다.

세 모드 모두 실제 경로로 잽니다. `DocumentService.search` + in-process Qdrant + 같은 권한 필터이고, 임베딩은 기본 모델(`paraphrase-multilingual-MiniLM-L12-v2`)입니다. **근거 검색 성공**은 출처 문서가 상위 4개(채팅 기본값) 안에 드는 것입니다.

| 모드 | 세트 | 근거 검색 성공 (doc@4) | 정확한 조각 (chunk@4) | 1위 정답 (doc@1) | MRR |
| --- | --- | --- | --- | --- | --- |
| lexical | paraphrase (33) | **0.394** (13/33) | 0.182 | 0.182 | 0.265 |
| vector | paraphrase (33) | 0.848 (28/33) | 0.727 | 0.636 | 0.730 |
| **hybrid** | paraphrase (33) | **0.909** (30/33) | 0.727 | 0.545 | 0.694 |
| lexical | control (10) | 1.000 (10/10) | 1.000 | 1.000 | 1.000 |
| vector | control (10) | 0.800 (8/10) | 0.600 | 0.500 | 0.600 |
| **hybrid** | control (10) | **1.000** (10/10) | 1.000 | 0.700 | 0.833 |
| lexical | 전체 (43) | 0.535 (23/43) | 0.372 | 0.372 | 0.436 |
| vector | 전체 (43) | 0.837 (36/43) | 0.698 | 0.605 | 0.700 |
| **hybrid** | 전체 (43) | **0.930** (40/43) | 0.791 | 0.581 | 0.727 |

읽는 법:

- 어휘가 다른 질문에서 근거 검색 성공률이 **39.4% → 90.9%**(+51.5%p)로 올랐습니다.
- 같은 단어로 묻는 질문은 하이브리드도 10/10을 지킵니다. 벡터만 쓰면 2개를 잃습니다(정확한 용어 질문). 그래서 기본값이 `vector`가 아니라 `hybrid`입니다.
- 1위 정답률(doc@1)은 하이브리드가 벡터보다 조금 낮습니다. 어휘 검색이 흔한 두 글자 접두어로 섞는 잡음 때문입니다. 모델에는 상위 4개가 모두 들어가므로, 근거가 4개 안에 드는지(doc@4)가 답의 품질을 좌우합니다.
- 질문 세트가 작은 가상 데이터라는 한계가 있습니다. 운영 문서로 다시 재는 방법은 아래 재현 절차와 같습니다(데이터셋 JSON만 바꾸면 됨).

재현:

```bash
cd apps/api
uv run python eval/retrieval_eval.py                        # 실제 모델 (처음 한 번 약 220 MB 내려받음)
uv run python eval/retrieval_eval.py --embedder fixture     # 기록된 벡터로 (내려받기 없음)
uv run python eval/retrieval_eval.py --json /tmp/report.json   # 문항별 순위
uv run python eval/retrieval_eval.py --record-fixture       # 질문 세트를 바꾼 뒤 CI용 벡터 다시 기록
```

CI는 `tests/test_retrieval_regression.py`에서 실제 모델로 기록한 벡터(`tests/fixtures/retrieval_paraphrase_embeddings.json`, float16)를 재생합니다. 같은 경로로 위 수치를 다시 재고, 다음 조건을 확인합니다.

- paraphrase에서 하이브리드가 어휘 검색보다 30%p 이상 높다
- 하이브리드가 0.85 이상이다
- control에서 하이브리드가 어휘 검색 이상이다

<a id="deploy"></a>
## 운영 배포 절차 (Mac mini 예시)

> 이 PR은 운영 서버에 배포하지 않습니다. owner 확인 후 아래 순서로 진행합니다.

현재 Mac mini 상태(읽기 전용 확인, 2026-11-23):

- `docker-compose.yml` + 로컬 `docker-compose.macmini.yml`(restart 정책만)로 `api`·`web`만 실행 중입니다.
- `QDRANT_URL`이 비어 있어 어휘 검색으로 동작하고, 문서는 37건입니다.
- `.env`에 `QDRANT_REINDEX_ON_START=false`가 있어 자동 채움이 일어나지 않습니다. 아래 수동 `reindex` 단계가 필요합니다.
- 저장소 체크아웃은 `#57` 커밋이라, 그 뒤 변경도 함께 들어갑니다.

**배포 전 백업** (문서 원본은 바뀌지 않지만, 되돌릴 지점을 남깁니다):

```bash
cd ~/dev/baryon-ax
git rev-parse HEAD > ~/baryon-ax-pre-qdrant.rev
docker run --rm -v baryon-ax_documents:/d -v "$HOME":/b alpine tar czf /b/baryon-ax-documents-$(date +%F).tgz -C /d .
grep -E '^(RETRIEVAL_MODE|QDRANT_|EMBEDDING_)' .env    # .env에 남은 옛 값 확인
```

`.env`에 `RETRIEVAL_MODE=vector`나 `QDRANT_REINDEX_ON_START=false`가 남아 있으면 Compose 기본값보다 우선합니다. 하이브리드를 쓰려면 `RETRIEVAL_MODE=hybrid`로 고치거나 그 줄을 지웁니다.

**배포**:

```bash
git fetch && git checkout main && git pull --ff-only
docker compose -f docker-compose.yml -f docker-compose.macmini.yml up -d --build   # qdrant가 새로 뜸, 모델은 이미지에 포함
docker compose -f docker-compose.yml -f docker-compose.macmini.yml exec api python -m baryon.reindex --dry-run
docker compose -f docker-compose.yml -f docker-compose.macmini.yml exec api python -m baryon.reindex
curl -s http://localhost:8000/health    # vector_backend: qdrant, retrieval_mode: hybrid
```

**확인**:

```bash
curl -s 'http://localhost:8000/v1/documents/search?q=<문서에 없는 말로 바꾼 질문>' \
  -H 'X-User-Id: <user>' -H 'X-Organization-Id: <org>' -H 'X-Roles: member'
```

같은 질문을 `RETRIEVAL_MODE=lexical`로 띄운 결과와 비교하려면 `eval/retrieval_eval.py`의 데이터셋을 운영 문서로 바꿔 돌립니다. 조직 헤더를 바꾸면 결과가 비어야 합니다.

**자원** (로컬 Docker Desktop, Apple Silicon에서 측정):

- Qdrant 컨테이너: 약 90 MB RAM
- API 컨테이너: 임베딩 모델 포함 약 730 MB RAM
- 이미지: 임베딩 모델 241 MB만큼 커짐
- 색인: 문서 15건에 5.4초(모델 로드 포함)

이미 있는 `baryon-ax_models` 볼륨은 이미지에 든 모델을 가립니다. 이때 모델은 첫 검색에서 한 번 내려받습니다(약 220 MB, 인터넷 필요). 인터넷이 없는 서버는 배포 전에 그 볼륨을 지워 이미지의 모델이 복사되게 합니다(`docker volume rm baryon-ax_models`, 파생 데이터).

**배포 리허설** (같은 절차를 로컬 Compose로 실행, 2026-11-23):

1. Qdrant 없이(`QDRANT_URL_DOCKER=`) 벤치 문서 15건을 업로드 API로 넣었습니다. 어휘 검색만 쓰던 설치와 같은 상태입니다.
2. 기본 설정으로 재시작하자 `auto`가 백그라운드로 15건을 색인했습니다(`Backfilled 15 documents`).
3. `python -m baryon.reindex --prune`가 `indexed=15 failed=0`으로 끝났습니다.
4. `GET /v1/documents/search`로 43문항을 물었습니다. paraphrase에서 하이브리드는 **31/33**, `RETRIEVAL_MODE=lexical`로 되돌리면 14/33을 찾았습니다. control은 두 모드 모두 10/10이었습니다.
5. 다른 `X-Organization-Id`로 물으면 결과는 0건이었습니다.

<a id="rollback"></a>
## 롤백

단계가 가벼운 순서입니다.

1. **검색만 이전 동작으로** (재빌드 없음, 몇 초): `.env`에 `RETRIEVAL_MODE=lexical`을 넣고 `docker compose … up -d api`. Qdrant는 떠 있어도 쓰이지 않습니다. 업로드는 계속 색인되므로 다시 `hybrid`로 돌아올 때 재색인이 필요 없습니다.
2. **벡터 색인 끄기**: `.env`에 `QDRANT_URL_DOCKER=`(빈 값)을 넣고 `up -d api`, 이어서 `docker compose … stop qdrant`. 문서 데이터는 그대로입니다.
3. **코드 되돌리기**: `git checkout $(cat ~/baryon-ax-pre-qdrant.rev)` 후 `docker compose … up -d --build`. 이전 Compose는 qdrant를 `deps` 프로필에만 두므로 qdrant는 시작되지 않습니다. `docker compose … rm -sf qdrant`로 컨테이너를 지웁니다. 볼륨 `baryon-ax_qdrant`는 남겨도 되고, 필요 없으면 `docker volume rm baryon-ax_qdrant`로 지웁니다(파생 데이터).
4. 문서 저장소가 손상된 경우에만 백업 tar를 `baryon-ax_documents` 볼륨에 풉니다. 이 변경은 문서 JSON을 쓰지 않으므로 보통 필요 없습니다.

## 한계와 다음 단계

- 기본 임베딩 모델은 작은 모델(384차원)입니다. 한국어 품질을 더 올리려면 `intfloat/multilingual-e5-large`(1024차원, 2.2 GB)나 사내 GPU 임베딩(`EMBEDDING_PROVIDER=openai`)으로 바꾸고 `--recreate`로 재색인합니다. 바꾸기 전후 모두 `eval/retrieval_eval.py --model …`로 잽니다.
- 어휘 검색은 두 글자 접두어를 쓰는 단순한 방식이라 하이브리드의 1위 정확도를 조금 깎습니다. 형태소 분석기 기반 BM25나 재순위(reranker)가 다음 후보입니다.
- 질문 세트는 가상 데이터 43문항입니다. 운영 질문 로그(지식 공백)로 만든 세트로 다시 재는 것을 권합니다.
