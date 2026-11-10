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
| `MODEL_TIMEOUT_SECONDS` | `180` | 7B 모델 응답 대기 |
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
