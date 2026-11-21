"""Minimal MCP server (Streamable HTTP, JSON responses) exposing baryon-ax as tools.

Any MCP host — a chat app with agents, a desktop assistant, an IDE — can then use
the same grounded search, answers and knowledge-gap loop as the Slack/Discord bots,
without a per-platform integration.

Like ``mcp.py`` (the client), this avoids an SDK dependency: the server only needs
``initialize``, ``ping``, ``tools/list`` and ``tools/call``.  It is stateless (no
``Mcp-Session-Id``) and answers every POST with ``application/json``; ``GET`` for a
server-sent event stream returns 405, which the spec allows.

Tools run as the calling principal, so document permissions, audit events and
knowledge-gap capture are the same as for the REST API.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import __version__

logger = logging.getLogger("baryon.mcp_server")

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST_VERSION = SUPPORTED_VERSIONS[0]
INSTRUCTIONS = (
    "baryon-ax is an organization's document knowledge base. Use search_documents to find passages and "
    "ask for a grounded answer with citations; always show the cited document titles to the user. "
    "When an answer is missing, list_knowledge_gaps shows questions people asked that could not be answered; "
    "record_answer saves a person's answer as a draft and publish_answer indexes it — publish only after the "
    "author confirms."
)

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603


class ToolError(Exception):
    """A tool-level failure: returned as a result with isError=true so the model can react."""


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], dict[str, str | None]], Awaitable[tuple[str, dict[str, Any] | None]]]
    read_only: bool = True
    destructive: bool = False

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"title": self.title, "readOnlyHint": self.read_only, "destructiveHint": self.destructive,
                            "idempotentHint": self.read_only, "openWorldHint": False},
        }


@dataclass
class MCPServer:
    tools: dict[str, Tool] = field(default_factory=dict)

    def tool(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    async def handle(self, payload: Any, identity: dict[str, str | None]) -> Any:
        """Handle one JSON-RPC message or a batch. Returns None when nothing is sent back."""
        if isinstance(payload, list):
            if not payload:
                return _error(None, INVALID_REQUEST, "empty batch")
            replies = [reply for reply in [await self._one(item, identity) for item in payload] if reply is not None]
            return replies or None
        return await self._one(payload, identity)

    async def _one(self, message: Any, identity: dict[str, str | None]) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
                return None  # a response to a server request; this server never sends any
            return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "invalid JSON-RPC request")
        method, params, request_id = message["method"], message.get("params") or {}, message.get("id")
        is_notification = "id" not in message
        if is_notification:
            return None  # notifications/initialized, notifications/cancelled, …
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS, "params must be an object")
        try:
            if method == "initialize":
                return _result(request_id, self._initialize(params))
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": [tool.definition() for tool in self.tools.values()]})
            if method == "tools/call":
                return _result(request_id, await self._call(params, identity))
            return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")
        except _ParamsError as error:
            return _error(request_id, INVALID_PARAMS, str(error))
        except Exception:
            logger.exception("MCP %s failed", method)
            return _error(request_id, INTERNAL_ERROR, "internal error")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else LATEST_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "baryon-ax", "title": "Baryon AX", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    async def _call(self, params: dict[str, Any], identity: dict[str, str | None]) -> dict[str, Any]:
        name, arguments = params.get("name"), params.get("arguments") or {}
        tool = self.tools.get(name) if isinstance(name, str) else None
        if tool is None:
            raise _ParamsError(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise _ParamsError("arguments must be an object")
        missing = [key for key in tool.input_schema.get("required", []) if arguments.get(key) in (None, "")]
        if missing:
            return _tool_error(f"missing required argument(s): {', '.join(missing)}")
        try:
            text, structured = await tool.handler(arguments, identity)
        except ToolError as error:
            return _tool_error(str(error))
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
        if structured is not None:
            result["structuredContent"] = structured
        return result


class _ParamsError(Exception):
    pass


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def parse_body(raw: bytes) -> tuple[Any, dict[str, Any] | None]:
    """JSON body → (payload, None) or (None, JSON-RPC parse error)."""
    try:
        return json.loads(raw or b"null"), None
    except (ValueError, UnicodeDecodeError):
        return None, _error(None, PARSE_ERROR, "parse error")
