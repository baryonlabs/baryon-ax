# Security baseline

Before production use, implement these controls:

- Replace development identity headers with validated OIDC/OAuth2 tokens and enforce RBAC at every API boundary.
- Store secrets in an approved secret manager; never place live API keys in Compose files, images, or browser code.
- Encrypt transport, database, object store, and backup paths according to organizational policy.
- Enforce tenant and document ACL filters in the retrieval query itself, not after results are returned. The Qdrant path applies organization/role/owner filters and rechecks them after retrieval.
- Return only citation metadata (document ID, title, and chunk number) to the browser; do not echo raw retrieved content back to the client.
- Keep document ACL changes administrator-only and emit an audit event for every change.
- Apply tool allowlists, parameter schemas, time limits, network egress rules, idempotency keys, and human approval for impactful actions.
- Use policy events and audit records that exclude secrets and minimize sensitive prompt retention.
- PostgreSQL audit events and the JSONL mirror are applied, but neither provides tamper evidence or a retention policy. Add append-only/WORM storage, signing, and organization-level retention before production.
- A Redis fixed-window rate limit is applied per user and organization. Add CSRF strategy where cookie authentication is used, security headers, dependency scanning, distributed rate limits, and regular restore tests.
- Remote vLLM is bound to the B300 loopback and reached through an SSH tunnel. Keep the tunnel on a trusted network, restrict `0.0.0.0:8020`, and terminate it after testing.
- Langfuse traces, Mem0 memories, Qdrant payloads, and audit records may contain prompts or PII. Configure redaction, retention, deletion, and encryption for each store.

The included phone-number mask is illustrative only. It is not a complete PII detector or a substitute for a security review.
