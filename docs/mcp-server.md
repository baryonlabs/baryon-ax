# baryon-ax as an MCP server

baryon-ax exposes its knowledge as **MCP tools** at `POST /mcp` (Streamable HTTP, JSON responses).
Any MCP host — a team chat with agents, a desktop assistant, an IDE, an agent framework — can then
search documents, get grounded answers with sources, and run the knowledge-gap loop without a
platform-specific integration. The Slack and Discord bots in [`examples/`](../examples) remain the
option for chat platforms that cannot host MCP tools.

[`docs/mcp-connectors.md`](mcp-connectors.md) covers the other direction: baryon-ax *calling*
external MCP tools through a gateway.

## Tools

| Tool | Read-only | What it does |
|---|---|---|
| `search_documents` | ✅ | Passages the caller may read, with document title and chunk (`query`, `limit`) |
| `ask` | ✅ | Grounded answer with citations and trace id (`question`, optional `history`, `thread_id`). Unanswered questions are recorded as knowledge gaps |
| `list_knowledge_gaps` | ✅ | Questions that could not be answered (`status`, `limit`). Administrators only |
| `suggest_questions` | ✅ | Questions worth asking colleagues: open gaps first, then tacit-knowledge templates |
| `record_answer` | — | Save a person's answer as an unpublished draft, optionally linked to a `gap_id` |
| `publish_answer` | — | Index a draft as a searchable document and resolve its gap. Only the author or an administrator |

Tool results carry readable text plus `structuredContent` (results, citations, gaps, entries).
Permission failures come back as tool errors (`isError: true`, e.g. `403: Administrator role required`)
so the model can explain them. The two write tools are annotated `readOnlyHint: false`, so hosts that
ask before side effects will ask before saving or publishing.

The "ask people first" flow from the chat bots maps directly onto these tools:
`list_knowledge_gaps` → ask a colleague → `record_answer` → confirm with the author → `publish_answer`.

## Identity and permissions

Each tool runs the same handler as the REST API with the caller's identity, so document role filters,
audit events and gap capture are identical.

- **Development auth** (`AUTH_MODE` unset): send `X-User-Id`, `X-Organization-Id`, `X-Roles` headers.
- **OIDC** (`AUTH_MODE=oidc`, see [auth-oidc.md](auth-oidc.md)): send `Authorization: Bearer <token>`;
  the middleware replaces any identity headers with the token's claims.

Browser `Origin` headers must be in `ALLOWED_ORIGINS` (DNS-rebinding protection); server-to-server
clients send none. Set `MCP_SERVER_ENABLED=false` to turn the endpoint off.

## Transport

- `POST /mcp` with a JSON-RPC request → `application/json` response. Notifications → `202`.
- Supported protocol versions: `2025-11-25`, `2025-06-18`, `2025-03-26` (the client's version is echoed when supported).
- Stateless: no `Mcp-Session-Id`, no server-initiated messages. `GET /mcp` returns `405`, as the spec allows.
- Methods: `initialize`, `ping`, `tools/list`, `tools/call`.

## Connecting a host

Replace the URL and identity with your deployment's.

**Claude Code**

```bash
claude mcp add --transport http baryon http://127.0.0.1:8000/mcp \
  --header "X-User-Id: me" --header "X-Organization-Id: local-org" --header "X-Roles: member"
```

**Any host with a JSON MCP config** (Claude Desktop via a remote-server entry, Cursor, LibreChat, …)

```json
{
  "mcpServers": {
    "baryon": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

**Team chat with built-in agents** (for example Mattermost Agents, or agent-native chat apps): add an MCP
server with the URL above in the agent settings and give each agent the identity it should search as
(a service account with member roles, or per-user tokens behind OIDC).

**Python, with the official SDK**

```python
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

headers = {"X-User-Id": "me", "X-Organization-Id": "local-org", "X-Roles": "member"}
async with streamable_http_client("http://127.0.0.1:8000/mcp", http_client=httpx.AsyncClient(headers=headers)) as (read, write, *_):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("ask", {"question": "How many vacation days do new employees get?"})
        print(result.content[0].text)
```

## Quick check with curl

```bash
curl -s http://127.0.0.1:8000/mcp -H 'Content-Type: application/json' -H 'X-Roles: member' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_documents","arguments":{"query":"vacation"}}}'
```
