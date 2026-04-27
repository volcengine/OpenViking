# MCP Integration Guide

OpenViking can be used as an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server, allowing any MCP-compatible client to access its memory and resource capabilities.

## Choosing the Integration Pattern

`examples/skills` is not the only or default integration path for agents.
On current `main`, OpenViking has three common agent integration patterns:

| Pattern | Best for | What you get |
|---|---|---|
| MCP server | General-purpose agent hosts that can call MCP tools | Cross-host integration, shared server deployment, explicit tool access |
| Host-specific plugin/example | Hosts that expose lifecycle hooks beyond plain tool calls | Deeper recall/capture behavior, host-native UX, tighter runtime integration |
| Embedded SDK / HTTP SDK | Custom applications you own end to end | Full control over ingestion, retrieval, sessions, and storage from application code |

### Where `examples/skills` fits

Use `examples/skills` when your host already has a native "skill", "tool pack", or prompt-driven tool integration mechanism and you want a lightweight, explicit workflow such as:

- add data into OpenViking
- search OpenViking context on demand
- operate the OpenViking server from an agent

Those examples are intentionally narrow. They are a good pattern for explicit tool calls, but they are not a replacement for:

- the MCP server, when you want a general integration path across Claude Code, Cursor, Claude Desktop, OpenClaw, and similar hosts
- host-specific plugins such as the OpenClaw context-engine example or the Claude Code memory plugin, when you need automatic recall/capture tied to the host lifecycle
- the SDK, when you are embedding OpenViking directly into your own Python service or application

### Quick recommendations

- Start with MCP if you want one integration story that works across multiple agent hosts.
- Use `examples/skills` if your host already has a strong native skill abstraction and you only need explicit OpenViking tool calls.
- Use a host-specific plugin/example if you need lifecycle-aware memory behavior, not just callable tools.
- Use the SDK if you are building the application itself and do not need MCP at all.

## Transport Modes

OpenViking supports two MCP transport modes:

| | HTTP (SSE) | stdio |
|---|---|---|
| **How it works** | Single long-running server process; clients connect via HTTP | Host spawns a new OpenViking process per session |
| **Multi-session safe** | ✅ Yes — single process, no lock contention | ⚠️ **No** — multiple processes contend for the same data directory |
| **Recommended for** | Production, multi-agent, multi-session | Single-session local development only |
| **Setup complexity** | Requires running `openviking-server` separately | Zero setup — host manages the process |

### Choosing the Right Transport

- **Use HTTP** if your host opens multiple sessions, runs multiple agents, or needs concurrent access.
- **Use stdio** only for single-session, single-agent local setups where simplicity is the priority.

> ⚠️ **Important:** When an MCP host spawns multiple stdio OpenViking processes (e.g., one per chat session), all instances compete for the same underlying data directory. This causes **lock/resource contention** in the storage layer (AGFS and VectorDB).
>
> Symptoms include misleading errors such as:
> - `Collection 'context' does not exist`
> - `Transport closed`
> - Intermittent search failures
>
> **The root cause is not a broken index** — it is multiple processes contending for the same storage files. Switch to HTTP mode to resolve this. See [Troubleshooting](#troubleshooting) for details.

## Setup

### Prerequisites

1. OpenViking installed (`pip install openviking` or from source)
2. A valid configuration file (see [Configuration Guide](01-configuration.md))
3. For HTTP mode: `openviking-server` running (see [Deployment Guide](03-deployment.md))

### HTTP Mode (Recommended)

Start the OpenViking server first:

```bash
openviking-server --config /path/to/config.yaml
# Default: http://localhost:1933
```

Then configure your MCP client to connect via HTTP.

### stdio Mode

No separate server needed — the MCP host spawns OpenViking directly.

## Client Configuration

### Claude Code (CLI)

**HTTP mode:**

```bash
claude mcp add openviking \
  --transport sse \
  "http://localhost:1933/mcp"
```

**stdio mode:**

```bash
claude mcp add openviking \
  --transport stdio \
  -- python -m openviking.server --transport stdio \
     --config /path/to/config.yaml
```

### Claude Desktop

Edit `claude_desktop_config.json`:

**HTTP mode:**

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://localhost:1933/mcp"
    }
  }
}
```

**stdio mode:**

```json
{
  "mcpServers": {
    "openviking": {
      "command": "python",
      "args": [
        "-m", "openviking.server",
        "--transport", "stdio",
        "--config", "/path/to/config.yaml"
      ]
    }
  }
}
```

### Cursor

In Cursor Settings → MCP:

**HTTP mode:**

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://localhost:1933/mcp"
    }
  }
}
```

**stdio mode:**

```json
{
  "mcpServers": {
    "openviking": {
      "command": "python",
      "args": [
        "-m", "openviking.server",
        "--transport", "stdio",
        "--config", "/path/to/config.yaml"
      ]
    }
  }
}
```

### OpenClaw

In your OpenClaw configuration (`openclaw.json` or `openclaw.yaml`):

**HTTP mode (recommended):**

```json
{
  "mcp": {
    "servers": {
      "openviking": {
        "url": "http://localhost:1933/mcp"
      }
    }
  }
}
```

**stdio mode:**

```json
{
  "mcp": {
    "servers": {
      "openviking": {
        "command": "python",
        "args": [
          "-m", "openviking.server",
          "--transport", "stdio",
          "--config", "/path/to/config.yaml"
        ]
      }
    }
  }
}
```

## Available MCP Tools

Once connected, OpenViking exposes the following MCP tools:

| Tool | Description |
|------|-------------|
| `search` | Semantic search across memories and resources |
| `add_memory` | Store a new memory |
| `add_resource` | Add a resource (file, text, URL) |
| `get_status` | Check system health and component status |
| `list_memories` | Browse stored memories |
| `list_resources` | Browse stored resources |

Refer to OpenViking's tool documentation for full parameter details.

## Troubleshooting

### `Collection 'context' does not exist`

**Likely cause:** Multiple stdio MCP instances contending for the same data directory.

**Fix:** Switch to HTTP mode. If you must use stdio, ensure only one OpenViking process accesses a given data directory at a time.

### `Transport closed`

**Likely cause:** The MCP stdio process crashed or was killed due to resource contention. Can also occur when a client holds a stale connection after the backend was restarted.

**Fix:**
1. Switch to HTTP mode to avoid contention.
2. If using HTTP: reload the MCP connection in your client (restart the session or reconnect).

### Connection refused on HTTP endpoint

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
- [Related issue: stdio contention (#473)](https://github.com/volcengine/OpenViking/issues/473)
