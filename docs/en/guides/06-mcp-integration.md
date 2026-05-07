# MCP Integration Guide

OpenViking server has a built-in [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) endpoint, allowing any MCP-compatible client to access its memory and resource capabilities over HTTP — no additional processes needed.

## Prerequisites

1. OpenViking installed (`pip install openviking` or from source)
2. A valid configuration file (see [Configuration Guide](01-configuration.md))
3. `openviking-server` running (see [Deployment Guide](03-deployment.md))

The MCP endpoint is at `http://<server>:1933/mcp`, sharing the same process and port as the REST API.

## Verified Platforms

The following platforms have been successfully integrated with OpenViking MCP:

| Platform | Integration Method |
|----------|-------------------|
| **Claude Code** | `type: http` |
| **ChatGPT & Codex** | Standard MCP config |
| **Claude.ai / Claude Desktop** | Via MCP-Key2OAuth proxy |
| **Manus** | Standard MCP config |
| **Trae** | Standard MCP config |

## Authentication

The MCP endpoint shares the same API-Key authentication system as the OpenViking REST API. Pass either header:

- `X-Api-Key: <your-key>`
- `Authorization: Bearer <your-key>`

No authentication is required in local dev mode (server bound to localhost).

## Client Configuration

### Generic MCP Clients

Most MCP-compatible platforms (Trae, Manus, Cursor, etc.) use the standard `mcpServers` format:

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

### Claude Code

Claude Code requires `"type": "http"`. Add via CLI:

```bash
claude mcp add --transport http openviking \
  https://your-server.com/mcp \
  --header "Authorization: Bearer your-api-key-here"
```

Or in `.mcp.json`:

```json
{
  "mcpServers": {
    "openviking": {
      "type": "http",
      "url": "https://your-server.com/mcp",
      "headers": {
        "Authorization": "Bearer your-api-key-here"
      }
    }
  }
}
```

Add `--scope user` to make the config global (shared across all projects).

### Claude.ai / Claude Desktop (OAuth Proxy)

Claude.ai and Claude Desktop Connector require MCP servers to use OAuth 2.1 authentication — API Keys cannot be passed directly.

#### Official OAuth Support (Planned)

We are evaluating built-in OAuth 2.1 authorization endpoints for `openviking-server`. The initial design includes:

- **OTP Authorization**: Obtain a one-time passcode via CLI (`ov otp`) or REST API, then enter it on the OAuth authorization page — no external dependencies required
- **Console Quick-Auth**: Leverage the Web Console (port 8020) same-origin session for one-click authorization
- **Third-party Login**: Optional delegated login via GitHub / Google or other IdPs

These approaches are currently in design review; implementation timeline is TBD.

#### Current Workaround: MCP-Key2OAuth (Community Project)

Until official OAuth support is available, the community project [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth) can proxy API Key auth as an OAuth flow:

1. Follow the project README to self-host the proxy (Cloudflare Workers)
2. Enter your OpenViking MCP server URL (e.g., `https://your-server.com/mcp`)
3. Generate the proxied URL
4. In Claude.ai / Claude Desktop, enter the generated URL — it will redirect to the proxy for auth
5. After authorization, MCP tools are available

> ⚠️ **Disclaimer:** MCP-Key2OAuth is a third-party community-maintained project. The OpenViking team makes no guarantees regarding its security, availability, or data handling. Please assess the risks before use. If you have concerns, consider waiting for the official OAuth implementation or deploying your own proxy.


## Available MCP Tools

Once connected, OpenViking exposes 9 tools:

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `search` | Semantic search across memories, resources, and skills | `query`, `target_uri` (optional), `limit`, `min_score` |
| `read` | Read one or more `viking://` URIs | `uris` (single string or array) |
| `list` | List entries under a `viking://` directory | `uri`, `recursive` (optional) |
| `store` | Store messages into long-term memory (triggers extraction) | `messages` (list of `{role, content}`) |
| `add_resource` | Add a local file or URL as a resource | `path`, `description` (optional) |
| `grep` | Regex content search across `viking://` files | `uri`, `pattern` (string or array), `case_insensitive` |
| `glob` | Find files matching a glob pattern | `pattern`, `uri` (optional scope) |
| `forget` | Delete any `viking://` URI (use `search` to find it first) | `uri` |
| `health` | Check OpenViking service health | none |

## Troubleshooting

### Connection refused

**Likely cause:** `openviking-server` is not running, or is running on a different port.

**Fix:** Verify the server is running:

```bash
curl http://localhost:1933/health
# Expected: {"status": "ok"}
```

### Authentication errors

**Likely cause:** API key mismatch between client config and server config.

**Fix:** Ensure the API key in your MCP client configuration matches the one in your OpenViking server configuration. See [Authentication Guide](04-authentication.md).

## References

- [MCP Specification](https://modelcontextprotocol.io/)
- [OpenViking Configuration](01-configuration.md)
- [OpenViking Deployment](03-deployment.md)
