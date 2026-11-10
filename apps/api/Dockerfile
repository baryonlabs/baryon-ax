FROM python:3.12-slim
ARG PRELOAD_EMBEDDINGS=false
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/data/fastembed FASTEMBED_CACHE_PATH=/data/fastembed HF_HUB_DISABLE_XET=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /data/fastembed \
    && if [ "$PRELOAD_EMBEDDINGS" = "true" ]; then python -c "from fastembed import TextEmbedding; model = TextEmbedding('$EMBEDDING_MODEL'); list(model.embed(['warmup']))"; fi \
    && useradd --create-home --uid 10001 baryon \
    && mkdir -p /data/documents /data/audit /data/mem0 \
    && chown -R baryon:baryon /data
USER baryon
EXPOSE 8000
CMD ["uvicorn", "baryon.main:app", "--host", "0.0.0.0", "--port", "8000"]
