from __future__ import annotations

from fastapi.testclient import TestClient

from baryon.connectors import (
    CONNECTOR_BY_ID,
    CONNECTORS,
    TOOLS,
    decide_tool,
    get_tool,
    search_tools,
)
from baryon.main import app


def test_catalog_has_thirty_unique_connectors() -> None:
    assert len(CONNECTORS) == 30
    assert len({connector.id for connector in CONNECTORS}) == 30
    for connector in CONNECTORS:
        assert connector.actions, connector.id
        assert all(tool.id.startswith(connector.id + ".") for tool in connector.actions)
        assert connector.auth in {"oauth2", "api_key", "basic", "service_account"}


def test_tool_ids_are_unique_and_read_write_are_balanced() -> None:
    assert len(TOOLS) == len({tool.id for tool in TOOLS})
    assert sum(1 for tool in TOOLS if tool.risk == "read") >= 30
    assert sum(1 for tool in TOOLS if tool.risk == "write") >= 30


def test_search_ranks_slack_message_tools() -> None:
    matches = search_tools("slack message", limit=5)
    ids = [match.tool.id for match in matches]
    assert "slack.post_message" in ids
    assert "slack.search_messages" in ids


def test_search_works_with_korean_aliases() -> None:
    matches = search_tools("슬랙 메시지", limit=5)
    assert any(match.connector.id == "slack" for match in matches)


def test_write_tools_require_approval_for_members() -> None:
    tool = get_tool("slack.post_message")
    assert tool is not None
    member = decide_tool(tool, {"member"})
    admin = decide_tool(tool, {"admin"})
    assert member.allowed
    assert member.requires_approval
    assert admin.allowed
    assert not admin.requires_approval


def test_role_restriction_denies_unknown_roles() -> None:
    tool = get_tool("slack.search_messages")
    assert tool is not None
    restricted = tool.__class__(**{**tool.__dict__, "allowed_roles": ("admin",)})
    decision = decide_tool(restricted, {"member"})
    assert not decision.allowed


def test_erp_and_hcm_connectors_are_admin_only() -> None:
    for connector_id in ("sap-s4hana", "oracle-fusion", "workday"):
        connector = CONNECTOR_BY_ID[connector_id]
        assert connector.actions
        for tool in connector.actions:
            assert not decide_tool(tool, {"member"}).allowed
            assert decide_tool(tool, {"admin"}).allowed


def test_connector_endpoints() -> None:
    client = TestClient(app)
    listing = client.get("/v1/connectors")
    assert listing.status_code == 200
    assert listing.json()["total"] == 30

    detail = client.get("/v1/connectors/slack")
    assert detail.status_code == 200
    assert detail.json()["name"] == "Slack"
    assert detail.json()["tools"]

    search = client.get("/v1/tools/search", params={"q": "slack message", "limit": 5})
    assert search.status_code == 200
    assert search.json()["items"]

    tools = client.get("/v1/tools", params={"connector_id": "slack"})
    assert tools.status_code == 200
    assert tools.json()["total"] == len(CONNECTOR_BY_ID["slack"].actions)

    status = client.get("/v1/mcp/status")
    assert status.status_code == 200
    assert status.json()["connectors"] == 30
    assert status.json()["execution"] == "not_configured"
