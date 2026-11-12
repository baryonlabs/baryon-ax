# Security baseline

Before production use, implement these controls:

- Replace development identity headers with validated OIDC/OAuth2 tokens and enforce RBAC at every API boundary.
- Store secrets in an approved secret manager; never place live API keys in Compose files, images, or browser code.
- Encrypt transport, database, object store, and backup paths according to organizational policy.
- Enforce tenant and document ACL filters in the retrieval query itself, not after results are returned. The Qdrant path applies organization/role/owner filters and rechecks them after retrieval.
- Chat responses return only citation metadata (document ID, title, and chunk number). Full text and the original uploaded file are served by `GET /v1/documents/{id}` and `GET /v1/documents/{id}/file` only after the same organization, owner, role, and administrator checks as retrieval. Do not weaken those checks or cache the content outside the permission scope.
- Keep document ACL changes administrator-only and emit an audit event for every change.
- Apply tool allowlists, parameter schemas, time limits, network egress rules, idempotency keys, and human approval for impactful actions.
- Use policy events and audit records that exclude secrets and minimize sensitive prompt retention.
- PostgreSQL audit events and the JSONL mirror are applied, but neither provides tamper evidence or a retention policy. Add append-only/WORM storage, signing, and organization-level retention before production.
- A Redis fixed-window rate limit is applied per user and organization. Add CSRF strategy where cookie authentication is used, security headers, distributed rate limits, and regular restore tests. Dependency scanning runs on every pull request and container images are scanned weekly; see [security-ci.md](security-ci.md) for the gate policy and coverage.
- Remote vLLM is bound to the B300 loopback and reached through an SSH tunnel. Keep the tunnel on a trusted network, restrict `0.0.0.0:8020`, and terminate it after testing.
- `FEEDBACK_STORE` (answer feedback) contains a short answer excerpt and the question. It is tied to a trace id and organization; apply the same retention and deletion policy as audit records.
- `TACIT_STORE` (tacit answers) contains free-text answers that may include operational detail or PII, plus attachment links. Treat it as confidential organizational knowledge: restrict access to the owning organization, apply retention, and review before publishing.
- `GAP_STORE` (knowledge gaps) and `ONTOLOGY_STORE` (pack drafts) contain question text, answer excerpts, and inferred entities. Apply the same retention, redaction, and access rules as audit records; both files are administrator-readable API surfaces.
- Langfuse traces, Mem0 memories, Qdrant payloads, and audit records may contain prompts or PII. Configure redaction, retention, deletion, and encryption for each store.
- Chat Markdown is rendered with `react-markdown` without raw HTML (`rehype-raw` disabled), and links open with `rel="noreferrer"`. Do not enable raw HTML without sanitization.
- `SYSTEM_METRICS_ENABLED=true` mounts the Docker socket read-only into the API container to read container status and ports. A Docker socket is effectively root-equivalent: any process that can use it can start privileged containers. Keep it read-only, restrict it to trusted hosts, set `SYSTEM_METRICS_ENABLED=false` where the socket must not be shared, and replace this with a scoped metrics sidecar before multi-tenant production.
- Uploaded original files are stored next to the parsed text under the document store (`raw/`). They inherit the document ACL and are deleted with the document. Include this directory in backup and retention policies, and encrypt the volume if uploads may contain confidential material.
- The API also mounts `/proc` read-only at `/host/proc` to report the host process count. It is used only to count numeric PID directories; do not expose this path to untrusted code.

The included phone-number mask is illustrative only. It is not a complete PII detector or a substitute for a security review.
