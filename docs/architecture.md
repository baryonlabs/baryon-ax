# Architecture

Baryon AX keeps product responsibilities separate even when a development profile runs them together.

| Layer | Current implementation | Production replacement point |
| --- | --- | --- |
| UI | React/Vite chat workspace | SSO-aware enterprise frontend |
| Execution | FastAPI gateway + LangGraph policy/retrieval graph | Durable LangGraph checkpoints and tool loops |
| Knowledge | Qdrant + FastEmbed retrieval, LlamaIndex chunking, lexical fallback | Milvus, hybrid search, richer parsing |
| Governance | PolicyEngine, PostgreSQL/JSONL audit, Langfuse traces | NeMo/Llama Guard, OPA, retention policy |
| Infrastructure | Docker Compose Basic Core + full-stack override | HA PostgreSQL, Redis, Qdrant, object storage, Kubernetes |

## Compose vs. target architecture

The overview image is a **target production architecture**, not a one-to-one container map. Several L2/L3 boxes are libraries or in-process adapters today; only the UI, API, and stateful infrastructure need containers.

| Diagram component | Layer | Current implementation | Status |
| --- | --- | --- | --- |
| Chat Web UI | L4 | `web` (React/Nginx) | ✅ |
| FastAPI gateway | L3 | `api` | ✅ |
| LangGraph | L3 | in-process policy/retrieval graph | ✅ |
| Guardrails | L3 | in-process `PolicyEngine` | 🔶 NeMo/Guardrails AI optional |
| Langfuse | L3 | host Langfuse v2 + SDK | ✅ |
| Qdrant | L2 | `qdrant` (`deps` profile) + FastEmbed | ✅ |
| Milvus | L2 | — | ⛔ Qdrant alternative |
| LlamaIndex | L2 | sentence splitter in the API | ✅ |
| Redis | L2 | `redis` (`deps`) fixed-window limiter | ✅ |
| Mem0 | L2 | in-process Mem0 + Qdrant + Ollama | ✅ |
| PostgreSQL | L1 | `postgres` (`deps`) audit table | ✅ |
| Ollama / model serving | L1 | host Ollama + B300 GPU 7 vLLM | ✅ |
| vLLM / SGLang | L1 | vLLM on B300 GPU 7; SGLang alternative | ✅ vLLM / ⛔ SGLang |
| Docker | L1 | `api`, `web`, data services | ✅ |
| Kubernetes | L1 | `deploy/kubernetes` (API only) | 🔶 starter manifests |

The Basic Core profile starts only `api` and `web` with the `demo-local` model. The full-stack override connects the data services and host services:

```bash
docker compose up --build                                  # Basic Core
python3 scripts/ollama_proxy.py --listen-port 11435 &      # Docker Desktop -> loopback Ollama
OLLAMA_HOST_URL=http://host.docker.internal:11435 \
  docker compose -f docker-compose.yml -f docker-compose.full.yml --profile deps up -d --build
```

See [full-stack.md](full-stack.md) for prerequisites and verification.

## Trust boundaries

1. The browser never talks directly to a model, vector store, or tool.
2. Identity is resolved at the gateway. The development header adapter is deliberately not trusted for production.
3. Retrieval applies organization and role filters **before** a chunk can enter model context.
4. Tool proposals must be authorized server-side; a model response alone is never execution authority.
5. `trace_id` connects chat, retrieval, policy, and audit records. Production adapters should propagate it to LangGraph and Langfuse.

## Model adapters

The gateway uses OpenAI-compatible `/chat/completions`. This permits vLLM, SGLang, LiteLLM gateways, and many internal inference services without coupling UI code to any one provider. If `MODEL_BASE_URL` is unset, `demo-local` returns an explicit non-inference response for smoke testing.

When streaming is requested, the gateway forwards upstream server-sent events token by token. Policy output masks are applied with a small holdback buffer so a phone-number-like value is not split across chunks. Audit events are appended to a JSONL sink (`AUDIT_LOG`) and filtered by organization when read through `/v1/audit-events`.

## Next production increments

1. Add OIDC/JWT validation and persist organizations, users, roles, threads, and messages in PostgreSQL.
2. Add durable LangGraph checkpoints, tool execution, approval interrupts, and resumable runs.
3. Add richer Guardrails (NeMo/Guardrails AI) and organization-specific policy rules.
4. Evaluate Milvus/hybrid search, vLLM/SGLang/LiteLLM, object storage, and managed HA services for scale.
