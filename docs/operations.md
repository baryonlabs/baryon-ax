# Operations guide

## Deployment profiles

| Profile | Intended use | Required services |
| --- | --- | --- |
| Basic Core | reference implementation and demos | Compose `api` + `web` (default) |
| Local dependencies | integration work | `--profile deps` adds PostgreSQL, Redis, Qdrant placeholders |
| Enterprise | internal users and data | GPU inference, PostgreSQL, Redis, Qdrant, object storage, OIDC, Langfuse |
| Scalable | HA / multi-node | Kubernetes, managed/HA data stores, monitoring, backups |

## Local startup

1. Copy `.env.example` to `.env`.
2. Run `docker compose up --build`. The default profile starts the API and web UI only.
3. Visit port 3000 and use the supplied `demo-local` model, or configure an OpenAI-compatible endpoint.
4. If you are working on an integration that needs the data-service placeholders, start them explicitly:
   `docker compose --profile deps up -d` (PostgreSQL, Redis, Qdrant).
5. When using the `deps` profile, change `POSTGRES_PASSWORD`; do not reuse the example value outside a laptop.

## Backups

Back up PostgreSQL, Qdrant storage, the original document/object store, and the append-only audit log (`AUDIT_LOG`, `/data/audit/events.jsonl` in Compose) as a coordinated set. The Basic Core writes documents and audit events to the `documents` and `audit` volumes; PostgreSQL and Qdrant are only present when the `deps` profile is enabled or when a production profile is used. Test restore procedures with an isolated environment. Conversation/audit data and vector indexes have different recovery characteristics; do not treat a vector snapshot as the system of record for original documents.

## Air-gapped deployments

Mirror container images, Python/npm artifacts, model weights, embedding models, and OS packages into an internal registry before disconnecting. Set an explicit egress policy for the model service and tools. Do not configure external model endpoints in an air-gapped profile.
