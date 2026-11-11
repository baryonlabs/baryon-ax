# 전체 스택 실행 가이드

> 기준 버전: **0.6.0** · macOS(Apple Silicon) + Docker Desktop 환경에서 검증

이 문서는 Basic Core에 **Qdrant, PostgreSQL, Redis, Langfuse, LangGraph, LlamaIndex, Mem0, Ollama**를 실제로 연결해 실행하는 방법을 설명합니다.

## 1. 적용된 구성

| 계층 | 구성 요소 | 구현 | 기본 포트 |
| --- | --- | --- | --- |
| L4 UI | Chat Web UI | React + Vite / Nginx | `3001` (Compose), `5173` (Vite) |
| L3 실행 | FastAPI gateway | `apps/api` | `8000` |
| L3 실행 | LangGraph | API 내 정책·검색 오케스트레이션 | — |
| L3 실행 | Guardrails | API 내 `PolicyEngine` (NeMo/Guardrails AI는 선택) | — |
| L3 관측 | Langfuse | 기존 Langfuse v2 인스턴스 + SDK | `3000` |
| L2 지식 | Qdrant | 벡터 검색 (FastEmbed 다국어 임베딩) | `6333`, `6334` |
| L2 지식 | LlamaIndex | 문장 기반 청킹 | — |
| L2 세션 | Redis | 분당 요청 제한 (fixed window) | `6379` |
| L2 세션 | Mem0 | 사용자 메모리 (Qdrant + FastEmbed + Ollama) | — |
| L1 인프라 | PostgreSQL | 감사 이벤트 저장 (JSONL과 이중 기록) | `5433` |
| L1 모델 | Ollama | `qwen2.5:7b` OpenAI 호환 API | `11434` |
| L1 인프라 | Docker | API/Web/데이터 서비스 | — |
| L1 인프라 | Kubernetes | 스타터 매니페스트 (클러스터는 별도) | — |

> **대안**: Milvus(Qdrant 대안), vLLM/SGLang(Ollama 대안, Linux/GPU), LiteLLM(모델 게이트웨이)은 이 환경에서 기본 적용하지 않았습니다. 필요 시 어댑터/프로필을 추가할 수 있습니다.

## 2. 사전 준비

### Docker Desktop

- Docker Engine 29+, Compose v2 이상
- 메모리 24GB 이상 권장 (Ollama 7B 모델 + Qdrant + Langfuse)

### Ollama

```bash
ollama list
# qwen2.5:7b가 없으면:
ollama pull qwen2.5:7b
```

Ollama는 기본적으로 `127.0.0.1:11434`에만 바인딩됩니다. Docker Desktop 컨테이너에서 접근하려면 둘 중 하나를 사용하세요.

**A. 프록시 사용 (권장, Ollama 설정 변경 없음)**

```bash
python3 scripts/ollama_proxy.py --listen-port 11435
# 컨테이너에서 http://host.docker.internal:11435 로 접근
```

**B. Ollama를 모든 인터페이스에 바인딩**

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

### Langfuse

기존 Langfuse v2 인스턴스가 `http://127.0.0.1:3000`에서 실행 중이어야 합니다. 프로젝트의 public/secret key를 `.env`에 설정하세요.

```bash
curl http://127.0.0.1:3000/api/public/health
# {"status":"OK","version":"2.95.11"}
```

## 3. 환경 변수

`.env.example`을 복사한 뒤 값을 맞춥니다.

```bash
cp .env.example .env
```

