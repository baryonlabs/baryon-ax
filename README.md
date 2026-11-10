# Baryon AX

**Baryon AX** is an on-premises, model-agnostic foundation for enterprise AI agents and grounded chat.

> Connect models. Orchestrate agents. Ground knowledge. Observe everything.

This repository is an executable Basic Core v0.6 reference implementation. It offers a complete vertical slice: role-aware chat, OpenAI-compatible model routing, document ingestion and vector retrieval with citations, policy checks, audit events, memory, and a browser UI. The Basic Core runs with a local demo model and file storage; the **full-stack profile** connects Ollama, Qdrant, PostgreSQL, Redis, Langfuse, LangGraph, LlamaIndex, and Mem0. See [docs/full-stack.md](docs/full-stack.md) for the applied stack.

## Platform at a glance

![Baryon AX platform overview: user flow, layered architecture, and core engines](docs/images/baryon-ax-platform-overview.png)

The platform brings a chat workspace, agent execution, grounded knowledge search, and on-premises infrastructure into one operating model. The image summarizes the user flow—ask, run an agent, evaluate, and review evidence—and the responsibilities of each layer. It is the **target production architecture**; the Basic Core maps most of it to in-process adapters, and the full-stack profile connects the stateful services. See [docs/architecture.md](docs/architecture.md#compose-vs-target-architecture) for the component-by-component mapping.

## What works today

- Chat UI with model selection, token streaming, and Markdown rendering (GFM)
- Keyboard-first composer: `Shift+Enter` sends, `Enter` starts a new line
- Korean-first bilingual UI (한국어 / English), with browser-language detection and persisted preference
- OpenAI-compatible model adapter, verified with **Ollama `qwen2.5:7b`** and **vLLM `Qwen3.5-27B` on B300 GPU 7**, with a safe local demo fallback
- Input/output policy checks and basic PII masking, including chunk-boundary-safe streaming masks
- Durable document upload, **LlamaIndex sentence chunking**, **Qdrant vector retrieval** with lexical fallback, and source citations
- **LangGraph** policy/retrieval orchestration, **FastEmbed** multilingual embeddings
- Role-aware document ACLs, inline administrator editing, and organization isolation
- **PostgreSQL audit events** with JSONL mirroring, **Redis rate limiting**, and **Langfuse tracing**
- **Mem0 user memory** with Qdrant + Ollama, managed from a dedicated UI tab
- Administrator settings with runtime, policy, storage, integration, and activity overview
- Live system cards for storage, memory, CPU load, process count, Compose containers, and backend health
- One-click admin links for Qdrant, Langfuse, the model server, pgAdmin, and Redis Insight, plus per-integration docs
- Docker Compose Basic Core profile and a **full-stack override** (`docker-compose.full.yml`)
- **B300 host deployment**: platform, Langfuse, Mem0, Qdrant, PostgreSQL, and Redis on the B300 server, using GPU 7 vLLM
- Kubernetes starter manifests and deployment profiles

## Quick start

### Basic Core (no external services)

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:3001`. The default `demo-local` model works without an API key.

### Full stack (Ollama + Qdrant + PostgreSQL + Redis + Langfuse + Mem0)

```bash
# Docker Desktop에서 Ollama(127.0.0.1)를 노출하는 프록시
python3 scripts/ollama_proxy.py --listen-port 11435 &

OLLAMA_HOST_URL=http://host.docker.internal:11435 \
  docker compose -f docker-compose.yml -f docker-compose.full.yml \
  --profile deps up -d --build
```

- UI: `http://localhost:3001` (Langfuse가 3000을 사용)
- API: `http://localhost:8000`
- 자세한 준비·검증·문제 해결: [docs/full-stack.md](docs/full-stack.md)

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL_BASE_URL` | empty | OpenAI-compatible endpoint; empty selects `demo-local` |
| `MODEL_NAME` | `qwen2.5:7b` | Model ID (full stack) |
| `MODEL_DISABLE_THINKING` | `false` | Send `enable_thinking=false` for Qwen3.5/vLLM |
| `MODEL_PROMPT_PROFILE` | `auto` | Model-specific grounded system prompt (`qwen35`, `qwen`, `generic`) |
| `RETRIEVAL_MIN_SCORE` | `0.3` | Minimum Qdrant similarity for a citation |
| `MODEL_TIMEOUT_SECONDS` | `180` | Upstream connect/read timeout |
| `DATABASE_URL` | empty | PostgreSQL audit store (empty = JSONL only) |
| `REDIS_URL` | empty | Redis rate limiter (empty = disabled) |
| `QDRANT_URL` | empty | Qdrant vector store (empty = lexical fallback) |
| `EMBEDDING_MODEL` | multilingual MiniLM | FastEmbed model |
| `LANGFUSE_ENABLED` / `LANGFUSE_HOST` | `false` / local | Langfuse tracing |
| `MEM0_ENABLED` / `OLLAMA_BASE_URL` | `false` / local | Mem0 memory layer |
| `DOCUMENT_STORE` | `/tmp/baryon-ax-documents` | Durable document JSON directory |
| `AUDIT_LOG` | `/tmp/baryon-ax-audit/events.jsonl` | JSONL audit mirror |
| `MAX_DOCUMENT_BYTES` | `2000000` | Upload size limit |
| `RATE_LIMIT_PER_MINUTE` | `60` | Chat requests per user/org per minute |

## Architecture

```text
Browser UI ──> FastAPI gateway ──> LangGraph policy/retrieval ──> model adapter ──> response stream
                        │                    │                          │
                        │                    ├──> Qdrant + FastEmbed ───┤
                        │                    └──> Mem0 (Qdrant/Ollama)  │
                        ├──> PostgreSQL audit / JSONL mirror
                        ├──> Redis rate limiter
                        └──> Langfuse traces
```

See [docs/full-stack.md](docs/full-stack.md) for the applied stack, [docs/b300-vllm.md](docs/b300-vllm.md) for the remote B300 GPU 7 vLLM test, [docs/b300-host-deploy.md](docs/b300-host-deploy.md) for the full platform on B300, [docs/research/strata-and-local-ai-shortlist.md](docs/research/strata-and-local-ai-shortlist.md) for the Strata/local-AI research, [docs/research/clawfit-positioning.md](docs/research/clawfit-positioning.md) for the clawfit comparison and registration strategy, [docs/research/clawfit/](docs/research/clawfit/) for the clawfit research-watch draft and registry entry proposal, [docs/features.md](docs/features.md) for the complete feature inventory, [docs/open-source.md](docs/open-source.md) for the open-source component and license inventory, and [docs/architecture.md](docs/architecture.md), [docs/operations.md](docs/operations.md), and [docs/security.md](docs/security.md) for the intended production integration boundaries.

## API highlights

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness and configured adapter status |
| `GET /v1/models` | Models visible to the caller |
| `POST /v1/chat/completions` | OpenAI-style chat completion, including SSE streaming |
| `POST /v1/documents` | Upload a UTF-8 text/Markdown document |
| `GET /v1/documents` | List documents visible to the caller |
| `DELETE /v1/documents/{id}` | Delete a document as its uploader or an administrator |
| `PATCH /v1/documents/{id}` | Update a document's role ACL as an administrator |
| `GET /v1/documents/search` | Permission-scoped retrieval results |
| `POST /v1/memories` | Store a user memory with Mem0 |
| `GET /v1/memories` | List the caller's memories |
| `GET /v1/memories/search` | Semantic memory search |
| `DELETE /v1/memories/{id}` | Delete the caller's memory |
| `GET /v1/audit-events` | Recent audit events for administrators |
| `POST /v1/admin/reindex` | Reindex documents into Qdrant (administrator) |
| `GET /v1/admin/settings` | Effective runtime, storage, policy, and integration settings for administrators |

For local development, callers can send `X-User-Id`, `X-Organization-Id`, and `X-Roles` headers. Replace this development identity adapter with OIDC/SSO before production use.

## Repository layout

```text
apps/api/       FastAPI control plane and API
apps/web/       React + Vite chat UI
deploy/         Compose and Kubernetes deployment assets
docs/           Architecture, operations, security, full-stack, B300 deployment, research, inventory
scripts/        Ollama proxy, B300 vLLM serve/tunnel, full-stack smoke
```

## Development

```bash
cd apps/api && python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest

cd ../web
npm install
npm run build
```

API를 호스트에서 실행할 때 `.env`를 명시적으로 로드합니다.

```bash
uvicorn baryon.main:app --app-dir apps/api/src --host 127.0.0.1 --port 8000 --env-file .env
```

### End-to-end test

Start the API on port 8000 and the web dev server on port 5173, then:

```bash
cd apps/web
npx playwright install chromium   # first run only
npm run test:e2e
```

Set `E2E_BASE_URL` to test another deployment (for example `http://127.0.0.1:3001` for the Compose web service), and `E2E_SCREENSHOT=/tmp/baryon-ax.png` to capture a full-page screenshot.

### Full-stack smoke

```bash
API_BASE=http://127.0.0.1:8000 REQUIRE_FULL=1 python3 scripts/smoke_full_stack.py
```

The smoke check covers health backends, document upload, Qdrant retrieval, chat, audit, and Mem0 memory.

## Security status

This is a reference implementation, not a security certification. The development identity headers, local JSON files, and JSONL audit mirror are development conveniences. The full-stack profile adds PostgreSQL, Qdrant, Redis, Langfuse, and Mem0, but OIDC/JWT, security headers, and a complete retention policy are still required for production. Follow [docs/security.md](docs/security.md) before exposing a deployment to users or confidential data.

## License

MIT. See [LICENSE](LICENSE).
