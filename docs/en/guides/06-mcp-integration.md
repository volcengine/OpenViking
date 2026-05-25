# MCP Integration Guide

OpenViking server has a built-in [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) endpoint, allowing any MCP-compatible client to access its memory and resource capabilities over HTTP — no additional processes needed.

> **Quick setup?** See [MCP Clients](../agent-integrations/06-mcp-clients.md) for client configuration snippets and platform-specific notes. This page covers the full tool reference and advanced configuration.

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
| **Trae** | Standard MCP config |
| **Cursor** | Standard MCP config |
| **ChatGPT & Codex** | Standard MCP config |
| **OpenCode** | Standard MCP config |
| **Manus** | Standard MCP config |
| **Claude.ai / Claude Desktop** | Native OAuth 2.1 (see [11-oauth](11-oauth.md)) |

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

### Claude.ai / Claude Desktop (OAuth)

These clients only accept OAuth 2.1 — API Keys cannot be passed directly.
OpenViking ships a native OAuth 2.1 implementation (DCR + PKCE + opaque
tokens, backed by SQLite, with a console-driven OTP authorization page) so
no external proxy is needed.

If you already have HTTPS configured, just connect to `https://your-server.com/mcp` — the client will walk you through the authorization flow automatically.

**See the [OAuth 2.1 Guide](11-oauth.md)** and **[Public Access Guide](12-public-access.md)** for:

- End-to-end flow (device-flow style: page displays a 6-character code,
  user confirms in the OpenViking console)
- HTTP (local) and HTTPS (production) deployment, including Caddy and nginx
  reverse-proxy templates plus a docker-compose example
- Connecting Claude.ai / Claude Desktop step by step
- `OPENVIKING_PUBLIC_BASE_URL` and the `oauth` config block
- Token model (`ovat_` / `ovrt_` / `ovac_` prefixes) and revocation

> The community [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth)
> Cloudflare Worker proxy is still around and remains a valid third-party
> option, but the native flow is recommended now: no extra deployment unit,
> no third-party trust boundary on the API key.


## Available MCP Tools

Once connected, OpenViking exposes 14 tools:

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `search` | Semantic search across memories, resources, and skills | `query`, `target_uri` (optional), `limit`, `min_score` |
| `read` | Read one or more `viking://` URIs | `uris` (single string or array) |
| `list` | List entries under a `viking://` directory | `uri`, `recursive` (optional) |
| `store` | Store messages into long-term memory (triggers extraction) | `messages` (list of `{role, content}`) |
| `add_resource` | Add a local file or URL as a resource (local files trigger a progressive upload flow) | `path`, `temp_file_id` (optional), `description` (optional), `watch_interval` (optional, minutes — auto-refresh cadence for remote URLs), `to` (optional, target `viking://resources/...` URI; required when `watch_interval > 0`) |
| `list_watches` | List watch tasks (auto-refresh subscriptions) visible to the current agent. Each entry shows target URI, refresh interval (minutes), active/paused status, and next scheduled execution time | none |
| `cancel_watch` | Cancel (delete) a watch task by its target URI. To change the cadence or pause temporarily, cancel and re-add with a new `watch_interval` | `to_uri` (must match the watch task's `to` value, e.g. `viking://resources/...`) |
| `grep` | Regex content search across `viking://` files | `uri`, `pattern` (string or array), `case_insensitive` |
| `glob` | Find files matching a glob pattern | `pattern`, `uri` (optional scope) |
| `forget` | Delete any `viking://` URI (use `search` to find it first; pass `recursive=true` to delete a directory) | `uri`, `recursive` (optional) |
| `code_outline` | Show a file's symbol structure (classes, functions, methods, line ranges) without reading bodies. Survey a file before deciding what to `read`. | `uri` (must be a `viking://` **file** URI) |
| `code_search` | Search symbol names (class / function / method) by substring across a `viking://` directory. Returns symbol type, class context, file URI, line range. Scans up to 200 source files. | `query`, `uri` (must be a `viking://` directory; narrow to subdir for deeper coverage) |
| `code_expand` | Return the full source of a single named symbol, avoiding reading the entire file. | `uri` (file), `symbol` (`bar` for top-level or `Foo.bar` for a method) |
| `health` | Check OpenViking service health | none |

> **Note**: MCP exposes the minimum closure for watch management (`list_watches` + `cancel_watch`). Pause / resume / trigger and the unified `update` verb are intentionally not exposed here — use the REST `/api/v1/watches/*` endpoints or the `ov task watch` CLI for those operations.

### Adding local-file resources (progressive upload)

The `add_resource` tool accepts both **remote URLs** and **local file paths**, handled differently:

- **Remote URL** (`http(s)://`, `git@`, `ssh://`, `git://`): single round-trip — the server fetches and ingests directly.
- **Local file path**: the tool returns a **two-step upload instruction** (plain prose with `Step 1` / `Step 2` formatting). The agent must:
  1. POST the file as `multipart/form-data` to the `temp_upload_signed` URL given in the response (URL embeds a one-shot token; 10-minute TTL by default). The server mints the `temp_file_id` at write time and returns it as JSON: `{"temp_file_id": "..."}`.
  2. Read `temp_file_id` from that response, then call `add_resource(temp_file_id="<id from step 1>")` again — the server resolves the file via `TempUploadStore` and ingests.

This lets any MCP client — including sandboxed environments without a local filesystem (Claude web, Manus, etc.) — push files into OpenViking without pre-installing the `ov` CLI. The signed endpoint shares the same persistence layer as the authenticated `temp_upload` route, so the `local` / `shared` upload modes (and multi-worker support via the `shared` mode) apply equally.

#### When you must set `OPENVIKING_PUBLIC_BASE_URL`

The upload URL the tool returns is resolved server-side in this order:

1. Environment variable `OPENVIKING_PUBLIC_BASE_URL`
2. `server.public_base_url` in `ov.conf`
3. Request headers `X-Forwarded-Host` / `X-Forwarded-Proto` (forwarded by the reverse-proxy chain)
4. Request `Host` header (direct connection)
5. Listen-address fallback: `http://{host}:{port}`

If the server runs behind a reverse proxy (nginx / cloud LB / k8s ingress / MCP proxy), **set `OPENVIKING_PUBLIC_BASE_URL` explicitly**. Layers 3–5 are inferred and break in these cases:

- The reverse proxy / MCP proxy does not forward `X-Forwarded-*` headers
- The server listens on `0.0.0.0` (fallback URL contains `0.0.0.0`, unreachable from agents)
- Multi-hop proxy with host rewriting

When the variable is unset and inference is used, the tool response automatically appends a hint asking the user to configure it. Docker Compose example:

```yaml
services:
  openviking:
    image: ghcr.io/volcengine/openviking:latest
    environment:
      OPENVIKING_PUBLIC_BASE_URL: "https://ov.your-domain.com"
```

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
