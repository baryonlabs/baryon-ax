"""Minimal MCP Streamable HTTP client for ContextForge tool calls.

The Baryon AX API only needs the tool-call subset: initialize a session, send
the initialized notification, then call one tool. Keeping this small avoids
binding the API to a specific MCP SDK version and makes the gateway replaceable.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class MCPGatewayError(Exception):
    """Raised when the gateway rejects a request or returns an MCP error."""


class MCPClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise MCPGatewayError("MCP gateway URL is not configured")
        if not token:
            raise MCPGatewayError("MCP gateway token is not configured")
        url = base_url.rstrip("/")
        self.url = url if url.endswith("/mcp") else url + "/mcp"
        self.token = token
        self.timeout = timeout
        self.transport = transport

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            initialize = await self._post_response(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "baryon-ax", "version": "0.6.0"},
                    },
                },
            )
            session_id = initialize.headers.get("mcp-session-id")
            if not session_id:
                raise MCPGatewayError("MCP gateway did not return a session id")
            await self._post(
                client,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                session_id=session_id,
                expect_json=False,
            )
            result = await self._post(
                client,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments or {}},
                },
                session_id=session_id,
            )
        if "error" in result:
            raise MCPGatewayError(str(result["error"]))
        payload = result.get("result")
        if not isinstance(payload, dict):
            raise MCPGatewayError("MCP gateway returned an empty tool result")
        if payload.get("isError"):
            raise MCPGatewayError(str(payload.get("content") or "MCP tool returned an error"))
        return payload

    async def _post_response(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        session_id: str | None = None,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        try:
            response = await client.post(self.url, headers=headers, json=payload)
        except httpx.HTTPError as error:
            raise MCPGatewayError(f"MCP gateway request failed: {error}") from error
        if response.status_code >= 400:
            detail = response.text[:300]
            raise MCPGatewayError(f"MCP gateway returned {response.status_code}: {detail}")
        return response

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        session_id: str | None = None,
        expect_json: bool = True,
    ) -> httpx.Response | dict[str, Any]:
        response = await self._post_response(client, payload, session_id)
        if not expect_json or not response.content:
            return response
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            data_lines = [
                line[5:].strip()
                for line in response.text.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                raise MCPGatewayError("MCP gateway returned an empty event stream")
            try:
                return json.loads(data_lines[-1])
            except json.JSONDecodeError as error:
                raise MCPGatewayError(f"MCP gateway returned invalid JSON: {error}") from error
        try:
            return response.json()
        except ValueError as error:
            raise MCPGatewayError(f"MCP gateway returned invalid JSON: {error}") from error
