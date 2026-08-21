# MCP Clients

Any [MCP](https://modelcontextprotocol.io/)-compatible client can connect to OpenViking's built-in `/mcp` endpoint — no plugin installation or extra processes needed. This covers Cursor, Trae, Manus, Claude Desktop, ChatGPT, GitHub Copilot, and others.

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

### Trae / Cursor / ChatGPT

Standard `mcpServers` config as shown above — all verified with API key auth.

### GitHub Copilot

GitHub Copilot supports MCP across VSCode Copilot Chat / agent mode, `gh copilot` CLI, and the GitHub.com Copilot cloud agent. The config shape differs by surface:

- **VSCode** (`.vscode/mcp.json`): top-level `servers` (NOT `mcpServers`), entry `{ "type": "http", "url": "...", "headers": {...} }`.
- **`gh copilot` CLI** (`~/.copilot/mcp-config.json`): top-level `mcpServers`, entry `{ "type": "http", "url": "...", "headers": {...}, "tools": ["*"] }`.
- **GitHub.com repo-level** (Settings → Copilot → MCP servers): top-level `mcpServers`, required `type` and `tools`; API key referenced as `${COPILOT_MCP_*}` Agents secret. Public preview; tools only (no resources/prompts).

Copilot has **no lifecycle hooks**, so memory is not auto-injected or auto-captured — the model calls the MCP tools itself. The [`examples/copilot-plugin`](../../examples/copilot-plugin/) bundle ships ready-to-use configs, an Agent Skill that supplies the recall/remember policy, and an installer: `bash examples/copilot-plugin/setup-helper/install.sh --cli`. See [DESIGN.md](../../examples/copilot-plugin/DESIGN.md) for the honest scope.

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

These clients require OAuth 2.1 — API keys cannot be passed directly. OpenViking ships a native OAuth 2.1 implementation, so no external proxy is needed.

If you already have HTTPS configured for your OpenViking server, just connect to `https://your-server.com/mcp` — the client will walk you through the OAuth authorization flow automatically.

See the [OAuth 2.1 Guide](../guides/11-oauth.md) and [Public Access Guide](../guides/12-public-access.md) for HTTPS setup, deployment templates, and the full authorization flow.

## Available tools

Once connected, OpenViking exposes retrieval, memory, resource, watch, filesystem, and code-navigation tools. See the [MCP Integration Guide](../guides/06-mcp-integration.md#available-mcp-tools) for the canonical tool list, parameters, progressive file upload, and advanced configuration.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Connection refused | Verify `openviking-server` is running: `curl http://localhost:1933/health` |
| Authentication errors | Ensure the API key in your client config matches the server. See [Authentication Guide](../guides/04-authentication.md) |

## See also

- [Capability Reference](./16-capability-reference.md)
- [MCP Integration Guide](../guides/06-mcp-integration.md) — tool parameters, progressive upload, `OPENVIKING_PUBLIC_BASE_URL`
- [OAuth 2.1 Guide](../guides/11-oauth.md) — for Claude Desktop, Claude.ai, Cursor
- [MCP Specification](https://modelcontextprotocol.io/)
