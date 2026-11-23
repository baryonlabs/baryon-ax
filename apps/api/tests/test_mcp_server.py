import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="baryon-ax-mcp-test-"))
os.environ.setdefault("DOCUMENT_STORE", str(TEST_ROOT / "documents"))
os.environ.setdefault("AUDIT_LOG", str(TEST_ROOT / "audit" / "events.jsonl"))
os.environ.setdefault("FASTEMBED_CACHE_PATH", str(TEST_ROOT / "fastembed"))
os.environ.setdefault("MEM0_HISTORY_DB", str(TEST_ROOT / "mem0" / "history.db"))
os.environ.setdefault("SYSTEM_METRICS_ENABLED", "false")

from fastapi.testclient import TestClient

from baryon.main import app

client = TestClient(app)
ORG = "mcp-test-org"


def headers(user: str = "mcp-user", roles: str = "member") -> dict[str, str]:
    return {"X-User-Id": user, "X-Organization-Id": ORG, "X-Roles": roles, "Accept": "application/json, text/event-stream"}


def rpc(method: str, params: dict | None = None, request_id: int = 1, **kwargs) -> dict:
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post("/mcp", json=body, headers=kwargs.get("headers") or headers())
    assert response.status_code == 200, response.text
    return response.json()


def call(name: str, arguments: dict, **kwargs) -> dict:
    return rpc("tools/call", {"name": name, "arguments": arguments}, **kwargs)["result"]


def test_initialize_negotiates_version_and_lists_tools() -> None:
    init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "baryon-ax" and init["instructions"]
    assert rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-11-25"

    notified = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers())
    assert notified.status_code == 202 and notified.content == b""
    assert rpc("ping")["result"] == {}

    tools = {tool["name"]: tool for tool in rpc("tools/list")["result"]["tools"]}
    assert set(tools) == {"search_documents", "ask", "list_knowledge_gaps", "suggest_questions", "record_answer", "publish_answer",
                          "crawl_site"}
    assert tools["search_documents"]["annotations"]["readOnlyHint"] is True
    assert tools["publish_answer"]["annotations"]["readOnlyHint"] is False
    assert tools["ask"]["inputSchema"]["required"] == ["question"]


def test_search_and_ask_respect_document_permissions() -> None:
    upload = client.post("/v1/documents", files={"file": ("leave-policy.md", "연차휴가는 입사 1년 후 15일을 부여한다.".encode())},
                         data={"allowed_roles": "admin"}, headers=headers(user="owner", roles="admin"))
    assert upload.status_code == 200

    admin = headers(user="admin-1", roles="admin")
    found = call("search_documents", {"query": "연차휴가"}, headers=admin)
    assert found["isError"] is False
    assert found["structuredContent"]["results"][0]["title"] == "leave-policy.md"
    assert "leave-policy.md" in found["content"][0]["text"]

    hidden = call("search_documents", {"query": "연차휴가"}, headers=headers(user="member-1", roles="member"))
    assert hidden["structuredContent"]["results"] == []

    answered = call("ask", {"question": "연차휴가는 며칠이야?", "thread_id": "mcp:test"}, headers=admin)
    assert answered["isError"] is False
    assert answered["structuredContent"]["citations"][0]["title"] == "leave-policy.md"
    # The demo model quotes the passage it used, so it is listed as a source the answer used.
    assert "Sources used in the answer:" in answered["content"][0]["text"]
    assert answered["structuredContent"]["cited"] == ["leave-policy.md · chunk 1"]


def test_gap_answer_loop_and_tool_errors() -> None:
    member = headers(user="asker", roles="member")
    call("ask", {"question": "주차 등록은 어디서 해?"}, headers=member)  # nothing indexed about parking → gap

    denied = call("list_knowledge_gaps", {}, headers=member)
    assert denied["isError"] is True and denied["content"][0]["text"].startswith("403")

    admin = headers(user="admin-1", roles="admin")
    gaps = call("list_knowledge_gaps", {"limit": 50}, headers=admin)["structuredContent"]["gaps"]
    gap = next(item for item in gaps if item["question"] == "주차 등록은 어디서 해?")

    suggested = call("suggest_questions", {}, headers=admin)["structuredContent"]["questions"]
    assert any(item.get("gap_id") == gap["id"] for item in suggested)

    answerer = headers(user="facilities", roles="member")
    draft = call("record_answer", {"question": gap["question"], "answer": "1층 안내데스크에서 등록합니다.", "gap_id": gap["id"]},
                 headers=answerer)
    entry = draft["structuredContent"]["entry"]
    assert entry["status"] == "draft" and entry["source"] == "mcp"

    other = call("publish_answer", {"entry_id": entry["id"]}, headers=headers(user="someone-else", roles="member"))
    assert other["isError"] is True and other["content"][0]["text"].startswith("403")

    published = call("publish_answer", {"entry_id": entry["id"]}, headers=answerer)
    assert published["isError"] is False
    assert published["structuredContent"]["entry"]["status"] == "published"
    open_ids = [item["id"] for item in call("list_knowledge_gaps", {"limit": 50}, headers=admin)["structuredContent"]["gaps"]]
    assert gap["id"] not in open_ids

    missing = call("ask", {}, headers=member)
    assert missing["isError"] is True and "question" in missing["content"][0]["text"]


def test_protocol_errors_and_transport_rules() -> None:
    assert rpc("resources/list")["error"]["code"] == -32601
    assert rpc("tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602

    bad_json = client.post("/mcp", content=b"{not json", headers={**headers(), "Content-Type": "application/json"})
    assert bad_json.status_code == 400 and bad_json.json()["error"]["code"] == -32700

    batch = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], headers=headers())
    assert [item["id"] for item in batch.json()] == [1, 2]

    foreign = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                          headers={**headers(), "Origin": "https://evil.example"})
    assert foreign.status_code == 403
    assert client.get("/mcp").status_code == 405
