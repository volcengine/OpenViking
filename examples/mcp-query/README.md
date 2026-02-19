# OpenViking MCP Server

MCP (Model Context Protocol) HTTP server that exposes OpenViking RAG capabilities as tools.

## Tools

| Tool | Description |
|------|-------------|
| `query` | Full RAG pipeline — search + LLM answer generation |
| `search` | Semantic search only, returns matching documents |
| `add_resource` | Add files, directories, or URLs to the database |

## Quick Start

```bash
# Setup config
cp ov.conf.example ov.conf
# Edit ov.conf with your API keys

# Install dependencies
uv sync

# Start the server (streamable HTTP on port 2033)
uv run server.py
```

The server will be available at `http://127.0.0.1:2033/mcp`.

## Connect from Claude

```bash
# Add as MCP server in Claude CLI
claude mcp add --transport http openviking http://localhost:2033/mcp
```

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "openviking": {
      "type": "http",
      "url": "http://localhost:2033/mcp"
    }
  }
}
```

## Options

```
uv run server.py [OPTIONS]

  --config PATH       Config file path (default: ./ov.conf, env: OV_CONFIG)
  --data PATH         Data directory path (default: ./data, env: OV_DATA)
  --host HOST         Bind address (default: 127.0.0.1)
  --port PORT         Listen port (default: 2033, env: OV_PORT)
  --transport TYPE    streamable-http | stdio (default: streamable-http)
```

## Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector
# Connect to http://localhost:2033/mcp
```
