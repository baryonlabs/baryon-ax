"""Admin settings: LLM gateway connection info, live fallback status, and operator links."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import baryon.gateway_status as gateway_status
from baryon.main import app

client = TestClient(app)
ADMIN = {"X-User-Id": "ops", "X-Organization-Id": "gw-org", "X-Roles": "admin"}
SECRET = "sk-or-v1-0123456789abcdef0123456789abcdef"

STATUS = {
    "enabled": True, "reachable": True, "error": "", "exposedOnNetwork": False,
    "endpoints": [{"label": "local", "url": "http://127.0.0.1:8030/v1"},
                  {"label": "docker", "url": "http://user:pw@host.docker.internal:8030/v1?token=x"}],
    "models": {"baryon-ax-qwen": [{"provider": "b300", "model": "baryon-ax-qwen"},
                                  {"provider": "rtx4090", "model": "qwen3.5-27b-local"},
                                  {"provider": "openrouter", "model": "qwen/qwen3.5-27b"}]},
    "providers": [
        {"name": "b300", "up": True, "ok": 12, "fail": 1, "lastError": "", "lastOkAt": "2026-11-21T08:12:05Z",
         "keyRequired": False, "keySet": False},
        {"name": "rtx4090", "up": False, "ok": 0, "fail": 3, "lastError": f"401 Bearer {SECRET} rejected",
         "lastOkAt": None, "keyRequired": False, "keySet": False, "apiKey": SECRET},
        {"name": "openrouter", "up": True, "ok": 0, "fail": 0, "lastError": "", "lastOkAt": None,
         "keyRequired": True, "keySet": False},
    ],
    "stats": {"requests": 13, "served": {"b300": 12}, "fallbacks": 1, "failed": 0},
    "upstreamKey": SECRET,
}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    gateway_status._cache.update(at=0.0, url="", value=None)
    for name in ("MODEL_GATEWAY_STATUS_URL", "MODEL_GATEWAY_STATUS_AUTH", "MODEL_SERVER_URL", "QDRANT_URL",
                 "QDRANT_DASHBOARD_URL", "LANGFUSE_ENABLED", "LANGFUSE_DASHBOARD_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_API_KEY", SECRET)
    yield
    gateway_status._cache.update(at=0.0, url="", value=None)


def serve(monkeypatch, payload=STATUS, status=200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    real = httpx.AsyncClient
    monkeypatch.setattr(gateway_status.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_gateway_status_is_shown_and_never_leaks_keys(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_STATUS_URL", "http://ops.test/api/gateway")
    monkeypatch.setenv("MODEL_SERVER_URL", "http://ops.test/#gateway")
    seen: list = []
    serve(monkeypatch, seen=seen)

    response = client.get("/v1/admin/settings", headers=ADMIN)
    assert response.status_code == 200
    body = response.text
    assert SECRET not in body and "upstreamKey" not in body and "apiKey" not in body

    gateway = response.json()["gateway"]
    assert gateway["api_key_configured"] is True
    assert gateway["dashboard_url"] == "http://ops.test/#gateway"
    status = gateway["status"]
    assert [p["name"] for p in status["providers"]] == ["b300", "rtx4090", "openrouter"]
    assert status["providers"][1]["up"] is False and "[redacted]" in status["providers"][1]["last_error"]
    assert status["providers"][2]["key_required"] is True and status["providers"][2]["key_set"] is False
    assert [step["provider"] for step in status["models"]["baryon-ax-qwen"]] == ["b300", "rtx4090", "openrouter"]
    # Credentials, query strings and fragments are stripped from endpoint URLs.
    assert status["endpoints"][1]["url"] == "http://host.docker.internal:8030/v1"
    assert status["stats"] == {"requests": 13, "fallbacks": 1, "failed": 0, "served": {"b300": 12}}
    # The model key is not sent to the status URL unless explicitly requested.
    assert "authorization" not in {k.lower() for k in seen[0].headers.keys()}


def test_status_auth_bearer_is_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_STATUS_URL", "http://llm-gateway.test/v1/gateway/status")
    monkeypatch.setenv("MODEL_GATEWAY_STATUS_AUTH", "bearer")
    seen: list = []
    serve(monkeypatch, seen=seen)
    client.get("/v1/admin/settings", headers=ADMIN)
    assert seen[0].headers["authorization"] == f"Bearer {SECRET}"


def test_unreachable_status_is_reported_not_raised(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_STATUS_URL", "http://ops.test/api/gateway")
    serve(monkeypatch, payload={"error": "boom"}, status=502)
    status = client.get("/v1/admin/settings", headers=ADMIN).json()["gateway"]["status"]
    assert status["reachable"] is False and "502" in status["error"]


def test_gateway_status_absent_when_not_configured() -> None:
    gateway = client.get("/v1/admin/settings", headers=ADMIN).json()["gateway"]
    assert gateway["status"] is None and gateway["status_configured"] is False


def test_links_only_for_configured_tools(monkeypatch) -> None:
    # Dashboard URLs alone do not show buttons: the tool itself must be configured.
    monkeypatch.setenv("QDRANT_DASHBOARD_URL", "http://127.0.0.1:6333/dashboard")
    monkeypatch.setenv("LANGFUSE_DASHBOARD_URL", "http://127.0.0.1:3000")
    links = client.get("/v1/admin/settings", headers=ADMIN).json()["links"]
    assert links["qdrant"] == "" and links["langfuse"] == ""

    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("MODEL_SERVER_URL", "http://ops.test/#gateway")
    links = client.get("/v1/admin/settings", headers=ADMIN).json()["links"]
    assert links["qdrant"].endswith("/dashboard") and links["langfuse"] == "http://127.0.0.1:3000"
    assert links["model_server"] == "http://ops.test/#gateway"


def test_sanitize_rejects_garbage() -> None:
    assert gateway_status.sanitize("nope")["reachable"] is False
    cleaned = gateway_status.sanitize({"providers": [{"name": "x", "ok": "-5", "lastError": "a" * 999}],
                                       "endpoints": [{"label": "bad", "url": "javascript:alert(1)"}]})
    assert cleaned["providers"][0]["ok"] == 0 and len(cleaned["providers"][0]["last_error"]) <= 200
    assert cleaned["endpoints"] == []
    assert json.dumps(cleaned)  # serializable
