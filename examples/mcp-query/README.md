# OpenViking MCP Bridge

MCP (Model Context Protocol) bridge that exposes OpenViking capabilities as MCP tools.

It supports two deployment modes:

- Embedded/local mode: the bridge opens a local OpenViking workspace directly
- Remote HTTP bridge mode: the bridge talks to an existing `openviking-server` over HTTP

For Codex users with OpenViking already running on another machine, the remote HTTP bridge mode is the recommended setup.

## Tools

| Tool | Description |
|------|-------------|
| `query` | Full RAG pipeline: search + LLM answer generation. Optional; requires local `ov.conf` with `vlm` configured |
| `search` | Semantic search only, returns matching documents |
| `add_resource` | Add files, directories, or URLs to OpenViking |
| `memory_start_session` | Create a manual OpenViking memory session |
| `memory_add_turn` | Append an important user/assistant turn into that session |
| `memory_get_session` | Inspect a memory session |
| `memory_commit_session` | Commit a memory session so OpenViking extracts memories |
| `memory_delete_session` | Delete a memory session |

## Quick Start

### Remote OpenViking HTTP Server

If OpenViking is already running elsewhere, start the MCP bridge locally and point it at that server:

```bash
# Install dependencies
uv sync

# Start the bridge against an existing OpenViking server
uv run server.py --url http://YOUR_SERVER:1933 --api-key YOUR_USER_KEY
```

If you only need retrieval and ingestion, that is enough.

If you also want the optional `query` tool, add a local `ov.conf` containing `vlm.api_base` and `vlm.model`:

```bash
uv run server.py --url http://YOUR_SERVER:1933 --api-key YOUR_USER_KEY --config ./ov.conf
```

### Embedded/Local Mode

If you want the bridge to open a local OpenViking workspace directly:

```bash
# Setup config
cp ov.conf.example ov.conf
# Edit ov.conf with your API keys

# Start the bridge (streamable HTTP on port 2033)
uv run server.py
```

The bridge will be available at `http://127.0.0.1:2033/mcp`.

## Connect from Codex

```bash
codex mcp add openviking --url http://127.0.0.1:2033/mcp
```

If you want to verify it was added:

```bash
codex mcp list
```

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

  --config PATH       Local ov.conf for optional query LLM config (default: ./ov.conf, env: OV_CONFIG)
  --data PATH         Local OpenViking data directory for embedded mode (default: ./data, env: OV_DATA)
  --url URL           Existing OpenViking HTTP server URL (env: OV_SERVER_URL)
  --api-key KEY       Existing OpenViking HTTP API key (env: OV_API_KEY)
  --account ID        Existing OpenViking account header for root-key access (env: OV_ACCOUNT)
  --user ID           Existing OpenViking user header for root-key access (env: OV_USER)
  --agent-id ID       Existing OpenViking agent header (env: OV_AGENT_ID)
  --host HOST         Bind address for the MCP bridge (default: 127.0.0.1)
  --port PORT         MCP bridge listen port (default: 2033, env: OV_PORT)
  --transport TYPE    streamable-http | stdio (default: streamable-http)
```

## Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector
# Connect to http://localhost:2033/mcp
```

## Memory Behavior

This bridge does not automatically save Codex conversation turns into OpenViking.

Claude's memory plugin does that through explicit lifecycle hooks (`SessionStart`, `Stop`, `SessionEnd`) that create an OpenViking session, append turns, and commit the session for memory extraction. Codex MCP integration does not provide that same hook flow here, so this bridge currently focuses on retrieval and resource ingestion.

What it can do today is manual memory capture through MCP tools:

1. Call `memory_start_session`
2. Call `memory_add_turn` for the important exchanges you want to keep
3. Call `memory_commit_session` when you want OpenViking to extract and index those memories

Example flow:

```text
memory_start_session()
→ {"session_id": "..."}

memory_add_turn(
  session_id="...",
  user_message="Kalev prefers Codex + OpenViking over Claude-only workflows",
  assistant_message="We set up an MCP bridge in front of the remote OpenViking server"
)

memory_commit_session(session_id="...")
```
