"""OpenAI-compatible gateway for Amazon Bedrock.

Baryon AX talks to any OpenAI-compatible endpoint (``MODEL_BASE_URL`` for chat,
``EMBEDDING_BASE_URL`` with ``EMBEDDING_PROVIDER=openai`` for embeddings). This
small service translates those calls to Bedrock so the platform runs on AWS
without code changes and without long-lived AWS keys: it uses the ECS task role.

Endpoints
    GET  /health
    GET  /v1/models
    POST /v1/chat/completions   (stream and non-stream; Bedrock Converse API)
    POST /v1/embeddings         (Titan Text Embeddings V2 or Cohere Embed)

Configuration (environment)
    GATEWAY_API_KEY        required bearer key (the API sends it as MODEL_API_KEY)
    CHAT_MODEL_ID          Bedrock model or inference-profile id for answers
    EMBEDDING_MODEL_ID     Bedrock embedding model id
    EMBEDDING_DIMENSION    output vector size (Titan V2: 256, 512 or 1024)
    CHAT_ALIAS             public name of the chat model (default "baryon-chat")
    EMBEDDING_ALIAS        public name of the embedding model (default "baryon-embed")
    MAX_OUTPUT_TOKENS      default answer length cap (default 2048)
    AWS_REGION             Bedrock region (set by ECS)
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
import uuid
from typing import Any, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("bedrock-gateway")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

API_KEY = os.getenv("GATEWAY_API_KEY", "")
CHAT_MODEL_ID = os.getenv("CHAT_MODEL_ID", "apac.amazon.nova-lite-v1:0")
EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
CHAT_ALIAS = os.getenv("CHAT_ALIAS", "baryon-chat")
EMBEDDING_ALIAS = os.getenv("EMBEDDING_ALIAS", "baryon-embed")
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "2048"))

app = FastAPI(title="Baryon AX Bedrock gateway", docs_url=None, redoc_url=None, openapi_url=None)
_client = None

# Counters for /v1/gateway/status (same shape as the operations dashboard's /api/gateway).
STATS: dict[str, Any] = {"requests": 0, "failed": 0, "ok": 0, "fail": 0, "last_error": "", "last_ok_at": None}


def record(ok: bool, error: str = "") -> None:
    STATS["requests"] += 1
    if ok:
        STATS["ok"] += 1
        STATS["last_ok_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    else:
        STATS["fail"] += 1
        STATS["failed"] += 1
        STATS["last_error"] = error[:200]


def bedrock():
    """Lazily created so tests can replace it."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            config=Config(retries={"max_attempts": 4, "mode": "adaptive"}, read_timeout=300),
        )
    return _client


def require_key(authorization: str | None) -> None:
    if not API_KEY:
        raise HTTPException(503, "GATEWAY_API_KEY is not configured")
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token.encode(), API_KEY.encode()):
        raise HTTPException(401, "invalid API key")


def bedrock_error(error: Exception) -> HTTPException:
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        status = {"ThrottlingException": 429, "ValidationException": 400, "AccessDeniedException": 403,
                  "ResourceNotFoundException": 404, "ModelTimeoutException": 504}.get(code, 502)
        return HTTPException(status, f"Bedrock {code}: {error.response.get('Error', {}).get('Message', '')}"[:500])
    return HTTPException(502, f"Bedrock call failed: {error}"[:500])


# --- chat ---------------------------------------------------------------------

def _text(content: Any) -> str:
    """OpenAI content may be a string or a list of {type: text} parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def to_converse(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split OpenAI messages into Converse system blocks and alternating user/assistant turns."""
    system: list[dict] = []
    turns: list[dict] = []
    for message in messages:
        role = message.get("role")
        text = _text(message.get("content"))
        if not text:
            continue
        if role in ("system", "developer"):
            system.append({"text": text})
            continue
        role = "assistant" if role == "assistant" else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].append({"text": text})  # Converse requires alternating roles
        else:
            turns.append({"role": role, "content": [{"text": text}]})
    if not turns or turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"text": "(continue)"}]})
    return system, turns


def inference_config(body: dict) -> dict:
    config: dict[str, Any] = {"maxTokens": int(body.get("max_tokens") or body.get("max_completion_tokens") or MAX_OUTPUT_TOKENS)}
    if body.get("temperature") is not None:
        config["temperature"] = float(body["temperature"])
    if body.get("top_p") is not None:
        config["topP"] = float(body["top_p"])
    stop = body.get("stop")
    if stop:
        config["stopSequences"] = [stop] if isinstance(stop, str) else list(stop)[:4]
    return config


FINISH = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "content_filtered": "content_filter",
          "guardrail_intervened": "content_filter", "tool_use": "tool_calls"}


