# Kubernetes starter manifests

These files deploy only the stateless API. Supply a real image reference and a `baryon-api-config` Secret through your deployment system. Run PostgreSQL, Redis, Qdrant/Milvus, object storage, model serving, and observability using the organization’s approved HA services or charts.

The starter deployment mounts `emptyDir` volumes for documents, audit, and runtime caches so it can run without extra infrastructure. Replace them with persistent volumes or managed storage before relying on the deployment; `emptyDir` data disappears when a pod is replaced.

For the full stack, inject `DATABASE_URL`, `REDIS_URL`, `QDRANT_URL`, `MEM0_ENABLED`, `MEM0_HISTORY_DB`, `OLLAMA_BASE_URL`, `LANGFUSE_*`, and model keys through the `baryon-api-config` Secret. Mount `/data/fastembed` and `/data/mem0` on persistent volumes so model caches and memory history survive pod replacement.

```bash
kubectl apply -f namespace.yaml
kubectl apply -f api.yaml
```

Do not apply a plaintext secret manifest to a shared cluster. Use an external secret operator, sealed secrets, or your approved secret-injection method.
