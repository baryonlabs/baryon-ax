# Architecture

Baryon AX keeps product responsibilities separate even when a development profile runs them together.

| Layer | Current implementation | Production replacement point |
| --- | --- | --- |
| UI | React/Vite chat workspace | SSO-aware enterprise frontend |
| Execution | FastAPI gateway and model adapter | LangGraph workflows + durable checkpointer |
| Knowledge | permission-filtered lexical retrieval with durable document files | LlamaIndex ingestion + Qdrant or Milvus |
| Governance | policy engine and audit schema | NeMo/Llama Guard, OPA, Langfuse |
| Infrastructure | Docker Compose services | PostgreSQL, Redis, Qdrant, object storage, Kubernetes |

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

1. Add OIDC/JWT validation and persist organizations, users, roles, threads, and audit events in PostgreSQL.
2. Replace `DocumentService` with LlamaIndex parsing/embeddings and Qdrant collections filtered by organization and ACL metadata.
3. Introduce LangGraph state/checkpoint persistence for tool loops, retries, approval interrupts, and resumable runs.
4. Export OpenTelemetry/Langfuse traces and promote policy decisions to structured governance events.