| 변수 | 예시 | 설명 |
| --- | --- | --- |
| `MODEL_BASE_URL` | `http://127.0.0.1:11434/v1` | 호스트 실행 시 Ollama OpenAI 호환 URL |
| `MODEL_NAME` | `qwen2.5:7b` | 모델 ID |
| `MODEL_TIMEOUT_SECONDS` | `180` | 모델 응답 대기 |
| `MODEL_PROMPT_PROFILE` | `qwen35` | Qwen3.5용 시스템 프롬프트 (B300) |
| `RETRIEVAL_MIN_SCORE` | `0.3` | Qdrant 검색 최소 유사도 |
| `MAX_DOCUMENT_BYTES` | `20000000` | 업로드 상한 (PDF/HWP 대응) |
| `KORDOC_URL` | `http://kordoc:8080` | kordoc sidecar (HWP/HWPX/HWPML) |
| `DOCLING_URL` | `http://docling:8000` | docling sidecar (PDF/Office/이미지) |
| `TIKA_URL` | `http://tika:9998` | Tika fallback (레거시 포맷) |
| `PARSER_TIMEOUT_SECONDS` | `180` | 문서 1건 파싱 타임아웃 |
| `INGEST_BATCH_SIZE` | `8` | 인제스트 배치 크기 |
| `INGEST_MAX_IN_FLIGHT` | `4` | 동시 처리 문서 수 |
| `DATABASE_URL` | `postgresql://baryon:baryon@127.0.0.1:5433/baryon` | 호스트 실행 시 Postgres |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis |
| `QDRANT_URL` | `http://127.0.0.1:6333` | Qdrant |
| `QDRANT_COLLECTION` | `baryon_documents` | 문서 벡터 컬렉션 |
| `EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | FastEmbed 모델 (384차원) |
| `MEM0_ENABLED` | `true` | Mem0 메모리 활성화 |
| `MEM0_LLM_MODEL` | `qwen2.5:7b` | 메모리 추출 모델 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Mem0용 Ollama URL |
| `MEM0_HISTORY_DB` | `/tmp/baryon-ax-mem0/history.db` | Mem0 이력 SQLite |
| `MEM0_TELEMETRY` | `false` | Mem0 PostHog 텔레메트리 비활성화 (온프레미스 권장) |
| `LANGFUSE_ENABLED` | `true` | Langfuse 트레이싱 |
| `LANGFUSE_HOST` | `http://127.0.0.1:3000` | Langfuse 서버 |
| `LANGFUSE_PUBLIC_KEY` | `pk-lf-...` | 프로젝트 public key |
| `LANGFUSE_SECRET_KEY` | `sk-lf-...` | 프로젝트 secret key |
| `RATE_LIMIT_PER_MINUTE` | `60` | 사용자·조직당 분당 채팅 요청 수 |
| `QDRANT_REINDEX_ON_START` | `false` | 시작 시 전체 재색인 (모델 다운로드로 느려질 수 있음) |
| `WEB_PORT` | `3001` | Compose Web 포트 (Langfuse가 3000 사용) |

## 4. 실행 방법

### A. 전체 Compose 스택 (권장)

```bash
# Ollama 프록시를 사용하는 경우
python3 scripts/ollama_proxy.py --listen-port 11435 &

OLLAMA_HOST_URL=http://host.docker.internal:11435 \
  docker compose -f docker-compose.yml -f docker-compose.full.yml --profile deps up -d --build
```

- Web: `http://localhost:3001`
- API: `http://localhost:8000`
- Langfuse: `http://localhost:3000`
- Postgres: `localhost:5433`
- Redis: `localhost:6379`
- Qdrant: `localhost:6333` (대시보드 `:6333/dashboard`)

종료:

```bash
docker compose -f docker-compose.yml -f docker-compose.full.yml --profile deps down
```

### B. 호스트에서 API 실행 + 데이터 서비스만 Compose

```bash
docker compose --profile deps up -d postgres redis qdrant

python3.12 -m venv .venv
source .venv/bin/activate
(cd apps/api && pip install -e '.[dev]')
uvicorn baryon.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8000 --env-file .env

# 별도 터미널
npm --prefix apps/web run dev -- --host 127.0.0.1 --port 5173
```

### C. B300 GPU 7 원격 vLLM

B300의 마지막 GPU(GPU 7)에서 vLLM을 서빙하고, SSH 터널로 플랫폼에 연결할 수 있습니다.

```bash
scripts/b300_vllm_tunnel.sh &
OLLAMA_HOST_URL=http://host.docker.internal:11435 \
MODEL_HOST_URL=http://host.docker.internal:8020 \
MODEL_NAME=baryon-ax-qwen \
MODEL_DISABLE_THINKING=true \
MEM0_LLM_MODEL=qwen2.5:7b \
  docker compose -f docker-compose.yml -f docker-compose.full.yml --profile deps up -d --build

# 또는
docker compose -f docker-compose.yml -f docker-compose.full.yml \
  -f docker-compose.b300.yml --profile deps up -d --build
```

Qwen3.5는 thinking 출력을 끄기 위해 `MODEL_DISABLE_THINKING=true`를 사용합니다. vLLM 시작·종료·검증 상세는 [b300-vllm.md](b300-vllm.md)를 참고하세요.

### D. B300 호스트에 전체 플랫폼 배포

vLLM만 B300에 두는 구성이 아니라 **플랫폼 전체를 B300에서 실행**하려면 [b300-host-deploy.md](b300-host-deploy.md)를 사용합니다. Web/API/Qdrant/PostgreSQL/Redis/Langfuse/Mem0가 모두 B300 컨테이너에서 실행되고, Mac은 SSH 터널로 접속합니다.

### E. 파서 sidecar (HWP/PDF/Office/이미지)

문서 포맷을 넓히려면 파서를 별도 컨테이너로 띄웁니다. API 프로세스는 파서를 직접 실행하지 않습니다.

```bash
docker compose -f docker-compose.yml -f docker-compose.parsers.yml \
  --profile parsers up -d --build
```

