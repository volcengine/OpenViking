# MCP Clients

Any [MCP](https://modelcontextprotocol.io/)-compatible client can connect to OpenViking's built-in `/mcp` endpoint — no plugin installation or extra processes needed. This covers Cursor, Trae, Manus, Claude Desktop, ChatGPT, and others.

## Quick setup

Most MCP clients use the standard `mcpServers` format:

```json
{
  "mcpServers": {
    "openviking": {
      "url": "https://your-server.com/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

No authentication is needed when connecting to a local server without `root_api_key` configured (dev mode).

## Platform-specific notes

### Claude Code

Claude Code requires `"type": "http"`. Add via CLI:

```bash
claude mcp add --transport http openviking \
  https://your-server.com/mcp \
  --header "Authorization: Bearer your-api-key-here"
```

Add `--scope user` to make the config global across all projects.

> For auto-recall and auto-capture without manual tool calls, use the [Claude Code Memory Plugin](./02-claude-code.md) instead.

### Trae / Cursor / ChatGPT / Codex / OpenCode

Standard `mcpServers` config as shown above — all verified with API key auth.

### Claude Desktop / Claude.ai (OAuth)

These clients require OAuth 2.1 — API keys cannot be passed directly. OpenViking ships a native OAuth 2.1 implementation, so no external proxy is needed.

If you already have HTTPS configured for your OpenViking server, just connect to `https://your-server.com/mcp` — the client will walk you through the OAuth authorization flow automatically.

See the [OAuth 2.1 Guide](../guides/11-oauth.md) and [Public Access Guide](../guides/12-public-access.md) for HTTPS setup, deployment templates, and the full authorization flow.

## Available tools

Once connected, OpenViking exposes 14 tools:

| Tool | Description |
|------|-------------|
| `search` | Semantic search across memories, resources, and skills |
| `read` | Read one or more `viking://` URIs |
| `list` | List entries under a `viking://` directory |
| `store` | Store messages into long-term memory |
| `add_resource` | Add a local file or URL as a resource |
| `grep` | Regex content search across `viking://` files |
| `glob` | Find files matching a glob pattern |
| `forget` | Delete a `viking://` URI |
| `code_outline` | Show a file's symbol structure |
| `code_search` | Search symbol names across a directory |
| `code_expand` | Return the full source of a single named symbol |
| `health` | Check OpenViking service health |
| `list_watches` | List auto-refresh subscriptions |
| `cancel_watch` | Cancel a watch task |

For tool parameters, progressive file upload, and advanced configuration, see the [MCP Integration Guide](../guides/06-mcp-integration.md).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Connection refused | Verify `openviking-server` is running: `curl http://localhost:1933/health` |
| Authentication errors | Ensure the API key in your client config matches the server. See [Authentication Guide](../guides/04-authentication.md) |

## See also

- [MCP Integration Guide](../guides/06-mcp-integration.md) — tool parameters, progressive upload, `OPENVIKING_PUBLIC_BASE_URL`
- [OAuth 2.1 Guide](../guides/11-oauth.md) — for Claude Desktop, Claude.ai, Cursor
- [MCP Specification](https://modelcontextprotocol.io/)
