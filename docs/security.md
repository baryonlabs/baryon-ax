# Security baseline

Before production use, implement these controls:

- Replace development identity headers with validated OIDC/OAuth2 tokens and enforce RBAC at every API boundary.
- Store secrets in an approved secret manager; never place live API keys in Compose files, images, or browser code.
- Encrypt transport, database, object store, and backup paths according to organizational policy.
- Enforce tenant and document ACL filters in the retrieval query itself, not after results are returned.
- Apply tool allowlists, parameter schemas, time limits, network egress rules, idempotency keys, and human approval for impactful actions.
- Use policy events and audit records that exclude secrets and minimize sensitive prompt retention.
- Replace the local JSONL audit sink with append-only storage that enforces retention, integrity, and organization-level access controls; the reference sink has no tamper protection.
- Add rate limits, CSRF strategy where cookie authentication is used, security headers, dependency scanning, and regular restore tests.

The included phone-number mask is illustrative only. It is not a complete PII detector or a substitute for a security review.
