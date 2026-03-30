# OpenViking Memory Plugin for Codex

Codex MCP server example that exposes OpenViking memories as explicit tools.

This example is repo-only. It does not edit `~/.codex/config.toml` for you.

## What It Does

- Exposes four MCP tools for Codex:
  - `memory_recall`
  - `memory_store`
  - `memory_forget`
  - `memory_health`
- Reads OpenViking connection details from `~/.openviking/ov.conf`
- Marks recalled memory URIs as `used()` before a fire-and-forget `commit()`
- Contributes retrieval feedback to OpenViking's hotness ranking without blocking Codex

## Prerequisites

- Codex CLI installed
- OpenViking HTTP server running
- Node.js 22+

Start OpenViking first if needed:

```bash
openviking-server --config ~/.openviking/ov.conf
```

## Build

From this directory:

```bash
npm ci
npm run build
```

That produces `servers/memory-server.js`, the MCP entrypoint Codex will launch.

## Add To Codex

Add the MCP server with the verified Codex CLI shape:

```bash
codex mcp add openviking-memory -- \
  node /ABS/PATH/TO/OpenViking/examples/codex-memory-plugin/servers/memory-server.js
```

Example using your local repository checkout:

```bash
codex mcp add openviking-memory -- \
  node /path/to/OpenViking/examples/codex-memory-plugin/servers/memory-server.js
```

List configured MCP servers:

```bash
codex mcp list
```

Remove it later if needed:

```bash
codex mcp remove openviking-memory
```

## Optional Environment Overrides

The server defaults to `~/.openviking/ov.conf`. You can override behavior with env vars when adding the MCP server:

```bash
codex mcp add openviking-memory \
  --env OPENVIKING_AGENT_ID=codex-local \
  --env OPENVIKING_TIMEOUT_MS=20000 \
  -- node /ABS/PATH/TO/OpenViking/examples/codex-memory-plugin/servers/memory-server.js
```

Supported overrides:

- `OPENVIKING_CONFIG_FILE`
- `OPENVIKING_AGENT_ID`
- `OPENVIKING_TIMEOUT_MS`
- `OPENVIKING_RECALL_LIMIT`
- `OPENVIKING_SCORE_THRESHOLD`

## How Recall Feedback Works

`memory_recall` searches OpenViking, returns the selected memories to Codex, and also starts a background sequence:

1. `POST /api/v1/sessions`
2. `POST /api/v1/sessions/{id}/used` with recalled `viking://` URIs
3. `POST /api/v1/sessions/{id}/commit`
4. `DELETE /api/v1/sessions/{id}`

This is fire-and-forget. Tool responses do not wait on the feedback loop.

## Notes

- This example gives Codex explicit tools only. It does not implement transparent auto-recall hooks.
- `memory_recall` only marks the memories it actually returns as used, which is higher-signal than broad auto-recall marking.
- If your OpenViking server requires auth, the MCP server reads `root_api_key` from `ov.conf`.

## Troubleshooting

- MCP server not starting: run `npm ci && npm run build` in this directory first
- OpenViking request failures: verify `openviking-server` is reachable at the host and port in `ov.conf`
- No memories returned: confirm you have indexed data under `viking://user/memories` or `viking://agent/memories`
