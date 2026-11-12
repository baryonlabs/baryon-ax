FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir "uv==0.13.7" \
    && uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim

ARG PRELOAD_EMBEDDINGS=false
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/data/fastembed FASTEMBED_CACHE_PATH=/data/fastembed HF_HUB_DISABLE_XET=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
RUN mkdir -p /data/fastembed /data/documents /data/audit /data/mem0 /data/gaps /data/ontology /data/tacit /data/feedback \
    && useradd --create-home --uid 10001 baryon \
    && chown -R baryon:baryon /data \
    && if [ "$PRELOAD_EMBEDDINGS" = "true" ]; then python -c "from fastembed import TextEmbedding; model = TextEmbedding('$EMBEDDING_MODEL'); list(model.embed(['warmup']))"; fi
USER baryon
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "baryon.main:app", "--host", "0.0.0.0", "--port", "8000"]
