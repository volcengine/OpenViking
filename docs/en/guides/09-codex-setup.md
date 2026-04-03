# Codex Setup Guide

This guide shows how to use OpenViking with Codex when your main OpenViking server is already running over HTTP.

## Overview

Codex consumes external tools through MCP.

If your OpenViking deployment is only running the normal HTTP API server (`openviking-server` on port `1933` by default), the easiest Codex setup is:

1. Keep your existing OpenViking server running
2. Start the local MCP bridge from `examples/mcp-query/server.py`
3. Register that local bridge with Codex

This keeps your current Claude/OpenViking setup intact and adds Codex access on top.

## Architecture

```text
Codex CLI
  ↓ MCP
Local MCP bridge (examples/mcp-query/server.py)
  ↓ HTTP
Remote OpenViking server (openviking-server)
```

## Prerequisites

- OpenViking server already running and reachable over HTTP
- Python available on the Codex machine
- Codex CLI installed
- The `mcp` Python package installed

Install `mcp` if needed:

```bash
python -m pip install mcp
```

## Bridge Setup

### 1. Set environment variables

Use your OpenViking server URL and API key.

```powershell
[Environment]::SetEnvironmentVariable('OV_SERVER_URL', 'http://YOUR_SERVER:1933', 'User')
[Environment]::SetEnvironmentVariable('OV_API_KEY', 'YOUR_OPENVIKING_KEY', 'User')
```

If your key is a normal user key, that is enough.

If your key is a root key, also set the target tenant:

```powershell
[Environment]::SetEnvironmentVariable('OV_ACCOUNT', 'default', 'User')
[Environment]::SetEnvironmentVariable('OV_USER', 'your-user-id', 'User')
```

Open a new terminal, or refresh the current session:

```powershell
$env:OV_SERVER_URL = [Environment]::GetEnvironmentVariable('OV_SERVER_URL', 'User')
$env:OV_API_KEY = [Environment]::GetEnvironmentVariable('OV_API_KEY', 'User')
$env:OV_ACCOUNT = [Environment]::GetEnvironmentVariable('OV_ACCOUNT', 'User')
$env:OV_USER = [Environment]::GetEnvironmentVariable('OV_USER', 'User')
```

### 2. Start the local MCP bridge

From the OpenViking repository:

```powershell
cd C:\Dev\OpenViking
python examples\mcp-query\server.py --url $env:OV_SERVER_URL --api-key $env:OV_API_KEY --account $env:OV_ACCOUNT --user $env:OV_USER
```

If you use a normal user key instead of a root key, you can omit `--account` and `--user`.

Expected log output includes:

- `mode: http-bridge`
- `ov url: http://...:1933`
- `endpoint: http://127.0.0.1:2033/mcp`

Keep this bridge terminal running while Codex uses OpenViking.

## Codex Registration

In another terminal:

```powershell
codex mcp add openviking --url http://127.0.0.1:2033/mcp
codex mcp list
```

`codex mcp list` should show an `openviking` entry with URL `http://127.0.0.1:2033/mcp`.

If Codex shows `Auth Unsupported` for this bridge, that is expected. Codex is talking to the local bridge without separate bridge-level authentication.

## Available Tools

The MCP bridge exposes:

- `search`: semantic search in OpenViking
- `add_resource`: add files, directories, or URLs into OpenViking
- `query`: optional search + LLM answer generation
- `memory_start_session`: create a manual memory session
- `memory_add_turn`: append an important user/assistant turn
- `memory_get_session`: inspect a session
- `memory_commit_session`: extract and index memories from a session
- `memory_delete_session`: remove a session

## First Test

Ask Codex something explicit:

```text
Use the openviking MCP tools to search for "OpenViking memory" and summarize what you find.
```

Or search for a specific project:

```text
Use the openviking MCP tools to search for "KADE.Voice" and summarize the top matches.
```

## Manual Memory Workflow

This bridge supports manual memory capture, not automatic conversation capture.

Typical flow:

1. `memory_start_session`
2. `memory_add_turn`
3. `memory_commit_session`

Example:

```text
Use the openviking MCP tools to:
1. start a memory session
2. add a turn saying I prefer Codex with OpenViking over Claude-only workflows
3. commit the session
4. tell me the session id
```

## Automatic Memory Behavior

This Codex bridge does **not** automatically save every Codex conversation turn.

Claude's OpenViking memory plugin uses dedicated lifecycle hooks such as `SessionStart`, `Stop`, and `SessionEnd`. The Codex MCP bridge does not receive those hook events, so automatic session capture is not available here yet.

What is supported today:

- manual memory save through MCP tools
- normal OpenViking retrieval and resource ingestion

## Optional `query` Tool

The `query` tool requires local LLM config in `ov.conf` because the bridge itself must call a model after search.

If you only need search, add-resource, and manual memory tools, you do **not** need local `ov.conf`.

If you want `query`, create a local `ov.conf` containing at least:

```json
{
  "vlm": {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "your-api-key",
    "api_base": "https://api.openai.com/v1"
  }
}
```

Then start the bridge with:

```powershell
python examples\mcp-query\server.py --url $env:OV_SERVER_URL --api-key $env:OV_API_KEY --account $env:OV_ACCOUNT --user $env:OV_USER --config .\ov.conf
```

## Troubleshooting

### Codex can see the MCP server, but searches fail

If your OpenViking key is a root key, restart the bridge with `--account` and `--user`.

### `python examples\mcp-query\server.py --help` fails with missing `mcp`

Install the runtime:

```bash
python -m pip install mcp
```

### Codex added the MCP server, but nothing happens

Make sure the bridge process is still running locally on `127.0.0.1:2033`.

### I want true auto-save memory like Claude

That needs a separate Codex-side lifecycle integration or wrapper that records turns and calls the session APIs automatically. The current guide covers the supported manual workflow only.
