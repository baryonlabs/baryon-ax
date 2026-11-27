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

## Knowledge imports and Code Mode

The [Baryon knowledge skill](../.agents/skills/baryon-knowledge/SKILL.md) guides agents through Slack threads and share links, linked documents, attachments, and recursive repository selection. The agent host resolves the sources using the caller's authorized connector or explicitly supplied files. The MCP server accepts the resulting bytes and treats source references as provenance. Upstream permission changes do not revoke an indexed copy. The caller can delete the copy through the document API.

`ingest_knowledge` accepts `authorized: true` and up to 16 `items` with a total of at most 8,000,000 base64 characters. Each item has a `filename`, a `kind`, and `content_base64`. Kinds include `file`, `link`, `thread`, `reply`, `analysis`, `attachment`, and `repository_file`. Use `source` for an HTTPS provenance reference and `parent` for the containing thread or message. Repository files also carry a `repository_path`. URL query strings are discarded. Results include the item index, status, and on success the document ID, hash, parser and chunk count. Binary parsing uses the configured [parser adapters](../deploy/parsers/README.md).

Private knowledge is bound to the authenticated user and organization, including when retrieved with Code Mode. Indexed text is available through `search_documents` for the same explicit identity. It is excluded from chat retrieval and automatic gap verification. Use OIDC for multi-user access. Confirm that the configured parser and embedding services are authorized to process private content before uploading it.

Code Mode takes a JSON program with one to eight sequential `steps`. Supported operations are `search` with `query` and optional `limit`, `read` with `document_id` and one-based `chunk`, and `ingest` with the same arguments as `ingest_knowledge`. Upload limits apply across the entire program. The tool is annotated as a write tool because programs can ingest content. Each step returns its result in order.

    {"steps":[{"operation":"search","query":"specimen catalog","limit":3}]}

## Identity and permissions

Each tool runs the same handler as the REST API with the caller's identity, so document role filters,
audit events and gap capture are identical.

- **Development auth** (`AUTH_MODE` unset): send `X-User-Id`, `X-Organization-Id`, `X-Roles` headers.
- **OIDC** (`AUTH_MODE=oidc`, see [auth-oidc.md](auth-oidc.md)): send `Authorization: Bearer <token>`;
  the middleware replaces any identity headers with the token's claims.
- **Gateways that serve many people** (a team-chat agent, a shared MCP proxy): authenticate with one
  service-account token that has the `baryon-service` role and name the person in each request with
  `X-On-Behalf-Of: mcp:<user>` (optionally `X-On-Behalf-Of-Roles`). Roles are capped by the service
  account's roles, and audit events record both the user and `acting_service`. See
  [auth-oidc.md](auth-oidc.md) → 서비스 계정 위임.

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
(per-user tokens behind OIDC, or a delegating service account that sends `X-On-Behalf-Of` per person).

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
