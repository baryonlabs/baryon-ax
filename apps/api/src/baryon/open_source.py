"""Open-source component inventory exposed to the web UI.

Kept next to the code so the Settings > Open Source screen and
docs/open-source.md can be kept in sync. Licenses are the upstream project
licenses; always verify the LICENSE file of the exact version you deploy.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

COMPONENTS: tuple[dict[str, Any], ...] = (
    # frontend
    {"id": "react", "name": "React / React DOM", "group": "frontend", "role": "UI runtime", "license": "MIT", "url": "https://github.com/facebook/react", "status": "active"},
    {"id": "vite", "name": "Vite", "group": "frontend", "role": "Build and dev server", "license": "MIT", "url": "https://github.com/vitejs/vite", "status": "active"},
    {"id": "typescript", "name": "TypeScript", "group": "frontend", "role": "Type system", "license": "Apache-2.0", "url": "https://github.com/microsoft/TypeScript", "status": "active"},
    {"id": "lucide", "name": "lucide-react", "group": "frontend", "role": "Icons", "license": "ISC", "url": "https://github.com/lucide-icons/lucide", "status": "active"},
    {"id": "react-markdown", "name": "react-markdown", "group": "frontend", "role": "Markdown rendering", "license": "MIT", "url": "https://github.com/remarkjs/react-markdown", "status": "active"},
    {"id": "remark-gfm", "name": "remark-gfm", "group": "frontend", "role": "GFM tables and task lists", "license": "MIT", "url": "https://github.com/remarkjs/remark-gfm", "status": "active"},
    {"id": "remark-breaks", "name": "remark-breaks", "group": "frontend", "role": "Line-break handling", "license": "MIT", "url": "https://github.com/remarkjs/remark-breaks", "status": "active"},
    {"id": "nginx", "name": "Nginx", "group": "frontend", "role": "Static serving and API proxy", "license": "BSD-2-Clause", "url": "https://nginx.org/", "status": "active"},
    {"id": "node", "name": "Node.js", "group": "frontend", "role": "Web build runtime", "license": "MIT", "url": "https://github.com/nodejs/node", "status": "active"},
    # backend
    {"id": "python", "name": "Python", "group": "backend", "role": "API runtime", "license": "PSF-2.0", "url": "https://www.python.org/", "status": "active"},
    {"id": "fastapi", "name": "FastAPI", "group": "backend", "role": "HTTP API framework", "license": "MIT", "url": "https://github.com/fastapi/fastapi", "status": "active"},
    {"id": "uvicorn", "name": "Uvicorn", "group": "backend", "role": "ASGI server", "license": "BSD-3-Clause", "url": "https://github.com/encode/uvicorn", "status": "active"},
    {"id": "pydantic", "name": "Pydantic", "group": "backend", "role": "Request and response validation", "license": "MIT", "url": "https://github.com/pydantic/pydantic", "status": "active"},
    {"id": "httpx", "name": "HTTPX", "group": "backend", "role": "Model server HTTP client", "license": "BSD-3-Clause", "url": "https://github.com/encode/httpx", "status": "active"},
    {"id": "psycopg", "name": "psycopg", "group": "backend", "role": "PostgreSQL driver", "license": "LGPL-3.0", "url": "https://github.com/psycopg/psycopg", "status": "active"},
    {"id": "redis-py", "name": "redis-py", "group": "backend", "role": "Redis client", "license": "MIT", "url": "https://github.com/redis/redis-py", "status": "active"},
    {"id": "psutil", "name": "psutil", "group": "backend", "role": "System metrics", "license": "BSD-3-Clause", "url": "https://github.com/giampaolo/psutil", "status": "active"},
    {"id": "docker-sdk", "name": "Docker SDK for Python", "group": "backend", "role": "Container status cards", "license": "Apache-2.0", "url": "https://github.com/docker/docker-py", "status": "active"},
    # data and retrieval
    {"id": "qdrant", "name": "Qdrant", "group": "data", "role": "Vector database", "license": "Apache-2.0", "url": "https://github.com/qdrant/qdrant", "status": "active"},
    {"id": "qdrant-client", "name": "qdrant-client", "group": "data", "role": "Vector client", "license": "Apache-2.0", "url": "https://github.com/qdrant/qdrant-client", "status": "active"},
    {"id": "fastembed", "name": "FastEmbed", "group": "data", "role": "Multilingual embeddings", "license": "Apache-2.0", "url": "https://github.com/qdrant/fastembed", "status": "active"},
    {"id": "onnxruntime", "name": "ONNX Runtime", "group": "data", "role": "Embedding inference", "license": "MIT", "url": "https://github.com/microsoft/onnxruntime", "status": "active"},
    {"id": "llama-index", "name": "LlamaIndex Core", "group": "data", "role": "Sentence chunking", "license": "MIT", "url": "https://github.com/run-llama/llama_index", "status": "active"},
    {"id": "postgresql", "name": "PostgreSQL", "group": "data", "role": "Audit and metadata store", "license": "PostgreSQL", "url": "https://github.com/postgres/postgres", "status": "active"},
    {"id": "redis", "name": "Redis", "group": "data", "role": "Rate limiting and cache", "license": "BSD-3-Clause / RSALv2 / SSPLv1 / AGPLv3", "url": "https://github.com/redis/redis", "status": "active", "note": "License depends on the deployed version. Pin and verify; Valkey is a BSD alternative."},
    # orchestration and observability
    {"id": "langgraph", "name": "LangGraph", "group": "orchestration", "role": "Policy and retrieval graph", "license": "MIT", "url": "https://github.com/langchain-ai/langgraph", "status": "active"},
    {"id": "langfuse", "name": "Langfuse", "group": "observability", "role": "LLM tracing and evaluation", "license": "MIT (core) + EE", "url": "https://github.com/langfuse/langfuse", "status": "active", "note": "The /ee directory is commercially licensed; core self-hosting is MIT."},
    {"id": "mem0", "name": "Mem0", "group": "observability", "role": "User memory", "license": "Apache-2.0", "url": "https://github.com/mem0ai/mem0", "status": "active"},
    {"id": "ollama", "name": "Ollama", "group": "model", "role": "Local model runtime", "license": "MIT", "url": "https://github.com/ollama/ollama", "status": "active"},
    {"id": "vllm", "name": "vLLM", "group": "model", "role": "GPU model serving", "license": "Apache-2.0", "url": "https://github.com/vllm-project/vllm", "status": "active"},
    # ingestion
    {"id": "kordoc", "name": "kordoc", "group": "ingestion", "role": "HWP/HWPX/HWPML parser sidecar", "license": "MIT", "url": "https://github.com/chrisryugj/kordoc", "status": "active"},
    {"id": "docling", "name": "docling", "group": "ingestion", "role": "PDF/Office/image/HTML parser sidecar", "license": "MIT", "url": "https://github.com/docling-project/docling", "status": "active"},
    {"id": "tika", "name": "Apache Tika", "group": "ingestion", "role": "Legacy Office/RTF/ZIP fallback", "license": "Apache-2.0", "url": "https://github.com/apache/tika", "status": "optional"},
    # infrastructure and tooling
    {"id": "docker", "name": "Docker Engine (Moby)", "group": "infra", "role": "Container runtime", "license": "Apache-2.0", "url": "https://github.com/moby/moby", "status": "active", "note": "Docker Desktop has separate commercial terms."},
    {"id": "kubernetes", "name": "Kubernetes", "group": "infra", "role": "Deployment manifests", "license": "Apache-2.0", "url": "https://github.com/kubernetes/kubernetes", "status": "optional"},
    {"id": "playwright", "name": "Playwright", "group": "dev", "role": "Browser E2E tests", "license": "Apache-2.0", "url": "https://github.com/microsoft/playwright", "status": "test"},
    {"id": "pytest", "name": "pytest", "group": "dev", "role": "Python tests", "license": "MIT", "url": "https://github.com/pytest-dev/pytest", "status": "test"},
    {"id": "security-sweep", "name": "security-sweep", "group": "dev", "role": "PR vulnerability and secret scan", "license": "MIT", "url": "https://github.com/baryonlabs/security-sweep", "status": "test"},
    # optional alternatives
    {"id": "milvus", "name": "Milvus", "group": "data", "role": "Vector database alternative", "license": "Apache-2.0", "url": "https://github.com/milvus-io/milvus", "status": "optional"},
    {"id": "valkey", "name": "Valkey", "group": "data", "role": "BSD Redis alternative", "license": "BSD-3-Clause", "url": "https://github.com/valkey-io/valkey", "status": "optional"},
    {"id": "litellm", "name": "LiteLLM", "group": "model", "role": "Model gateway alternative", "license": "MIT", "url": "https://github.com/BerriAI/litellm", "status": "optional"},
    {"id": "sglang", "name": "SGLang", "group": "model", "role": "High-throughput serving alternative", "license": "Apache-2.0", "url": "https://github.com/sgl-project/sglang", "status": "optional"},
)


def open_source_payload() -> dict[str, Any]:
    counts = Counter(component["license"].split(" ")[0] for component in COMPONENTS)
    return {
        "data": list(COMPONENTS),
        "summary": {
            "total": len(COMPONENTS),
            "active": sum(1 for component in COMPONENTS if component["status"] == "active"),
            "optional": sum(1 for component in COMPONENTS if component["status"] == "optional"),
            "test": sum(1 for component in COMPONENTS if component["status"] == "test"),
            "licenses": dict(counts.most_common()),
        },
        "docs_url": "https://github.com/baryonlabs/baryon-ax/blob/main/docs/open-source.md",
        "notice": "Licenses are reported from the upstream projects. Verify the LICENSE of the exact version you deploy, especially for source-available licenses and model weights.",
    }
