from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, AsyncIterator, Literal

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__

logger = logging.getLogger("baryon.ax")

MAX_DOCUMENT_BYTES = int(os.getenv("MAX_DOCUMENT_BYTES", "2000000"))
MODEL_TIMEOUT_SECONDS = float(os.getenv("MODEL_TIMEOUT_SECONDS", "90"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Principal:
    user_id: str
    organization_id: str
    roles: set[str]


@dataclass
class Document:
    id: str
    organization_id: str
    owner_id: str
    title: str
    content: str
    allowed_roles: set[str]
    created_at: str = field(default_factory=now)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequest(BaseModel):
    model: str = "demo-local"
    messages: list[Message] = Field(min_length=1)
    stream: bool = True
    use_retrieval: bool = True
    thread_id: str | None = None
    locale: Literal["ko", "en"] = "ko"


class DocumentUpdate(BaseModel):
    allowed_roles: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=20)


class AuditSink:
    """Append-only JSONL sink; production adapters can forward the same schema to Postgres/Langfuse."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or Path(os.getenv("AUDIT_LOG", "/tmp/baryon-ax-audit/events.jsonl"))
        self.events: deque[dict] = deque(maxlen=5000)
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        for line in self.storage_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self.events.append(event)

    def emit(self, event_type: str, principal: Principal, trace_id: str, **payload: object) -> None:
        event = {
            "id": str(uuid.uuid4()), "at": now(), "type": event_type, "trace_id": trace_id,
            "organization_id": principal.organization_id, "user_id": principal.user_id, **payload,
        }
        self.events.append(event)
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.storage_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            logger.exception("Could not persist audit event %s", event_type)

    def recent_for(self, principal: Principal, limit: int = 100) -> list[dict]:
        matching = [event for event in self.events if event.get("organization_id") == principal.organization_id]
        return matching[-limit:]


class PolicyEngine:
    blocked_patterns = (re.compile(r"\b(?:how to make a bomb|credit card dump)\b", re.I),)
    pii_pattern = re.compile(r"\b\d{3}[- ]?\d{2,4}[- ]?\d{4}\b")
    pii_max_length = 13

    def check_input(self, text: str) -> None:
        if any(pattern.search(text) for pattern in self.blocked_patterns):
            raise HTTPException(status_code=422, detail="Request blocked by configured content policy")

    def mask_output(self, text: str) -> str:
        return self.pii_pattern.sub("[REDACTED-PHONE]", text)

    def mask_stream(self, buffer: str, final: bool = False) -> tuple[str, str]:
        """Return text that is safe to stream plus the tail that must wait for more input.

        Keeping the final ``pii_max_length`` characters preserves matches that could
        otherwise be split across two model chunks. A match that starts before the
        boundary and ends after it moves the boundary back to its start.
        """
        if final:
            return self.mask_output(buffer), ""
        if len(buffer) <= self.pii_max_length:
            return "", buffer
        limit = len(buffer) - self.pii_max_length
        for match in self.pii_pattern.finditer(buffer):
            if match.start() < limit < match.end():
                limit = match.start()
                break
        if limit <= 0:
            return "", buffer
        return self.mask_output(buffer[:limit]), buffer[limit:]


class DocumentService:
    """Local durable document store used by the basic deployment profile.

    The file format deliberately mirrors the metadata an eventual Qdrant/LlamaIndex
    adapter must preserve: organization scope, role ACL, source title, and content.
    """

    def __init__(self, storage_root: Path | None = None) -> None:
        self.storage_root = storage_root or Path(os.getenv("DOCUMENT_STORE", "/tmp/baryon-ax-documents"))
        self.documents: dict[str, Document] = {}
        self._load()

    def _load(self) -> None:
        if not self.storage_root.exists():
            return
        for file_path in self.storage_root.glob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                document = Document(
                    id=payload["id"], organization_id=payload["organization_id"], owner_id=payload.get("owner_id", "unknown"), title=payload["title"],
                    content=payload["content"], allowed_roles=set(payload["allowed_roles"]), created_at=payload["created_at"],
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            self.documents[document.id] = document

    def _persist(self, document: Document) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        target = self.storage_root / f"{document.id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(document), ensure_ascii=False, default=list), encoding="utf-8")
        temporary.replace(target)

    def can_view(self, principal: Principal, document: Document) -> bool:
        """Single visibility rule shared by listing and retrieval."""
        if document.organization_id != principal.organization_id:
            return False
        if "admin" in principal.roles or document.owner_id == principal.user_id:
            return True
        return bool(document.allowed_roles.intersection(principal.roles))

    def create(self, principal: Principal, title: str, content: str, allowed_roles: set[str]) -> Document:
        document = Document(str(uuid.uuid4()), principal.organization_id, principal.user_id, title, content, allowed_roles)
        self.documents[document.id] = document
        self._persist(document)
        return document

    def list_for(self, principal: Principal) -> list[dict]:
        return [
            {"id": document.id, "title": document.title, "owner_id": document.owner_id,
             "allowed_roles": sorted(document.allowed_roles), "created_at": document.created_at,
             "chunks": len(chunk_text(document.content))}
            for document in self.documents.values()
            if self.can_view(principal, document)
        ]

    def delete(self, principal: Principal, document_id: str) -> bool:
        document = self.documents.get(document_id)
        if not document or document.organization_id != principal.organization_id:
            return False
        if document.owner_id != principal.user_id and "admin" not in principal.roles:
            raise PermissionError("Only the uploader or an administrator can delete this document")
        self.documents.pop(document_id)
        (self.storage_root / f"{document_id}.json").unlink(missing_ok=True)
        return True

    def update_roles(self, principal: Principal, document_id: str, allowed_roles: set[str]) -> Document | None:
        if "admin" not in principal.roles:
            raise PermissionError("Administrator role required")
        document = self.documents.get(document_id)
        if not document or document.organization_id != principal.organization_id:
            return None
        document.allowed_roles = allowed_roles
        self._persist(document)
        return document

    def search(self, principal: Principal, query: str, limit: int = 4) -> list[dict]:
        terms = search_terms(query)
        matches = []
        for document in self.documents.values():
            if not self.can_view(principal, document):
                continue
            for number, chunk in enumerate(chunk_text(document.content)):
                score = sum(term in chunk.lower() for term in terms)
                if score:
                    matches.append({"document_id": document.id, "title": document.title, "chunk": number + 1,
                                    "content": chunk, "score": score})
        return sorted(matches, key=lambda item: item["score"], reverse=True)[:limit]


def chunk_text(text: str, size: int = 900) -> list[str]:
    return [text[index:index + size] for index in range(0, len(text), size)] or [""]


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[가-힣]+")


def search_terms(text: str) -> set[str]:
    """Expand query tokens with short Korean prefixes so particles do not block matches."""
    terms: set[str] = set()
    for token in TOKEN_PATTERN.findall(text.lower()):
        if len(token) < 2:
            continue
        terms.add(token)
        if re.fullmatch(r"[가-힣]+", token):
            terms.add(token[:2])
            if len(token) > 3:
                terms.add(token[:3])
    return terms


class ModelAdapter:
    def __init__(self) -> None:
        self.model = os.getenv("MODEL_NAME", "demo-local")
        self.base_url = os.getenv("MODEL_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("MODEL_API_KEY", "")

    def demo_answer(self, request: ChatRequest, context: list[dict]) -> str:
        prompt = request.messages[-1].content
        sources = "\n".join(f"- {item['title']}: {item['content'][:180]}" for item in context)
        if request.locale == "ko":
            suffix = f"\n\n검색된 근거:\n{sources}" if sources else ""
            return f"[demo-local] 요청을 받았습니다: {prompt}{suffix}\n\n실제 온프레미스 모델을 사용하려면 MODEL_BASE_URL을 설정하세요."
        suffix = f"\n\nRetrieved context:\n{sources}" if sources else ""
        return f"[demo-local] I received: {prompt}{suffix}\n\nConfigure MODEL_BASE_URL to use your on-premises model."

    def payload(self, request: ChatRequest, context: list[dict], stream: bool) -> dict:
        messages = [message.model_dump() for message in request.messages]
        if context:
            instruction = "관련된 경우 다음 검색 문서를 근거로 답변하고, 한국어로 답변하세요:\n" if request.locale == "ko" else "Answer using this retrieved context when relevant:\n"
            messages.insert(0, {"role": "system", "content": instruction + "\n".join(item["content"] for item in context)})
        return {"model": request.model, "messages": messages, "stream": stream}

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def complete(self, request: ChatRequest, context: list[dict]) -> str:
        if not self.base_url:
            return self.demo_answer(request, context)
        async with httpx.AsyncClient(timeout=MODEL_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=self.headers(),
                json=self.payload(request, context, stream=False))
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    async def stream(self, request: ChatRequest, context: list[dict]) -> AsyncIterator[str]:
        if not self.base_url:
            for token in re.findall(r"\S+\s*", self.demo_answer(request, context)):
                yield token
                await asyncio.sleep(0.01)
            return
        async with httpx.AsyncClient(timeout=MODEL_TIMEOUT_SECONDS) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=self.headers(),
                json=self.payload(request, context, stream=True)) as response:
                response.raise_for_status()
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    payload = json.loads(await response.aread())
                    content = payload["choices"][0]["message"]["content"]
                    if content:
                        yield content
                    return
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta


app = FastAPI(title="Baryon AX API", version=__version__)
origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
documents, policy, audit, model_adapter = DocumentService(), PolicyEngine(), AuditSink(), ModelAdapter()


def roles_from_header(value: str) -> set[str]:
    return {role.strip() for role in value.split(",") if role.strip()}


def principal_from_headers(user_id: str | None, organization_id: str | None, roles: str | None) -> Principal:
    return Principal(user_id or "local-user", organization_id or "local-org", roles_from_header(roles or "member"))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "model_adapter": "openai-compatible" if model_adapter.base_url else "demo-local", "time": now()}


@app.get("/v1/models")
async def models() -> dict:
    return {"object": "list", "data": [{"id": model_adapter.model, "object": "model", "owned_by": "baryon"}]}


@app.post("/v1/documents")
async def upload_document(
    file: UploadFile = File(...), allowed_roles: str = Form("member,admin"),
    x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
) -> dict:
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    if not file.filename or Path(file.filename).suffix.lower() not in {".txt", ".md"}:
        raise HTTPException(415, "Only UTF-8 .txt and .md files are supported by the basic parser")
    payload = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f"Document exceeds the {MAX_DOCUMENT_BYTES} byte limit")
    try:
        content = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise HTTPException(415, "Document must be UTF-8 encoded") from error
    if not content.strip():
        raise HTTPException(422, "Document is empty")
    document = documents.create(principal, file.filename, content, roles_from_header(allowed_roles))
    trace_id = str(uuid.uuid4())
    audit.emit("document.ingested", principal, trace_id, document_id=document.id, title=document.title)
    return {"id": document.id, "title": document.title, "chunks": len(chunk_text(content)), "trace_id": trace_id}


@app.get("/v1/documents/search")
async def search_documents(
    q: str = Query(min_length=2), x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
) -> dict:
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    return {"data": documents.search(principal, q)}


@app.get("/v1/documents")
async def list_documents(
    x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
) -> dict:
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    return {"data": documents.list_for(principal)}


@app.delete("/v1/documents/{document_id}")
async def delete_document(
    document_id: str, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
) -> dict:
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    try:
        deleted = documents.delete(principal, document_id)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    if not deleted:
        raise HTTPException(404, "Document not found")
    trace_id = str(uuid.uuid4())
    audit.emit("document.deleted", principal, trace_id, document_id=document_id)
    return {"id": document_id, "deleted": True, "trace_id": trace_id}


@app.patch("/v1/documents/{document_id}")
async def update_document(
    document_id: str, update: DocumentUpdate,
    x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
) -> dict:
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    roles = {role.strip() for role in update.allowed_roles if role.strip()}
    try:
        document = documents.update_roles(principal, document_id, roles)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    if not document:
        raise HTTPException(404, "Document not found")
    trace_id = str(uuid.uuid4())
    audit.emit("document.acl_updated", principal, trace_id, document_id=document.id, allowed_roles=sorted(document.allowed_roles))
    return {"id": document.id, "allowed_roles": sorted(document.allowed_roles), "trace_id": trace_id}


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def citation_payload(citations: list[dict]) -> list[dict]:
    """Return only the metadata the client needs; document bodies stay server-side."""
    return [{"document_id": item["document_id"], "title": item["title"], "chunk": item["chunk"]} for item in citations]


async def stream_chat(request: ChatRequest, principal: Principal, trace_id: str, started: float, citations: list[dict]) -> AsyncIterator[str]:
    buffer = ""
    try:
        async for chunk in model_adapter.stream(request, citations):
            safe, buffer = policy.mask_stream(buffer + chunk)
            if safe:
                yield sse({"type": "token", "content": safe})
        tail, _ = policy.mask_stream(buffer, final=True)
        if tail:
            yield sse({"type": "token", "content": tail})
        audit.emit("chat.completed", principal, trace_id, thread_id=request.thread_id, model=request.model,
                   latency_ms=round((time.perf_counter() - started) * 1000),
                   citations=[item["document_id"] for item in citations], streamed=True)
        yield sse({"type": "citations", "data": citation_payload(citations), "trace_id": trace_id})
        yield "data: [DONE]\n\n"
    except asyncio.CancelledError:
        audit.emit("chat.cancelled", principal, trace_id, thread_id=request.thread_id, model=request.model, streamed=True)
        raise
    except Exception as error:
        logger.exception("Streaming completion failed for trace %s", trace_id)
        audit.emit("chat.failed", principal, trace_id, thread_id=request.thread_id, model=request.model,
                   reason=type(error).__name__, streamed=True)
        yield sse({"type": "error", "message": "Configured model endpoint could not complete the request"})
        yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat(
    request: ChatRequest, x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
):
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    latest = request.messages[-1].content
    policy.check_input(latest)
    trace_id, started = str(uuid.uuid4()), time.perf_counter()
    citations = documents.search(principal, latest) if request.use_retrieval else []
    audit.emit("chat.started", principal, trace_id, thread_id=request.thread_id, model=request.model, retrieval_count=len(citations))
    if request.stream:
        return StreamingResponse(stream_chat(request, principal, trace_id, started, citations), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Trace-Id": trace_id})
    try:
        answer = policy.mask_output(await model_adapter.complete(request, citations))
    except httpx.HTTPError as error:
        audit.emit("chat.failed", principal, trace_id, thread_id=request.thread_id, model=request.model, reason=type(error).__name__, streamed=False)
        raise HTTPException(502, "Configured model endpoint could not complete the request") from error
    audit.emit("chat.completed", principal, trace_id, thread_id=request.thread_id, model=request.model,
               latency_ms=round((time.perf_counter() - started) * 1000),
               citations=[item["document_id"] for item in citations], streamed=False)
    return {"id": trace_id, "object": "chat.completion", "model": request.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "citations": citation_payload(citations)}


@app.get("/v1/audit-events")
async def audit_events(
    limit: int = Query(100, ge=1, le=500), x_user_id: str | None = Header(None),
    x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
) -> dict:
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    if "admin" not in principal.roles:
        raise HTTPException(403, "Administrator role required")
    return {"data": audit.recent_for(principal, limit)}


@app.get("/v1/admin/settings")
async def admin_settings(
    x_user_id: str | None = Header(None), x_organization_id: str | None = Header(None), x_roles: str | None = Header(None),
) -> dict:
    principal = principal_from_headers(x_user_id, x_organization_id, x_roles)
    if "admin" not in principal.roles:
        raise HTTPException(403, "Administrator role required")
    organization_documents = [document for document in documents.documents.values() if document.organization_id == principal.organization_id]
    organization_events = audit.recent_for(principal, limit=5000)
    return {
        "version": __version__,
        "organization_id": principal.organization_id,
        "model": {
            "name": model_adapter.model,
            "adapter": "openai-compatible" if model_adapter.base_url else "demo-local",
            "base_url_configured": bool(model_adapter.base_url),
            "timeout_seconds": MODEL_TIMEOUT_SECONDS,
        },
        "storage": {
            "documents": "local-json",
            "audit": "jsonl",
            "max_document_bytes": MAX_DOCUMENT_BYTES,
        },
        "policy": {
            "blocked_patterns": len(policy.blocked_patterns),
            "pii_masking": True,
        },
        "retrieval": {"mode": "lexical", "result_limit": 4},
        "counts": {"documents": len(organization_documents), "audit_events": len(organization_events)},
    }
