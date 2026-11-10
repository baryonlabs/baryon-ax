# B300 호스트 전체 플랫폼 배포

> 검증일: 2026-11-11  
> 대상: B300 서버 (`cloud-Ox5IZb`, x86_64, 8× NVIDIA B300 SXM6 PC)  
> 목적: **Mac 로컬이 아니라 B300 서버 안에서** Baryon AX 전체 플랫폼을 실행하고, GPU 7의 vLLM을 사용

## 1. 배포 구조

```text
B300 서버
├─ vLLM 0.32.0 (호스트 프로세스) ── GPU 7 ── Qwen3.5-27B ── 127.0.0.1:8020
├─ TCP proxy 8021 ── 127.0.0.1:8020 (Docker bridge에서 접근)
└─ Docker Compose (baryon-ax 프로젝트)
   ├─ web      127.0.0.1:3101 → 80
   ├─ api      127.0.0.1:8100 → 8000
   ├─ langfuse 127.0.0.1:3102 → 3000
   ├─ postgres 127.0.0.1:5533 → 5432
   ├─ redis    127.0.0.1:6479 → 6379
   └─ qdrant   127.0.0.1:6433/6434 → 6333/6334
```

- 플랫폼은 **B300 안에서** 실행됩니다.
- Mac은 SSH 터널로 UI/API에 접속만 합니다.
- vLLM은 호스트 GPU 7을 사용하고, API 컨테이너는 `host.docker.internal:8021`로 접근합니다.
- Langfuse와 Mem0도 B300 안에서 동작합니다.
- Mem0의 LLM은 vLLM(OpenAI 호환)을 사용하고, embedder는 FastEmbed를 사용합니다.
- 검증 시점에는 GPU 7을 사용했습니다. 이후 GPU 7에 별도 학습 작업이 들어가, 현재 27B는 GPU 0(gpu-memory-utilization 0.30)에서 운영 중입니다.

## 2. 준비 파일

| 파일 | 역할 |
| --- | --- |
| `.env.b300.example` | B300 호스트용 환경변수 템플릿 |
| `docker-compose.b300-host.yml` | `/data` 바인드 마운트, Langfuse 서비스, API 의존성 |
| `scripts/b300_serve_vllm.sh` | B300 GPU 7 vLLM 실행 (B300에서 실행) |
| `scripts/b300_vllm_tunnel.sh` | Mac에서 vLLM 포트만 터널링할 때 사용 |
| `scripts/ollama_proxy.py` | loopback vLLM을 Docker bridge에 노출하는 TCP 프록시 |
| `scripts/smoke_full_stack.py` | 전체 스택 스모크 |

## 3. vLLM + 프록시

### vLLM (GPU 7)

```bash
scp scripts/b300_serve_vllm.sh b300:/data/baryon-b300/scripts/
ssh b300 'mkdir -p /data/baryon-b300/logs && \
  tmux new-session -d -s baryon-vllm \
  "bash /data/baryon-b300/scripts/b300_serve_vllm.sh >> /data/baryon-b300/logs/serve-baryon-ax-vllm.log 2>&1"'

ssh b300 'curl -sf http://127.0.0.1:8020/v1/models | python3 -m json.tool | head'
ssh b300 'nvidia-smi -i 7 --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader'
```

### Docker bridge 프록시

vLLM은 `127.0.0.1:8020`에만 바인딩됩니다. Linux Docker에서는 `host.docker.internal`이 호스트 loopback에 직접 닿지 않으므로 프록시를 둡니다.

```bash
ssh b300 'tmux new-session -d -s baryon-vllm-proxy \
  "python3 /data/baryon-b300/baryon-ax/scripts/ollama_proxy.py \
   --listen-host 0.0.0.0 --listen-port 8021 \
   --target-host 127.0.0.1 --target-port 8020"'

# Docker private 대역에서만 8021 허용 (외부 차단)
ssh b300 'sudo ufw allow from 172.16.0.0/12 to any port 8021 proto tcp'
```

확인:

```bash
ssh b300 'docker run --rm --add-host host.docker.internal:host-gateway \
  curlimages/curl:latest -s http://host.docker.internal:8021/v1/models | head -c 160'
```

## 4. 디렉터리와 환경변수

