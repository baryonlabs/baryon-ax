"""Voice secretary bridge: owner-only access, session ownership, metadata-only audit."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import baryon.main as main
from baryon import assistant

client = TestClient(main.app)
OWNER = {"X-User-Id": "owner", "X-Organization-Id": "org", "X-Roles": "member"}
OTHER = {"X-User-Id": "intruder", "X-Organization-Id": "org", "X-Roles": "admin"}
SESSION = "a" * 32


@pytest.fixture()
def secretary(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v1/sessions":
            return httpx.Response(200, json={"id": SESSION, "model": "sonnet"})
        if path.endswith("/turn"):
            return httpx.Response(200, json={"used": "answer"})
        if path.endswith("/events"):
            body = (": connected\n\n"
                    'data: {"type": "text", "delta": "메일 세 통이 왔어요."}\n\n'
                    'data: {"type": "confirmed", "id": "1", "allow": true, "tool": "mcp__dureclaw__send_task"}\n\n')
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient
    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(main, "assistant_config", assistant.AssistantConfig("http://secretary.test", "sec-key", frozenset({"owner"})))
    main.assistant_sessions.clear()
    return seen


def audit(kind: str) -> list[dict]:
    events = client.get("/v1/audit-events?limit=200", headers={**OWNER, "X-Roles": "admin"}).json()["data"]
    return [event for event in events if event.get("type") == kind]


def test_owner_runs_a_conversation_and_audit_has_no_transcript(secretary) -> None:
    assert client.get("/v1/assistant/status", headers=OWNER).json() == {"enabled": True, "allowed": True}
    started = client.post("/v1/assistant/sessions", headers=OWNER).json()
    assert started["id"] == SESSION and json.loads(secretary[-1].content) == {"owner": "owner"}
    assert secretary[-1].headers["authorization"] == "Bearer sec-key"
    turn = client.post(f"/v1/assistant/sessions/{SESSION}/turn", headers=OWNER, json={"text": "오늘 메일 요약해줘"})
    assert turn.json() == {"used": "answer"}
    with client.stream("GET", f"/v1/assistant/sessions/{SESSION}/events", headers=OWNER) as response:
        lines = [line for line in response.iter_lines() if line]
    assert lines[0] == ": ping" and json.loads(lines[1][6:])["delta"] == "메일 세 통이 왔어요."
    dumped = json.dumps(audit("assistant.turn") + audit("assistant.confirmation"), ensure_ascii=False)
    assert "메일 요약" not in dumped and '"characters": 10' in dumped
    assert audit("assistant.confirmation")[-1]["tool"] == "mcp__dureclaw__send_task"


def test_other_users_and_unknown_sessions_are_refused(secretary) -> None:
    assert client.get("/v1/assistant/status", headers=OTHER).json()["allowed"] is False
    assert client.post("/v1/assistant/sessions", headers=OTHER).status_code == 403
    client.post("/v1/assistant/sessions", headers=OWNER)
    # an admin who is not an owner cannot reach the owner's session
    assert client.post(f"/v1/assistant/sessions/{SESSION}/turn", headers=OTHER, json={"text": "x"}).status_code == 403
    assert client.post(f"/v1/assistant/sessions/{'b' * 32}/turn", headers=OWNER, json={"text": "x"}).status_code == 404
    assert client.post("/v1/assistant/sessions/../turn", headers=OWNER, json={"text": "x"}).status_code in (404, 405)


def test_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(main, "assistant_config", assistant.AssistantConfig("", "", frozenset()))
    assert client.get("/v1/assistant/status", headers=OWNER).json() == {"enabled": False, "allowed": False}
    assert client.post("/v1/assistant/sessions", headers=OWNER).status_code == 503
