# Kubernetes starter manifests

These files deploy only the stateless API. Supply a real image reference and a `baryon-api-config` Secret through your deployment system. Run PostgreSQL, Redis, Qdrant/Milvus, object storage, model serving, and observability using the organization’s approved HA services or charts.

```bash
kubectl apply -f namespace.yaml
kubectl apply -f api.yaml
```

Do not apply a plaintext secret manifest to a shared cluster. Use an external secret operator, sealed secrets, or your approved secret-injection method.
