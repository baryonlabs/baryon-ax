"""Curated SaaS connector catalog and read-only tool discovery.

The catalog is intentionally metadata-only. It gives the API and UI a stable
inventory of connectors, their auth type, and the tools we intend to expose
through an MCP gateway. Execution (OAuth, credential vault, gateway proxy) is a
separate phase; see docs/mcp-connectors.md.

Licensing note: this module contains no vendor SDK code. Connectors are
described by public metadata only. Optional external adapters (Nango,
Metorial, Composio) are not bundled here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

READ = "read"
WRITE = "write"


@dataclass(frozen=True)
class Tool:
    id: str
    connector_id: str
    name: str
    description: str
    risk: str = READ
    tags: tuple[str, ...] = ()
    allowed_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Connector:
    id: str
    name: str
    category: str
    auth: str
    docs_url: str
    aliases: tuple[str, ...] = ()
    status: str = "curated"
    actions: tuple[Tool, ...] = ()


def _connector(
    connector_id: str,
    name: str,
    category: str,
    auth: str,
    docs_url: str,
    aliases: tuple[str, ...],
    actions: Iterable[tuple[str, str, str, tuple[str, ...]]],
) -> Connector:
    tools = tuple(
        Tool(
            id=f"{connector_id}.{action}",
            connector_id=connector_id,
            name=action,
            description=description,
            risk=risk,
            tags=tags,
        )
        for action, description, risk, tags in actions
    )
    return Connector(
        id=connector_id,
        name=name,
        category=category,
        auth=auth,
        docs_url=docs_url,
        aliases=aliases,
        actions=tools,
    )


_CONNECTORS: tuple[Connector, ...] = (
    _connector(
        "slack", "Slack", "communication", "oauth2", "https://api.slack.com/", ("슬랙",),
        (
            ("search_messages", "Search messages in channels the user can access", READ, ("messages", "search", "chat")),
            ("post_message", "Post a message to a channel", WRITE, ("messages", "send", "chat")),
        ),
    ),
    _connector(
        "microsoft-teams", "Microsoft Teams", "communication", "oauth2",
        "https://learn.microsoft.com/graph/api/resources/teams-api-overview", ("팀즈", "MS팀즈"),
        (
            ("list_channels", "List channels in a team", READ, ("channels", "teams")),
            ("send_message", "Send a message to a channel", WRITE, ("messages", "send", "teams")),
        ),
    ),
    _connector(
        "google-drive", "Google Drive", "storage", "oauth2", "https://developers.google.com/drive", ("구글드라이브", "드라이브"),
        (
            ("search_files", "Search files by name or content", READ, ("files", "search", "documents")),
            ("upload_file", "Upload a file to a folder", WRITE, ("files", "upload", "documents")),
        ),
    ),
    _connector(
        "google-calendar", "Google Calendar", "productivity", "oauth2", "https://developers.google.com/calendar", ("구글캘린더", "캘린더"),
        (
            ("list_events", "List events in a time range", READ, ("calendar", "events", "schedule")),
            ("create_event", "Create a calendar event", WRITE, ("calendar", "events", "schedule")),
        ),
    ),
    _connector(
        "gmail", "Gmail", "communication", "oauth2", "https://developers.google.com/gmail/api", ("지메일", "메일"),
        (
            ("search_messages", "Search mail messages", READ, ("mail", "search", "messages")),
            ("send_email", "Send an email", WRITE, ("mail", "send", "messages")),
        ),
    ),
    _connector(
        "notion", "Notion", "knowledge", "oauth2", "https://developers.notion.com/", ("노션",),
        (
            ("search_pages", "Search pages and databases", READ, ("pages", "search", "wiki")),
            ("create_page", "Create a page in a database", WRITE, ("pages", "create", "wiki")),
        ),
    ),
    _connector(
        "jira", "Jira", "project-management", "oauth2",
        "https://developer.atlassian.com/cloud/jira/platform/rest/v3/", ("지라",),
        (
            ("search_issues", "Search issues with JQL or text", READ, ("issues", "search", "tickets")),
            ("create_issue", "Create an issue", WRITE, ("issues", "create", "tickets")),
        ),
    ),
    _connector(
        "confluence", "Confluence", "knowledge", "oauth2",
        "https://developer.atlassian.com/cloud/confluence/rest/v2/", ("컨플루언스",),
        (
            ("search_pages", "Search pages and spaces", READ, ("pages", "search", "wiki")),
            ("create_page", "Create a page in a space", WRITE, ("pages", "create", "wiki")),
        ),
    ),
    _connector(
        "github", "GitHub", "engineering", "oauth2", "https://docs.github.com/rest", ("깃허브",),
        (
            ("search_code", "Search code across repositories", READ, ("code", "search", "repositories")),
            ("create_issue", "Create an issue or pull request comment", WRITE, ("issues", "create", "pull-requests")),
        ),
    ),
    _connector(
        "gitlab", "GitLab", "engineering", "oauth2", "https://docs.gitlab.com/ee/api/rest/", ("깃랩",),
        (
            ("search_projects", "Search projects and merge requests", READ, ("projects", "search", "merge-requests")),
            ("create_issue", "Create an issue", WRITE, ("issues", "create", "projects")),
        ),
    ),
    _connector(
        "salesforce", "Salesforce", "crm", "oauth2",
        "https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/", ("세일즈포스",),
        (
            ("query_records", "Run a SOQL query", READ, ("crm", "query", "records")),
            ("create_record", "Create a record", WRITE, ("crm", "create", "records")),
        ),
    ),
    _connector(
        "hubspot", "HubSpot", "crm", "oauth2", "https://developers.hubspot.com/docs/api/overview", ("허브스팟",),
        (
            ("search_contacts", "Search contacts", READ, ("crm", "contacts", "search")),
            ("create_contact", "Create a contact", WRITE, ("crm", "contacts", "create")),
        ),
    ),
    _connector(
        "zendesk", "Zendesk", "support", "oauth2", "https://developer.zendesk.com/api-reference/", ("젠데스크",),
        (
            ("search_tickets", "Search support tickets", READ, ("tickets", "support", "search")),
            ("create_ticket", "Create a support ticket", WRITE, ("tickets", "support", "create")),
        ),
    ),
    _connector(
        "servicenow", "ServiceNow", "itsm", "oauth2", "https://docs.servicenow.com/", ("서비스나우",),
        (
            ("query_incidents", "Query incidents and requests", READ, ("incidents", "itsm", "query")),
            ("create_incident", "Create an incident", WRITE, ("incidents", "itsm", "create")),
        ),
    ),
    _connector(
        "sap-s4hana", "SAP S/4HANA", "erp", "oauth2", "https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE", ("SAP", "에스에이피"),
        (
            ("query_business_partners", "Query business partners", READ, ("erp", "business-partner", "query")),
            ("create_sales_order", "Create a sales order", WRITE, ("erp", "sales-order", "create")),
        ),
    ),
    _connector(
        "oracle-fusion", "Oracle Fusion", "erp", "oauth2", "https://docs.oracle.com/en/cloud/saas/", ("오라클", "Oracle"),
        (
            ("query_suppliers", "Query suppliers", READ, ("erp", "suppliers", "query")),
            ("create_requisition", "Create a purchase requisition", WRITE, ("erp", "purchase", "create")),
        ),
    ),
    _connector(
        "workday", "Workday", "hcm", "oauth2", "https://community.workday.com/api", ("워크데이",),
        (
            ("search_workers", "Search worker records", READ, ("hcm", "workers", "search")),
            ("create_time_off_request", "Create a time-off request", WRITE, ("hcm", "time-off", "create")),
        ),
    ),
    _connector(
        "sharepoint", "SharePoint", "storage", "oauth2",
        "https://learn.microsoft.com/graph/api/resources/sharepoint", ("셰어포인트", "쉐어포인트"),
        (
            ("search_sites", "Search sites and documents", READ, ("sites", "documents", "search")),
            ("upload_document", "Upload a document to a library", WRITE, ("documents", "upload", "sites")),
        ),
    ),
    _connector(
        "onedrive", "OneDrive", "storage", "oauth2",
        "https://learn.microsoft.com/graph/api/resources/onedrive", ("원드라이브",),
        (
            ("search_files", "Search files", READ, ("files", "search", "documents")),
            ("upload_file", "Upload a file", WRITE, ("files", "upload", "documents")),
        ),
    ),
    _connector(
        "box", "Box", "storage", "oauth2", "https://developer.box.com/reference/", ("박스",),
        (
            ("search_files", "Search files and folders", READ, ("files", "search", "documents")),
            ("upload_file", "Upload a file", WRITE, ("files", "upload", "documents")),
        ),
    ),
    _connector(
        "dropbox", "Dropbox", "storage", "oauth2",
        "https://www.dropbox.com/developers/documentation/http/documentation", ("드롭박스",),
        (
            ("search_files", "Search files and folders", READ, ("files", "search", "documents")),
            ("upload_file", "Upload a file", WRITE, ("files", "upload", "documents")),
        ),
    ),
    _connector(
        "figma", "Figma", "design", "oauth2", "https://www.figma.com/developers/api", ("피그마",),
        (
            ("list_files", "List files in a project", READ, ("design", "files", "projects")),
            ("export_image", "Export a frame or component as an image", READ, ("design", "export", "images")),
            ("create_comment", "Create a comment on a file", WRITE, ("design", "comment", "review")),
        ),
    ),
    _connector(
        "linear", "Linear", "project-management", "oauth2", "https://developers.linear.app/docs", ("리니어",),
        (
            ("search_issues", "Search issues", READ, ("issues", "search", "projects")),
            ("create_issue", "Create an issue", WRITE, ("issues", "create", "projects")),
        ),
    ),
    _connector(
        "asana", "Asana", "project-management", "oauth2", "https://developers.asana.com/docs", ("아사나",),
        (
            ("list_tasks", "List tasks in a project", READ, ("tasks", "projects", "list")),
            ("create_task", "Create a task", WRITE, ("tasks", "create", "projects")),
        ),
    ),
    _connector(
        "trello", "Trello", "project-management", "oauth2",
        "https://developer.atlassian.com/cloud/trello/rest/", ("트렐로",),
        (
            ("list_cards", "List cards in a board", READ, ("cards", "boards", "list")),
            ("create_card", "Create a card", WRITE, ("cards", "create", "boards")),
        ),
    ),
    _connector(
        "airtable", "Airtable", "database", "api_key",
        "https://airtable.com/developers/web/api/introduction", ("에어테이블",),
        (
            ("list_records", "List records in a table", READ, ("records", "tables", "list")),
            ("create_record", "Create a record", WRITE, ("records", "create", "tables")),
        ),
    ),
    _connector(
        "monday", "Monday.com", "project-management", "oauth2",
        "https://developer.monday.com/api-reference/", ("먼데이",),
        (
            ("list_items", "List items on a board", READ, ("items", "boards", "list")),
            ("create_item", "Create an item", WRITE, ("items", "create", "boards")),
        ),
    ),
    _connector(
        "zoom", "Zoom", "communication", "oauth2", "https://developers.zoom.us/docs/api/", ("줌",),
        (
            ("list_meetings", "List meetings", READ, ("meetings", "calendar", "list")),
            ("create_meeting", "Create a meeting", WRITE, ("meetings", "create", "calendar")),
        ),
    ),
    _connector(
        "naver-works", "Naver Works", "communication", "oauth2",
        "https://developers.worksmobile.com/", ("네이버웍스", "웍스"),
        (
            ("search_mail", "Search mail", READ, ("mail", "search", "messages")),
            ("send_mail", "Send mail", WRITE, ("mail", "send", "messages")),
        ),
    ),
    _connector(
        "dooray", "Dooray", "communication", "oauth2", "https://developer.nhn.com/", ("두레이", "NHN두레이"),
        (
            ("search_posts", "Search posts", READ, ("posts", "search", "wiki")),
            ("create_post", "Create a post", WRITE, ("posts", "create", "wiki")),
        ),
    ),
)

CONNECTORS: tuple[Connector, ...] = _CONNECTORS
CONNECTOR_BY_ID = {connector.id: connector for connector in CONNECTORS}
TOOLS: tuple[Tool, ...] = tuple(tool for connector in CONNECTORS for tool in connector.actions)
TOOL_BY_ID = {tool.id: tool for tool in TOOLS}

_TOKEN = re.compile(r"[0-9a-zA-Z가-힣]+")
_STOPWORDS = {
    "a", "an", "and", "are", "for", "from", "get", "in", "is", "list", "me", "my",
    "of", "on", "or", "please", "search", "show", "the", "to", "with",
    "검색", "내", "좀", "해줘", "보여줘", "찾아", "에서", "하고",
}


def _tokens(value: str) -> list[str]:
    return [token for token in _TOKEN.findall(value.lower()) if token not in _STOPWORDS]


@dataclass(frozen=True)
class ToolMatch:
    tool: Tool
    connector: Connector
    score: int


@dataclass(frozen=True)
class ToolDecision:
    allowed: bool
    requires_approval: bool
    reason: str


def search_tools(
    query: str,
    limit: int = 10,
    connector_id: str | None = None,
    risk: str | None = None,
) -> list[ToolMatch]:
    """Rank tools by lexical relevance.

    Phase 1 is lexical so it works without an embedding service. The interface
    is deliberately the same shape a Qdrant/FastEmbed implementation will use,
    so the API does not change when embeddings are added.
    """
    query_tokens = _tokens(query)
    raw_query = query.strip().lower()
    matches: list[ToolMatch] = []
    for tool in TOOLS:
        connector = CONNECTOR_BY_ID[tool.connector_id]
        if connector_id and connector.id != connector_id:
            continue
        if risk and tool.risk != risk:
            continue
        haystack = " ".join(
            [
                tool.id,
                tool.name,
                tool.description,
                *tool.tags,
                connector.name,
                *connector.aliases,
            ]
        ).lower()
        score = 0
        for token in query_tokens:
            if token in tool.name.lower():
                score += 5
            if token in tool.description.lower():
                score += 2
            if token in tool.tags:
                score += 3
            if token in connector.name.lower() or token in connector.aliases:
                score += 3
        if raw_query and raw_query in haystack:
            score += 5
        if query_tokens and score == 0:
            continue
        matches.append(ToolMatch(tool=tool, connector=connector, score=score))
    matches.sort(key=lambda match: (-match.score, match.tool.id))
    return matches[:limit]


def decide_tool(tool: Tool, roles: set[str]) -> ToolDecision:
    """Read tools are open to members; writes need admin or explicit approval."""
    if tool.allowed_roles and not set(tool.allowed_roles).intersection(roles):
        return ToolDecision(False, False, "role not allowed for this tool")
    if tool.risk == WRITE and "admin" not in roles:
        return ToolDecision(True, True, "write tool requires approval")
    return ToolDecision(True, False, "allowed")


def connector_payload(connector: Connector) -> dict:
    return {
        "id": connector.id,
        "name": connector.name,
        "category": connector.category,
        "auth": connector.auth,
        "docs_url": connector.docs_url,
        "aliases": list(connector.aliases),
        "status": connector.status,
        "tool_count": len(connector.actions),
    }


def tool_payload(tool: Tool, connector: Connector, roles: set[str]) -> dict:
    decision = decide_tool(tool, roles)
    return {
        "id": tool.id,
        "name": tool.name,
        "connector_id": tool.connector_id,
        "connector_name": connector.name,
        "category": connector.category,
        "description": tool.description,
        "risk": tool.risk,
        "tags": list(tool.tags),
        "auth": connector.auth,
        "decision": {
            "allowed": decision.allowed,
            "requires_approval": decision.requires_approval,
            "reason": decision.reason,
        },
    }


def get_connector(connector_id: str) -> Connector | None:
    return CONNECTOR_BY_ID.get(connector_id)


def get_tool(tool_id: str) -> Tool | None:
    return TOOL_BY_ID.get(tool_id)
