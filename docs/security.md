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
- Chat Markdown is rendered with `react-markdown` without raw HTML (`rehype-raw` disabled), and links open with `rel="noreferrer"`. Do not enable raw HTML without sanitization.
- `SYSTEM_METRICS_ENABLED=true` mounts the Docker socket read-only into the API container to read container status and ports. A Docker socket is effectively root-equivalent: any process that can use it can start privileged containers. Keep it read-only, restrict it to trusted hosts, set `SYSTEM_METRICS_ENABLED=false` where the socket must not be shared, and replace this with a scoped metrics sidecar before multi-tenant production.
- The API also mounts `/proc` read-only at `/host/proc` to report the host process count. It is used only to count numeric PID directories; do not expose this path to untrusted code.

The included phone-number mask is illustrative only. It is not a complete PII detector or a substitute for a security review.
