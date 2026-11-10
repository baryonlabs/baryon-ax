# Operations guide

## Deployment profiles

| Profile | Intended use | Required services |
| --- | --- | --- |
| Local | development and demos | Compose, `demo-local` or an allowed endpoint |
| Enterprise | internal users and data | GPU inference, PostgreSQL, Redis, Qdrant, object storage, OIDC, Langfuse |
| Scalable | HA / multi-node | Kubernetes, managed/HA data stores, monitoring, backups |

## Local startup

1. Copy `.env.example` to `.env`.
2. Change `POSTGRES_PASSWORD`; do not reuse the example value outside a laptop.
3. Run `docker compose up --build`.
4. Visit port 3000 and use the supplied `demo-local` model, or configure an OpenAI-compatible endpoint.

## Backups

Back up PostgreSQL, Qdrant storage, and the original document/object store as a coordinated set. Test restore procedures with an isolated environment. Conversation/audit data and vector indexes have different recovery characteristics; do not treat a vector snapshot as the system of record for original documents.

## Air-gapped deployments

Mirror container images, Python/npm artifacts, model weights, embedding models, and OS packages into an internal registry before disconnecting. Set an explicit egress policy for the model service and tools. Do not configure external model endpoints in an air-gapped profile.
