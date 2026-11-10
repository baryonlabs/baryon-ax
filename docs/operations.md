# Operations guide

## Deployment profiles

| Profile | Intended use | Required services |
| --- | --- | --- |
| Basic Core | reference implementation and demos | Compose `api` + `web` (default), `demo-local` |
| Full stack | applied local stack | `docker-compose.full.yml` + `--profile deps`, host Ollama/Langfuse |
| Local dependencies | integration work | `--profile deps` adds PostgreSQL, Redis, Qdrant |
| Enterprise | internal users and data | GPU inference, HA PostgreSQL, Redis, Qdrant, object storage, OIDC, Langfuse |
| Scalable | HA / multi-node | Kubernetes, managed/HA data stores, monitoring, backups |

## Local startup

### Basic Core

1. Copy `.env.example` to `.env`.
2. Run `docker compose up --build`. The default profile starts the API and web UI only with `demo-local`.
3. Visit `http://localhost:3001`.

### Full stack

1. Ensure Ollama has `qwen2.5:7b` and Langfuse is running on `http://127.0.0.1:3000`.
2. Expose loopback Ollama to Docker Desktop:
   `python3 scripts/ollama_proxy.py --listen-port 11435 &`
3. Start the full stack:
   `OLLAMA_HOST_URL=http://host.docker.internal:11435 docker compose -f docker-compose.yml -f docker-compose.full.yml --profile deps up -d --build`
4. Verify with `API_BASE=http://127.0.0.1:8000 REQUIRE_FULL=1 python3 scripts/smoke_full_stack.py`.
5. Change `POSTGRES_PASSWORD`; do not reuse the example value outside a laptop.

Ports: web `3001`, API `8000`, Langfuse `3000`, PostgreSQL `5433`, Redis `6379`, Qdrant `6333/6334`, Ollama `11434`, Ollama proxy `11435`.

### Remote vLLM (B300 GPU 7)

For a remote inference server, start vLLM on the B300 GPU 7 and forward its loopback port:

```bash
scripts/b300_vllm_tunnel.sh &
OLLAMA_HOST_URL=http://host.docker.internal:11435 \
MODEL_HOST_URL=http://host.docker.internal:8020 \
MODEL_NAME=baryon-ax-qwen \
MODEL_DISABLE_THINKING=true \
MEM0_LLM_MODEL=qwen2.5:7b \
  docker compose -f docker-compose.yml -f docker-compose.full.yml --profile deps up -d --build
```

The tunnel binds `0.0.0.0:8020` for Docker Desktop. Use it only on a trusted network and stop it after the test. See [b300-vllm.md](b300-vllm.md) for vLLM start/stop and verification.

## Backups

Back up PostgreSQL audit events, Qdrant storage, the original document/object store, Mem0 history, and the JSONL audit mirror (`AUDIT_LOG`, `/data/audit/events.jsonl` in Compose) as a coordinated set. The full stack adds the `postgres`, `qdrant`, `mem0`, and `models` volumes. Test restore procedures with an isolated environment. Conversation/audit data and vector indexes have different recovery characteristics; do not treat a vector snapshot as the system of record for original documents.

## Air-gapped deployments

Mirror container images, Python/npm artifacts, model weights, embedding models (FastEmbed/HuggingFace), Ollama models, and OS packages into an internal registry before disconnecting. Set an explicit egress policy for the model service and tools. Do not configure external model endpoints in an air-gapped profile.