- `kordoc` — HWP 3.x/5.x, HWPX, HWPML → Markdown/구조 블록 (Node 20, `KORDOC_OFFLINE=1`, read-only, 4 GB limit)
- `docling` — PDF/Office/이미지 → Markdown (python:3.12 + docling 이미지)
- `tika` — 위 어댑터가 처리하지 못하는 레거시 포맷 fallback

확인:

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/v1/documents \
  -H "X-User-Id: parser-test" -H "X-Organization-Id: parser-org" -H "X-Roles: admin" \
  -F "file=@sample.hwpx"
# {"parser":"kordoc","mime":"application/hwp+zip","chunks":4,...}
```

sidecar가 없으면 API는 text/markdown만 받습니다. 알 수 없는 바이너리는 415로 거절하고 `document.parse_failed` 감사를 남깁니다.

docling은 첫 변환 전에 모델을 한 번 받아야 합니다(오프라인 배포 대비):

```bash
docker exec baryon-ax-docling-1 docling-tools models download \
  layout tableformer rapidocr \
  --rapidocr-backend-lang torch:ch --rapidocr-backend-lang torch:iso:ko \
  -o /models
docker compose -f docker-compose.yml -f docker-compose.parsers.yml restart docling
```

계약·보안 설정은 [deploy/parsers/README.md](../deploy/parsers/README.md)를 참고하세요.

## 5. 검증

```bash
# 백엔드 상태
curl -s http://127.0.0.1:8000/health | python3 -m json.tool

# 전체 스택 스모크 (문서 업로드 → 검색 → 채팅 → 감사 → 메모리)
API_BASE=http://127.0.0.1:8000 REQUIRE_FULL=1 \
  python3 scripts/smoke_full_stack.py

# 브라우저 E2E
cd apps/web
E2E_BASE_URL=http://127.0.0.1:3001 npm run test:e2e
```

정상적인 `/health` 예시:

```json
{
  "status": "ok",
  "version": "0.6.0",
  "model_adapter": "openai-compatible",
  "vector_backend": "qdrant",
  "audit_backend": "postgresql",
  "cache_backend": "redis",
  "tracing_backend": "langfuse",
  "memory_backend": "mem0"
}
```

## 6. 데이터 볼륨

| 볼륨 | 내용 |
| --- | --- |
| `documents` | 원본 문서 JSON |
| `audit` | JSONL 감사 로그 (Postgres와 이중 기록) |
| `mem0` | Mem0 이력 SQLite |
| `models` | FastEmbed/HuggingFace 모델 캐시 (`FASTEMBED_CACHE_PATH=/data/fastembed`) |
| `postgres` | 감사 이벤트 테이블 |
| `qdrant` | 문서 벡터 + Mem0 벡터 |

## 7. 문제 해결

| 증상 | 원인/해결 |
| --- | --- |
| 컨테이너에서 Ollama 연결 실패 | Ollama가 `127.0.0.1`에만 바인딩됨. `scripts/ollama_proxy.py` 사용 또는 `OLLAMA_HOST=0.0.0.0` |
| 첫 업로드/검색이 느림 | FastEmbed 모델 최초 로딩. 이미지에 모델이 포함되어 있고 `models` 볼륨(`/data/fastembed`)에 캐시됨 |
| `Qdrant client version incompatible` | 클라이언트는 `1.12.x`, 서버는 `v1.12.5`로 고정 |
| Postgres 인증 실패 | 기존 볼륨이 이전 비밀번호로 초기화됨. 테스트 환경이면 `baryon-ax_postgres` 볼륨 재생성 |
| 기존 문서가 검색되지 않음 | `QDRANT_REINDEX_ON_START=false` 기본값. 관리자 설정의 **벡터 재색인** 또는 `POST /v1/admin/reindex` |
| Mem0 첫 요청이 느림 | Mem0를 lazy 초기화. 첫 메모리 요청에서 FastEmbed/Ollama 준비 |
| Langfuse trace가 안 보임 | `LANGFUSE_ENABLED`, 키, `LANGFUSE_HOST` 확인 |

## 8. 보안 주의

- 신원은 여전히 **개발용 헤더**(`X-User-Id`, `X-Organization-Id`, `X-Roles`)입니다. 프로덕션 전 OIDC/JWT로 교체하세요.
- Redis rate limit은 단일 인스턴스 fixed-window입니다. 다중 노드에서는 공유 Redis와 정교한 정책이 필요합니다.
- Langfuse에는 프롬프트/응답이 저장될 수 있습니다. 민감정보 보존 정책을 확인하세요.
- Mem0/Qdrant에는 사용자 메모리와 문서 벡터가 저장됩니다. 조직별 삭제·보존 정책을 정의하세요.
- PostgreSQL 감사 테이블과 JSONL은 이중 기록됩니다. 운영에서는 append-only/보존 정책을 적용하세요.
