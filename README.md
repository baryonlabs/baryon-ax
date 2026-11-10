# Baryon AX

**Baryon AX** is an on-premises, model-agnostic foundation for enterprise AI agents and grounded chat.

> Connect models. Orchestrate agents. Ground knowledge. Observe everything.

This repository is an executable Basic Core v0.6 reference implementation. It offers a small but complete vertical slice: role-aware chat, OpenAI-compatible model routing, document ingestion and retrieval with citations, policy checks, audit events, and a browser UI. It is intentionally modular: production deployments can swap the development retrieval implementation for Qdrant, the demo model for vLLM/Ollama, and the in-process audit sink for PostgreSQL + Langfuse.

## Platform at a glance

![Baryon AX platform overview: user flow, layered architecture, and core engines](docs/images/baryon-ax-platform-overview.png)

The platform brings a chat workspace, agent execution, grounded knowledge search, and on-premises infrastructure into one operating model. The image summarizes the user flow—ask, run an agent, evaluate, and review evidence—and the responsibilities of each layer.

## What works today

- Chat UI with model selection and streaming responses
- Korean-first bilingual UI (한국어 / English), with browser-language detection and persisted preference
- OpenAI-compatible model adapter (`MODEL_BASE_URL`) with a safe local demo fallback
- Input/output policy checks and basic PII masking
- Durable document upload, chunking, permission-scoped lexical retrieval, and source citations
- JSON audit events that link request, thread, model, and tool/retrieval activity
- Docker Compose services for API, UI, PostgreSQL, Redis, and Qdrant
- Kubernetes starter manifests and deployment profiles

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:3000`. The default `demo-local` model works without an API key. To use a local OpenAI-compatible server (such as vLLM), set `MODEL_BASE_URL`, `MODEL_NAME`, and if required `MODEL_API_KEY` in `.env`.

## Architecture

```text
Browser UI ──> FastAPI gateway ──> policy gate ──> model adapter ──> response stream
                       │                 │                 │
                       ├──> document service / retrieval ──┘
                       ├──> audit events (trace_id)
                       └──> PostgreSQL / Redis / Qdrant (deployment services)
```

See [docs/architecture.md](docs/architecture.md), [docs/operations.md](docs/operations.md), and [docs/security.md](docs/security.md) for the intended production integration boundaries.

## API highlights

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness and configured adapter status |
| `GET /v1/models` | Models visible to the caller |
| `POST /v1/chat/completions` | OpenAI-style chat completion, including SSE streaming |
| `POST /v1/documents` | Upload a UTF-8 text/Markdown document |
| `GET /v1/documents` | List documents visible to the caller |
| `DELETE /v1/documents/{id}` | Delete a document as its uploader or an administrator |
| `GET /v1/documents/search` | Permission-scoped retrieval results |
| `GET /v1/audit-events` | Recent audit events for administrators |

For local development, callers can send `X-User-Id`, `X-Organization-Id`, and `X-Roles` headers. Replace this development identity adapter with OIDC/SSO before production use.

## Repository layout

```text
apps/api/       FastAPI control plane and API
apps/web/       React + Vite chat UI
deploy/         Compose and Kubernetes deployment assets
docs/           Architecture, operations, and security guidance
```

## Development

```bash
cd apps/api && python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest

cd ../web
npm install
npm run build
```

## Security status

This is a reference implementation, not a security certification. The default identity headers, local document store, lexical retrieval, and JSON audit sink are development conveniences. Follow [docs/security.md](docs/security.md) before exposing a deployment to users or confidential data.

## License

MIT. See [LICENSE](LICENSE).
