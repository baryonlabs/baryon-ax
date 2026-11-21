"""Voice secretary service.

Runs on the operator's workstation (where Claude Code and its MCP connections are
signed in) and is reached only by the Baryon AX API, which authenticates the owner.

    POST   /v1/sessions                     start a conversation      -> {id}
    POST   /v1/sessions/{id}/turn           one utterance {text}      -> {used: answer|confirm|queued|interrupt}
    GET    /v1/sessions/{id}/events         server-sent events the voice client speaks
    POST   /v1/sessions/{id}/interrupt      barge-in: stop the current answer
    DELETE /v1/sessions/{id}
    GET    /health

Environment:
    SECRETARY_API_KEY      required bearer key (the Baryon AX API holds it)
    SECRETARY_CWD          working directory, e.g. the repository you code in
    SECRETARY_MODEL        Claude model alias (default sonnet)
    SECRETARY_MCP          comma-separated MCP server names to import from ~/.claude.json
                           (for example dureclaw,baryon-todo,kb-baryon)
    SECRETARY_MAX_SESSIONS concurrent conversations (default 2)
    SECRETARY_IDLE_SECONDS close idle conversations after this long (default 900)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .session import Session

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("secretary")

API_KEY = os.getenv("SECRETARY_API_KEY", "")
CWD = os.getenv("SECRETARY_CWD", os.getcwd())
MODEL = os.getenv("SECRETARY_MODEL", "sonnet")
MCP_NAMES = [name.strip() for name in os.getenv("SECRETARY_MCP", "").split(",") if name.strip()]
MAX_SESSIONS = int(os.getenv("SECRETARY_MAX_SESSIONS", "2"))
IDLE_SECONDS = float(os.getenv("SECRETARY_IDLE_SECONDS", "900"))
HEARTBEAT_SECONDS = 15.0

sessions: dict[str, Session] = {}


def default_client_factory(options: Any) -> Any:
    from claude_agent_sdk import ClaudeSDKClient

    return ClaudeSDKClient(options=options)


client_factory: Callable[[Any], Any] = default_client_factory
session_kwargs: dict[str, Any] = {}


async def reap_idle() -> None:
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for session_id, session in list(sessions.items()):
            if not session.busy and now - session.last_active > IDLE_SECONDS:
                sessions.pop(session_id, None)
                await session.close()
                logger.info("closed idle session %s", session_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    reaper = asyncio.create_task(reap_idle())
    yield
    reaper.cancel()
    for session in list(sessions.values()):
        await session.close()
    sessions.clear()


app = FastAPI(title="Baryon AX voice secretary", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def require_key(authorization: str | None) -> None:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not API_KEY or not hmac.compare_digest(token.encode(), API_KEY.encode()):
        raise HTTPException(401, "invalid secretary key")


def get_session(session_id: str) -> Session:
    session = sessions.get(session_id)
    if not session or session.closed:
        raise HTTPException(404, "unknown or closed session")
    return session


class StartBody(BaseModel):
    owner: str = Field(min_length=1, max_length=200)


class TurnBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "sessions": len(sessions), "model": MODEL, "mcp": MCP_NAMES, "configured": bool(API_KEY)}


@app.post("/v1/sessions")
async def start(body: StartBody, authorization: str | None = Header(None)) -> dict:
    require_key(authorization)
    if len(sessions) >= MAX_SESSIONS:
        # Free the oldest idle conversation of the same owner before refusing.
        idle = [s for s in sessions.values() if s.owner == body.owner and not s.busy]
        if not idle:
            raise HTTPException(429, "too many conversations")
        oldest = min(idle, key=lambda s: s.last_active)
        sessions.pop(oldest.id, None)
        await oldest.close()
    session = Session(client_factory, owner=body.owner, cwd=CWD, model=MODEL, mcp_names=MCP_NAMES, **session_kwargs)
    try:
        await session.start()
    except Exception as error:
        logger.exception("cannot start the agent")
        raise HTTPException(503, f"cannot start the agent: {type(error).__name__}") from error
    sessions[session.id] = session
    return {"id": session.id, "model": MODEL}


@app.post("/v1/sessions/{session_id}/turn")
async def turn(session_id: str, body: TurnBody, authorization: str | None = Header(None)) -> dict:
    require_key(authorization)
    used = await get_session(session_id).turn(body.text)
    return {"used": used}


@app.post("/v1/sessions/{session_id}/interrupt")
async def interrupt(session_id: str, authorization: str | None = Header(None)) -> dict:
    require_key(authorization)
    await get_session(session_id).interrupt()
    return {"ok": True}


@app.delete("/v1/sessions/{session_id}")
async def end(session_id: str, authorization: str | None = Header(None)) -> dict:
    require_key(authorization)
    session = sessions.pop(session_id, None)
    if session:
        await session.close()
    return {"ok": True}


@app.get("/v1/sessions/{session_id}/events")
async def events(session_id: str, authorization: str | None = Header(None)) -> StreamingResponse:
    require_key(authorization)
    session = get_session(session_id)

    async def stream():
        yield ": connected\n\n"
        while not session.closed:
            try:
                event = await asyncio.wait_for(session.events.get(), HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
