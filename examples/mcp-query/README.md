# OpenViking MCP Server

MCP (Model Context Protocol) HTTP server that exposes a shared OpenViking HTTP
backend as MCP tools.

## Tools

| Tool | Description |
|------|-------------|
| `search` | Semantic search only, returns matching documents |
| `add_resource` | Add files, directories, or URLs through the HTTP backend |
| `get_status` | Fetch backend health and observer status |

## Quick Start

```bash
# First start the main OpenViking HTTP server
openviking-server --config ~/.openviking/ov.conf

# Install example dependencies
uv sync

# Start the MCP server
uv run server.py \
  --backend-url http://127.0.0.1:1933 \
  --account brianle \
  --user brianle \
  --agent-id mcp
```

The server will be available at `http://127.0.0.1:2033/mcp`.

## Connect from Claude

```bash
claude mcp add openviking --transport http http://127.0.0.1:2033/mcp
```

## Options

```text
uv run server.py [OPTIONS]

  --backend-url URL   OpenViking backend URL (default: http://127.0.0.1:1933,
                      env: OV_BACKEND_URL)
  --host HOST         Bind address (default: 127.0.0.1)
  --port PORT         Listen port (default: 2033, env: OV_PORT)
  --transport TYPE    streamable-http | stdio (default: streamable-http)
  --account ID        OpenViking account header (env: OV_ACCOUNT)
  --user ID           OpenViking user header (env: OV_USER)
  --agent-id ID       OpenViking agent header (env: OV_AGENT_ID, default: mcp)
  --default-uri URI   Default search scope (env: OV_DEFAULT_URI)
```