def usage_of(raw: dict | None) -> dict:
    raw = raw or {}
    prompt, completion = int(raw.get("inputTokens", 0)), int(raw.get("outputTokens", 0))
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(None)):
    require_key(authorization)
    body = await request.json()
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages must be a non-empty list")
    system, turns = to_converse(messages)
    kwargs: dict[str, Any] = {"modelId": CHAT_MODEL_ID, "messages": turns, "inferenceConfig": inference_config(body)}
    if system:
        kwargs["system"] = system
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model = body.get("model") or CHAT_ALIAS

    if not body.get("stream"):
        try:
            response = bedrock().converse(**kwargs)
        except (ClientError, BotoCoreError) as error:
            mapped = bedrock_error(error)
            record(False, str(mapped.detail))
            raise mapped from error
        record(True)
        text = "".join(block.get("text", "") for block in response.get("output", {}).get("message", {}).get("content", []))
        return {
            "id": completion_id, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": FINISH.get(response.get("stopReason", ""), "stop")}],
            "usage": usage_of(response.get("usage")),
        }

    try:
        response = bedrock().converse_stream(**kwargs)
    except (ClientError, BotoCoreError) as error:
        mapped = bedrock_error(error)
        record(False, str(mapped.detail))
        raise mapped from error
    record(True)
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

    def events() -> Iterator[str]:
        def chunk(delta: dict, finish: str | None = None) -> str:
            payload = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": model,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield chunk({"role": "assistant", "content": ""})
        finish, usage = "stop", None
        try:
            for event in response["stream"]:
                if "contentBlockDelta" in event:
                    text = event["contentBlockDelta"].get("delta", {}).get("text")
                    if text:
                        yield chunk({"content": text})
                elif "messageStop" in event:
                    finish = FINISH.get(event["messageStop"].get("stopReason", ""), "stop")
                elif "metadata" in event:
                    usage = usage_of(event["metadata"].get("usage"))
        except (ClientError, BotoCoreError) as error:
            logger.warning("stream interrupted: %s", error)
            finish = "error"
        yield chunk({}, finish)
        if include_usage and usage:
            payload = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": model,
                       "choices": [], "usage": usage}
            yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# --- embeddings -----------------------------------------------------------------

def embed_one(text: str) -> list[float]:
    if EMBEDDING_MODEL_ID.startswith("cohere."):
        raise AssertionError("cohere models are embedded in batches")
    body = {"inputText": text or " ", "dimensions": EMBEDDING_DIMENSION, "normalize": True}
    response = bedrock().invoke_model(modelId=EMBEDDING_MODEL_ID, body=json.dumps(body),
                                      contentType="application/json", accept="application/json")
    return json.loads(response["body"].read())["embedding"]


def embed_cohere(texts: list[str], input_type: str) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 96):  # Cohere Embed accepts up to 96 texts per call
        body = {"texts": [t or " " for t in texts[start:start + 96]], "input_type": input_type, "truncate": "END"}
        response = bedrock().invoke_model(modelId=EMBEDDING_MODEL_ID, body=json.dumps(body),
                                          contentType="application/json", accept="application/json")
        vectors.extend(json.loads(response["body"].read())["embeddings"])
    return vectors


@app.post("/v1/embeddings")
async def embeddings(request: Request, authorization: str | None = Header(None)):
    require_key(authorization)
    body = await request.json()
    raw = body.get("input")
    texts = [raw] if isinstance(raw, str) else list(raw or [])
    if not texts or not all(isinstance(t, str) for t in texts):
        raise HTTPException(400, "input must be a string or a list of strings")
    try:
        if EMBEDDING_MODEL_ID.startswith("cohere."):
            # The API asks for query vectors with a separate model alias; treat "*query*" as search queries.
            input_type = "search_query" if "query" in str(body.get("model", "")) else "search_document"
            vectors = embed_cohere(texts, input_type)
        else:
            vectors = [embed_one(text) for text in texts]
    except (ClientError, BotoCoreError) as error:
        raise bedrock_error(error) from error
    tokens = sum(len(t) for t in texts) // 3  # Bedrock does not report tokens for every model; rough estimate
    return {
        "object": "list", "model": body.get("model") or EMBEDDING_ALIAS,
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


# --- metadata -----------------------------------------------------------------------

@app.get("/v1/models")
async def models(authorization: str | None = Header(None)):
    require_key(authorization)
    return {"object": "list", "data": [
        {"id": CHAT_ALIAS, "object": "model", "owned_by": "bedrock", "bedrock_model_id": CHAT_MODEL_ID},
        {"id": EMBEDDING_ALIAS, "object": "model", "owned_by": "bedrock", "bedrock_model_id": EMBEDDING_MODEL_ID},
    ]}


@app.get("/v1/gateway/status")
async def gateway_status(authorization: str | None = Header(None)):
    """Fallback chain and live counters, in the contract Baryon AX shows in Settings.

    Bedrock is the only backend here; Bedrock itself retries and, with an inference
    profile, routes across regions. No key value is ever included.
    """
    require_key(authorization)
    return {
        "enabled": True, "reachable": True, "error": "",
        "endpoints": [{"label": "service-connect", "url": "http://llm-gateway:4000/v1"}],
        "models": {CHAT_ALIAS: [{"provider": "bedrock", "model": CHAT_MODEL_ID}],
                   EMBEDDING_ALIAS: [{"provider": "bedrock", "model": EMBEDDING_MODEL_ID}]},
        "providers": [{"name": "bedrock", "up": STATS["fail"] == 0 or STATS["last_ok_at"] is not None,
                       "ok": STATS["ok"], "fail": STATS["fail"], "lastError": STATS["last_error"],
                       "lastOkAt": STATS["last_ok_at"], "keyRequired": False, "keySet": False}],
        "stats": {"requests": STATS["requests"], "served": {"bedrock": STATS["ok"]}, "fallbacks": 0, "failed": STATS["failed"]},
    }


@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "chat_model": CHAT_MODEL_ID, "embedding_model": EMBEDDING_MODEL_ID,
                         "embedding_dimension": EMBEDDING_DIMENSION})