```bash
ssh b300 'mkdir -p /data/baryon-b300/baryon-ax-data/{documents,audit,mem0,fastembed,postgres,qdrant}'
ssh b300 'sudo chown -R 10001:10001 /data/baryon-b300/baryon-ax-data/{documents,audit,mem0,fastembed}'
ssh b300 'sudo chown -R 999:999 /data/baryon-b300/baryon-ax-data/postgres'
ssh b300 'sudo chown -R 0:0 /data/baryon-b300/baryon-ax-data/qdrant'
```

`.env.b300.example`을 복사하고 비밀번호/키를 채웁니다.

```bash
ssh b300 'cd /data/baryon-b300/baryon-ax && cp .env.b300.example .env'
# POSTGRES_PASSWORD, LANGFUSE_SECRET_KEY, LANGFUSE_NEXTAUTH_SECRET, LANGFUSE_SALT 변경
```

주요 값:

| 변수 | 값 |
| --- | --- |
| `BIND_HOST` | `127.0.0.1` |
| `API_PORT` / `WEB_PORT` | `8100` / `3101` |
| `POSTGRES_PORT` / `REDIS_PORT` | `5533` / `6479` |
| `QDRANT_HTTP_PORT` / `QDRANT_GRPC_PORT` | `6433` / `6434` |
| `MODEL_HOST_URL` | `http://host.docker.internal:8021` |
| `MODEL_NAME` | `baryon-ax-qwen` |
| `MODEL_DISABLE_THINKING` | `true` |
| `LANGFUSE_ENABLED` / `LANGFUSE_HOST` | `true` / `http://langfuse:3000` |
| `MEM0_ENABLED` | `true` |
| `MEM0_LLM_PROVIDER` | `openai` |
| `MEM0_LLM_BASE_URL` | `http://host.docker.internal:8021/v1` |
| `MEM0_LLM_MODEL` | `baryon-ax-qwen` |

## 5. 기존 볼륨에서 `/data`로 이전 (선택)

기존 named volume을 사용했다면, 컨테이너를 내린 뒤 복사합니다. 새 배포라면 생략 가능합니다.

```bash
ssh b300 'cd /data/baryon-b300/baryon-ax && \
  docker compose -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.b300-host.yml --profile deps down'

for pair in "documents:documents" "audit:audit" "mem0:mem0" "models:fastembed" "postgres:postgres" "qdrant:qdrant"; do
  vol=${pair%%:*}; dir=${pair##*:}
  ssh b300 "docker run --rm -v baryon-ax_${vol}:/from \
    -v /data/baryon-b300/baryon-ax-data/${dir}:/to alpine sh -c 'cp -a /from/. /to/'"
done

ssh b300 'sudo chown -R 10001:10001 /data/baryon-b300/baryon-ax-data/{documents,audit,mem0,fastembed}'
ssh b300 'sudo chown -R 999:999 /data/baryon-b300/baryon-ax-data/postgres'
ssh b300 'sudo chown -R 0:0 /data/baryon-b300/baryon-ax-data/qdrant'
```

> 바인드 마운트는 `baryonai` 사용자가 직접 `chown`할 수 없습니다. `sudo`를 사용하세요.

## 6. Langfuse DB 생성

`baryon-ax-host.yml`의 Langfuse는 플랫폼 PostgreSQL의 `langfuse` DB를 사용합니다.

```bash
ssh b300 'cd /data/baryon-b300/baryon-ax && \
  docker compose -f docker-compose.yml -f docker-compose.full.yml -f docker-compose.b300-host.yml --profile deps up -d postgres'

ssh b300 'if ! docker exec -i baryon-ax-postgres-1 psql -U baryon -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='\''langfuse'\''" | grep -q 1; then \
  docker exec -i baryon-ax-postgres-1 psql -U baryon -d postgres -c "CREATE DATABASE langfuse"; fi'
```

## 7. 전체 스택 시작

```bash
ssh b300 'cd /data/baryon-b300/baryon-ax && \
  docker compose -f docker-compose.yml -f docker-compose.full.yml \
  -f docker-compose.b300-host.yml --profile deps up -d --build'
```

Langfuse가 healthy가 된 뒤 API와 Web이 시작됩니다.

```bash
ssh b300 'cd /data/baryon-b300/baryon-ax && \
  docker compose -f docker-compose.yml -f docker-compose.full.yml \
  -f docker-compose.b300-host.yml --profile deps ps'
```

