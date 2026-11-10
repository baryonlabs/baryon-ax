# Research Watch: Baryon AX — Governed On-Premises Knowledge & Chat Platform

- Repo/Link: `baryonlabs` (Baryon Labs, Seoul) — repository not yet public; local Git history only as of 2026-11-11
- Related: [`찰떡AI / Chaltteok`](https://chaltteok-app.baryon.ai/) (L6b, same org, tracked 2026-05-05)
- Source: internal submission (Baryon Labs)

## Why this is worth watching

clawfit's registry has no entry that closes **retrieval, policy, audit, tenancy, observability, and memory inside a single self-hosted deployment**. The 107 registry entries are mostly single-layer components (Qdrant, Langfuse, Mem0, Qwen, guardrail libraries), and the two closest platform-shaped tools sit at opposite edges: AnythingLLM covers document chat plus multi-user permissions but treats governance as configuration, while LibreChat covers the L6 interface, MCP, and RBAC but was excluded from the registry because a self-hosted multi-provider platform has no single `cost_per_call`/`latency` data point and no matching `provider router` category.

Baryon AX is the first tracked tool in this corpus whose **product claim is the governance layer itself**: organization and role filters are applied inside the retrieval query, citations return metadata only (never retrieved text), ACL changes and client cancellations are audit events, and traces land in Langfuse with user/org/model metadata. It is also a **reference implementation** rather than a product: the repository is explicitly a v0.6 "Basic Core" vertical slice with a Compose profile and a full-stack override, which makes its architecture inspectable in a way that commercial platforms are not.

The pairing with Chaltteok matters for the taxonomy: same organization, two knowledge-maintenance models — **L6b on the device** (LLM maintains a local graph for one SMB user) and **L6a on the server** (retrieval maintains organizational knowledge behind ACLs). No existing L6a or L6b entry documents that split as a deliberate portfolio.

## What stands out immediately

- **Governance as a first-class surface, not an add-on.** `PolicyEngine` runs input/output checks with PII masking that is safe across streaming chunk boundaries; document ACLs are enforced before a chunk can enter model context and rechecked after retrieval; every ACL change, cancellation, and blocked access emits an audit event.
- **Citation discipline.** The browser receives document ID, title, and chunk number only. Raw retrieved content is never echoed to the client — a stricter posture than the chat-with-sources pattern in most self-hosted RAG tools.
- **Audit with a dual sink.** PostgreSQL `audit_events` plus an append-only JSONL mirror, queryable by administrators through `/v1/audit-events`. The docs are explicit that this is not tamper-evident yet (no WORM, no signing, no retention policy) — an honest gap rather than a marketing claim.
- **Model-agnostic routing with model-specific prompt profiles.** A single OpenAI-compatible adapter, verified against Ollama `qwen2.5:7b` and vLLM `Qwen3.5-27B` on B300 GPU 7 over an SSH tunnel, plus a `demo-local` fallback. `MODEL_PROMPT_PROFILE` (`qwen35` / `qwen` / `generic`) and `MODEL_DISABLE_THINKING` encapsulate per-model behaviour — a capability that LibreChat-style routers do not expose as policy.
- **Measured on-premises GPU deployment.** 27B BF16 on a single B300 GPU (246,030 MiB / 275,040 MiB at `gpu-memory-utilization=0.90`), non-streaming chat ≈ 0.50 s, streaming ≈ 0.58 s for 39 tokens, Langfuse trace latency ≈ 0.53 s. A 122B-A10B model was also validated on the same GPU (0.14–3.47 s depending on cache state). This is the kind of deterministic deployment evidence clawfit's hardware × LLM axis currently lacks.
- **Korean-first bilingual UI.** Korean by default with browser-language detection, persisted preference, and prompts that force Korean output and refuse to hallucinate organization facts when retrieval is empty.
- **Full stack is composed, not vendored.** Qdrant + FastEmbed (multilingual MiniLM), LlamaIndex sentence chunking, LangGraph policy/retrieval graph, FastEmbed, Mem0 (Qdrant + Ollama), PostgreSQL audit, Redis rate limiting, Langfuse tracing — with documented replacement points (Milvus, NeMo/Llama Guard, OPA, HA PostgreSQL) rather than a single-vendor lock.

## Why clawfit should care

**Taxonomy gap at L6a + L3.** clawfit's L6a section lists retrieval infrastructure (MinerU, LightRAG, PageIndex, RAG-Anything, CocoIndex) — all ingestion/retrieval engines with no tenancy or audit model. L3 governance entries are harness-side (hooks, permission tiers, policy DSLs), not knowledge-side. Baryon AX occupies the intersection: **retrieval-native knowledge infrastructure whose access control and audit trail are part of the retrieval path**. If the `governed_ai_platform` category is created, this is a defensible anchor.

**Registry schema gap — the LibreChat problem, restated.** Baryon AX cannot supply `cost_per_call` or a single `latency` number; it is self-hosted with a user-selected backend. But unlike LibreChat it can supply a **deterministic GPU-hour cost model**:

```
cost_per_1k_tokens($) = C_gpu_per_hour / (3.6 × T_tokens_per_sec)
```

with `C_gpu_per_hour` as an org accounting input and `T_tokens_per_sec` as a measured median sustained throughput. The repository already publishes measured latency and VRAM footprint, so a benchmark run is a documentation task, not a research task. This is a concrete path for clawfit to admit self-hosted platforms without weakening the "deterministic cost" rule — and a different mechanism from LibreChat's (provider-routed, no fixed hardware).

**`data_sensitivity: confidential` + `governance_need: hard` profile.** clawfit repeatedly notes thin coverage for this cell (WeKnora 2026-09-17 was the last addition). Baryon AX adds a whole-deployment answer: no data egress by default, org-scoped retrieval, PII masking, audit trail, and per-user/per-org rate limiting — with the honest caveat that identity is still a development header adapter and must be replaced by OIDC.

**The Chaltteok pairing is a genuine architectural signal.** clawfit tracked Chaltteok as "domain-specific local L6b" and asked whether that is a sub-type. Baryon AX answers from the same organization: L6b for personal knowledge, L6a for organizational knowledge, both offline-capable. That is a **two-tier knowledge architecture**, not two unrelated products, and it is not currently expressible in the level taxonomy (L6a and L6b are described as alternatives, not as a portfolio).

**Scoring dimension affected — governance at the knowledge layer.** Current `features` values (`rag`, `document-ingestion`, `self-hosted`, `governance`, `audit-logs`) collapse very different postures. A `governance_enforcement: [advisory | interface | retrieval-path]` axis would separate tools that document policy from tools that enforce it before model context. Baryon AX is `retrieval-path`; LibreChat is `interface`; AnythingLLM is `advisory`.

## Preliminary interpretation

Current best reading:

- **Level 6a primary — Retrieval-native knowledge infrastructure.** Document ingestion, ACL-scoped retrieval, citation emission, and a lexical fallback are the product's core loop.
- **Level 3 secondary — Governance/policy.** PolicyEngine, audit events, ACL administration, PII masking, and rate limiting are enforced in the request path, not documented as guidance.
- **Level 7 tertiary — Human interface.** Korean-first chat workspace and administrator console (runtime, policy, storage, integration, live system cards).
- **Level 1 / L4a / L5 supporting.** OpenAI-compatible adapter (L1), Mem0 memory (L4a), Langfuse tracing plus pytest/Playwright smoke coverage (L5).

It is **not** L2 in the primary sense: LangGraph orchestrates policy and retrieval, not autonomous agent loops. There is no tool execution, no approval gate, and no MCP server — which is the single largest gap between the repository's name ("agent platform") and its shipped surface. The target architecture diagram shows agent execution; the Basic Core does not implement it. Treat agent claims as roadmap, not capability.

## Limitations and signal quality

- **Not public.** Local Git repository, no remote, 0★. clawfit's registry requires a public repo and ≥5k★; neither is met. This document is an evidence entry, not a registry promotion.
- **Not a security certification.** The repository says so explicitly: development identity headers, local JSON document store, JSONL audit mirror without tamper evidence, no OIDC/JWT, no security headers, no retention policy, and a read-only Docker socket mount for system metrics (root-equivalent). All governance claims are architectural, not audited.
- **Thin ingestion.** UTF-8 text and Markdown only. No PDF/DOCX/OCR, which is a hard gap against WeKnora and AnythingLLM.
- **No conversation persistence.** `thread_id` is accepted and audited but not stored; history lives in browser memory.
- **Latency sample is small.** 0.50–0.58 s figures come from short responses during a single verification run, not a sustained benchmark. They must not be used as throughput evidence.
- **Korean-market framing.** Most docs are Korean-first. Claims about regulatory fit are product positioning, not independent verification.
- **Same-org signal.** Chaltteok and Baryon AX share an organization, so the two-tier pattern is currently a single-org observation. clawfit's two-signal rule would require a second independent organization to confirm the portfolio pattern.

## What would constitute a promotion signal

1. **Public repository** with the reference implementation and docs (this document can then be attached to a registry PR instead of standing alone).
2. **Measured sustained throughput** (fixed prompt, 3 runs, median tok/s) → deterministic `cost_per_1k_tokens` under the GPU-hour formula.
3. **OIDC/JWT identity adapter** replacing development headers, so the governance claim survives a security review.
4. **MCP server surface** for retrieval, memory, and audit query — the minimum for comparison against AnythingLLM and LibreChat.
5. **A second independent organization** shipping the two-tier pattern (device L6b + server L6a, both offline-capable), which would promote it from observation to canonical sub-pattern.
6. **External adoption signal** — a deployment report or Korean enterprise/education reference that is not the maintainer's own.

## Status

- **Tracking: new, pre-threshold.** Classified L6a primary / L3 secondary / L7 tertiary.
- **Registry action: none today.** Blocked by (a) private repository, (b) no star count, (c) no `governed_ai_platform` category, (d) no deterministic cost point yet.
- **Queued actions:** sustained-throughput benchmark; public release; MCP surface; registry-entry proposal (`registry-entry-proposal.md`) to be attached once (a) and (d) are resolved.
- **Registry correction surfaced by this review:** `tencent-weknora` is recorded as `Apache-2.0`, but the upstream LICENSE states MIT with separately licensed third-party components. Verify and fix in `data/tools_registry.json`.
