# Baryon AX

**Baryon AX** is an on-premises, model-agnostic foundation for enterprise AI agents and grounded chat.

> Connect models. Orchestrate agents. Ground knowledge. Observe everything.

This repository is an executable Basic Core v0.6 reference implementation. It offers a small but complete vertical slice: role-aware chat, OpenAI-compatible model routing, document ingestion and retrieval with citations, policy checks, audit events, and a browser UI. It is intentionally modular: production deployments can swap the development retrieval implementation for Qdrant, the demo model for vLLM/Ollama, and the in-process audit sink for PostgreSQL + Langfuse.

## Platform at a glance

![Baryon AX platform overview: user flow, layered architecture, and core engines](docs/images/baryon-ax-platform-overview.png)

The platform brings a chat workspace, agent execution, grounded knowledge search, and on-premises infrastructure into one operating model. The image summarizes the user flow—ask, run an agent, evaluate, and review evidence—and the responsibilities of each layer. It is the **target production architecture**; the Basic Core runs the UI and API containers and implements policy, retrieval, and audit in-process. See [docs/architecture.md](docs/architecture.md#compose-vs-target-architecture) for the component-by-component mapping.

## What works today

- Chat UI with model selection and token streaming
- Keyboard-first composer: `Shift+Enter` sends, `Enter` starts a new line
- Korean-first bilingual UI (한국어 / English), with browser-language detection and persisted preference
- OpenAI-compatible model adapter (`MODEL_BASE_URL`) with end-to-end token streaming and a safe local demo fallback
- Input/output policy checks and basic PII masking, including chunk-boundary-safe streaming masks
- Durable document upload, chunking, permission-scoped lexical retrieval, and source citations
- One visibility rule shared by document listing and retrieval, scoped by organization, role, and owner
- Administrator settings view with runtime, policy, storage, and activity overview
- Inline document ACL editing for administrators, with audit events on every change
- Append-only JSONL audit events, filtered by organization and readable by administrators
- Docker Compose services for API and UI, with optional PostgreSQL, Redis, and Qdrant integration placeholders (`--profile deps`)
- Kubernetes starter manifests and deployment profiles

## Quick start

```bash
cp .env.example .env
docker compose up --build                 # Basic Core: API + UI
# docker compose --profile deps up -d     # optional: PostgreSQL, Redis, Qdrant
```

Open `http://localhost:3000`. The default `demo-local` model works without an API key. To use a local OpenAI-compatible server (such as vLLM), set `MODEL_BASE_URL`, `MODEL_NAME`, and if required `MODEL_API_KEY` in `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL_BASE_URL` | empty | OpenAI-compatible inference endpoint; empty selects `demo-local` |
| `MODEL_TIMEOUT_SECONDS` | `90` | Upstream connect/read timeout |
| `DOCUMENT_STORE` | `/tmp/baryon-ax-documents` | Directory for durable document JSON files |
| `AUDIT_LOG` | `/tmp/baryon-ax-audit/events.jsonl` | Append-only JSONL audit sink |
| `MAX_DOCUMENT_BYTES` | `2000000` | Upload size limit per document |

## Architecture

```text
Browser UI ──> FastAPI gateway ──> policy gate ──> model adapter ──> response stream
                       │                 │                 │
                       ├──> document service / retrieval ──┘
                       ├──> audit events (trace_id)
                       └──> PostgreSQL / Redis / Qdrant (deployment services)
```

See [docs/features.md](docs/features.md) for the complete feature inventory, [docs/open-source.md](docs/open-source.md) for the open-source component and license inventory, and [docs/architecture.md](docs/architecture.md), [docs/operations.md](docs/operations.md), and [docs/security.md](docs/security.md) for the intended production integration boundaries.

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
| `GET /v1/audit-events` | Recent audit events for administrators |
| `GET /v1/admin/settings` | Effective runtime, storage, policy, and activity settings for administrators |

For local development, callers can send `X-User-Id`, `X-Organization-Id`, and `X-Roles` headers. Replace this development identity adapter with OIDC/SSO before production use.

## Repository layout

```text
apps/api/       FastAPI control plane and API
apps/web/       React + Vite chat UI
deploy/         Compose and Kubernetes deployment assets
docs/           Architecture, operations, security, feature and open-source inventory
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

### End-to-end test

Start the API on port 8000 and the web dev server on port 5173, then:

```bash
cd apps/web
npx playwright install chromium   # first run only
npm run test:e2e
```

Set `E2E_BASE_URL` to test another deployment (for example the Compose web service), and `E2E_SCREENSHOT=/tmp/baryon-ax.png` to capture a full-page screenshot.

## Security status

This is a reference implementation, not a security certification. The default identity headers, local document store, lexical retrieval, and JSON audit sink are development conveniences. Follow [docs/security.md](docs/security.md) before exposing a deployment to users or confidential data.

## License

MIT. See [LICENSE](LICENSE).