## 8. Mac에서 접속

B300 포트는 `127.0.0.1`에만 바인딩되어 있습니다. Mac에서 SSH 터널을 엽니다.

```bash
ssh -N \
  -L 8100:127.0.0.1:8100 \
  -L 3101:127.0.0.1:3101 \
  -L 3102:127.0.0.1:3102 \
  b300
```

- UI: `http://127.0.0.1:3101`
- API: `http://127.0.0.1:8100`
- Langfuse: `http://127.0.0.1:3102`

## 9. 검증

```bash
curl -s http://127.0.0.1:8100/health | python3 -m json.tool

API_BASE=http://127.0.0.1:8100 REQUIRE_FULL=1 \
  python3 scripts/smoke_full_stack.py

cd apps/web
E2E_BASE_URL=http://127.0.0.1:3101 npm run test:e2e
```

정상 `/health`:

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

검증 결과:

| 항목 | 결과 |
| --- | --- |
| vLLM GPU 7 | Qwen3.5-27B, 약 246GB VRAM |
| 채팅 | 약 0.49초 (비스트리밍), 스트리밍 정상 |
| Qdrant | 문서 청크 검색 + 출처 |
| PostgreSQL | 감사 이벤트 |
| Redis | 분당 요청 제한 |
| Langfuse | trace/generation |
| Mem0 | 메모리 저장/검색 (vLLM OpenAI provider) |
| Full-stack smoke | ALL CHECKS PASSED |
| Playwright E2E | 12 checks passed |
| API tests | 24 passed |

## 10. 문제 해결

| 증상 | 원인/해결 |
| --- | --- |
| Qdrant `Too many open files` | `docker-compose.yml`의 qdrant `ulimits.nofile` 65536 적용 |
| Langfuse unhealthy | Next.js가 loopback이 아닌 컨테이너 hostname에 바인딩. healthcheck를 `node -e fetch('http://'+os.hostname()+':3000/...')`로 변경 |
| Postgres bind mount 권한 | `sudo chown -R 999:999 .../postgres` |
| Qdrant/API bind mount 권한 | API 10001, Qdrant 0 |
| 컨테이너에서 vLLM 접근 실패 | vLLM은 loopback. 8021 프록시 + UFW Docker 대역 허용 |
| Mem0 메모리 추출 실패 | `MEM0_LLM_PROVIDER=openai`, `MEM0_LLM_BASE_URL=http://host.docker.internal:8021/v1`, 모델명 일치 확인 |
| 기존 문서가 검색되지 않음 | `POST /v1/admin/reindex` 또는 관리자 **벡터 재색인** |

## 11. 종료/재시작

```bash
# 플랫폼만 종료 (데이터 유지)
ssh b300 'cd /data/baryon-b300/baryon-ax && \
  docker compose -f docker-compose.yml -f docker-compose.full.yml \
  -f docker-compose.b300-host.yml --profile deps down'

# vLLM 종료 (GPU 7 반환)
ssh b300 'pgrep -af "vllm serve.*8020"'
ssh b300 'pkill -TERM -f "vllm serve models/base/Qwen3.5-27B.*8020"'
ssh b300 'nvidia-smi -i 7 --query-gpu=memory.used --format=csv,noheader'

# 프록시 종료
ssh b300 'tmux kill-session -t baryon-vllm-proxy'
```

재시작은 3장(vLLM/프록시) → 7장(Compose) 순서로 진행합니다.

## 12. 보안/운영 메모

- 모든 플랫폼 포트는 `127.0.0.1` 바인딩, Mac은 SSH 터널 사용.
- UFW는 22만 공개하고, 8021은 Docker private 대역(172.16.0.0/12)만 허용.
- vLLM/API에는 인증이 없습니다. 터널 밖으로 노출하지 마세요.
- `/data/baryon-b300/baryon-ax-data`에 문서·감사·Mem0·Postgres·Qdrant·FastEmbed 캐시가 있습니다. 백업 대상입니다.
- Langfuse에는 프롬프트/응답이 저장됩니다. 보존/삭제 정책을 정의하세요.
- 현재 신원은 개발용 헤더입니다. 프로덕션 전 OIDC/JWT로 교체하세요.
