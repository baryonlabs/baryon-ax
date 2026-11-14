from __future__ import annotations

import json

import httpx
import pytest

from baryon.mcp import MCPClient, MCPGatewayError


@pytest.mark.asyncio
async def test_mcp_client_initializes_and_calls_a_tool() -> None:
    seen: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "ContextForge"}}},
            )
        if payload["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "session-1"
            return httpx.Response(202)
        if payload["method"] == "tools/call":
            assert request.headers["mcp-session-id"] == "session-1"
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}},
            )
        raise AssertionError(payload)

    client = MCPClient("http://gateway", "token", transport=httpx.MockTransport(handler))
    result = await client.call_tool("baryon_health", {})
    assert result["isError"] is False
    assert [item["method"] for item in seen] == ["initialize", "notifications/initialized", "tools/call"]


@pytest.mark.asyncio
async def test_mcp_client_surfaces_tool_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s"}, json={"result": {}})
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "error": {"message": "boom"}})

    client = MCPClient("http://gateway", "token", transport=httpx.MockTransport(handler))
    with pytest.raises(MCPGatewayError):
        await client.call_tool("baryon_health", {})


def test_tool_call_requires_gateway_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from baryon.main import app

    monkeypatch.delenv("MCP_GATEWAY_URL", raising=False)
    monkeypatch.delenv("MCP_GATEWAY_TOKEN", raising=False)
    client = TestClient(app)
    response = client.post("/v1/tools/baryon-ax.probe/call", headers={"X-Roles": "admin"}, json={})
    assert response.status_code == 503


def test_member_cannot_call_write_tools() -> None:
    from fastapi.testclient import TestClient
    from baryon.main import app

    client = TestClient(app)
    response = client.post("/v1/tools/slack.post_message/call", headers={"X-Roles": "member"}, json={})
    assert response.status_code == 403
