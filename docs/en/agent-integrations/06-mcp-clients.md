# MCP Clients

Clients supporting [MCP](https://modelcontextprotocol.io/) Streamable HTTP can connect directly to OpenViking's `/mcp` endpoint. Clients that support only stdio can use the proxy in the [Agent Plugins package](./15-agent-plugins.md).

## Quick setup

Clients accepting `mcpServers` and custom headers can use this example. For other clients, follow the platform-specific instructions below:

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

No authentication is needed for a server running in `dev` mode. Keep it bound to loopback; an authenticated server still requires credentials when accessed locally.

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

<a id="trae-cursor-chatgpt"></a>

### Trae / Cursor

Add the service URL and API key shown above to the client's MCP configuration.

### ChatGPT

Create a custom App in developer mode and complete OAuth authorization. See the [OAuth guide](../guides/11-oauth.md#chatgpt).

### Codex

For Codex, use the [Codex Memory Plugin](./04-codex.md). It supplies a stdio MCP proxy through the plugin manifest and keeps MCP credentials aligned with the lifecycle hooks.

### OpenCode

Use OpenCode's native `mcp` config in `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "openviking": {
      "type": "remote",
      "url": "https://your-server.com/mcp",
      "enabled": true,
      "oauth": false,
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

### Claude Desktop / Claude.ai (OAuth)

For the hosted remote-connector flow, use OpenViking’s native OAuth implementation. At the authorization page, sign in with an existing OpenViking User/Admin key. Claude Desktop’s local stdio configuration is a separate connection path.

Enable `oauth.enabled` on the server and configure HTTPS, then connect the client to `https://your-server.com/mcp` and complete authorization in the browser.

See the [OAuth 2.1 Guide](../guides/11-oauth.md) and [Public Access Guide](../guides/12-public-access.md) for HTTPS setup, deployment templates, and the full authorization flow.

## Available tools

Once connected, OpenViking exposes retrieval, memory, resource, watch, filesystem, and code-navigation tools. See the [MCP Integration Guide](../guides/06-mcp-integration.md#available-mcp-tools) for the canonical tool list, parameters, progressive file upload, and advanced configuration.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Connection refused | Verify `openviking-server` is running: `curl http://localhost:1933/health` |
| Authentication errors | Check that the client uses a valid user/admin key. See [Authentication Guide](../guides/04-authentication.md) |

## See also

- [Capability Reference](./16-capability-reference.md)
- [MCP Integration Guide](../guides/06-mcp-integration.md) — tool parameters, progressive upload, `OPENVIKING_PUBLIC_BASE_URL`
- [OAuth 2.1 Guide](../guides/11-oauth.md) — for Claude Desktop, Claude.ai, Cursor
- [MCP Specification](https://modelcontextprotocol.io/)
