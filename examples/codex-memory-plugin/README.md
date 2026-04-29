# OpenViking Memory MCP Server for Codex

Small Codex MCP example for explicit OpenViking memory operations.

This example intentionally stays MCP-only:

- no lifecycle hooks
- no background capture worker
- no writes to `~/.codex`
- no checked-in build output

Codex gets four tools:

- `openviking_recall`
- `openviking_store`
- `openviking_forget`
- `openviking_health`

## Files

- `.codex-plugin/plugin.json`: plugin metadata
- `.mcp.json`: MCP server wiring for Codex
- `src/memory-server.ts`: MCP server source
- `src/config.ts`: config loader and precedence resolver
- `tests/config.test.mjs`: config-loading tests (`node --test`)
- `package.json`: build, start, and test scripts
- `tsconfig.json`: TypeScript build config

## Prerequisites

- Codex CLI
- OpenViking server
- Node.js 22+

Start OpenViking before using the MCP server:

```bash
openviking-server --config ~/.openviking/ov.conf
```

## Build

```bash
cd examples/codex-memory-plugin
npm install
npm run build
```

## Install in Codex

Use the built server:

```bash
codex mcp add openviking-memory -- \
  node /ABS/PATH/TO/OpenViking/examples/codex-memory-plugin/servers/memory-server.js
```

Or copy `.mcp.json` into a Codex workspace and adjust the `cwd` path if needed.

## Config

The server reads OpenViking connection settings from `~/.openviking/ov.conf`.

Per-plugin overrides may live in `~/.openviking/codex-memory-plugin/config.json`.
This is optional; when the file is absent the server falls back to `ov.conf`
defaults and the literal defaults below.

Resolution precedence (highest wins):

1. environment variable
2. client config (`~/.openviking/codex-memory-plugin/config.json`)
3. `ov.conf` default
4. built-in literal default

Supported client config keys (all optional):

- `apiKey`
- `accountId`
- `userId`
- `agentId` (default: `codex`)
- `timeoutMs` (default: `15000`)
- `recallLimit` (default: `6`)
- `scoreThreshold` (default: `0.01`)

Example `~/.openviking/codex-memory-plugin/config.json`:

```json
{
  "agentId": "codex-prod",
  "recallLimit": 8
}
```

Supported environment overrides:

- `OPENVIKING_CONFIG_FILE`: alternate `ov.conf` path
- `OPENVIKING_CODEX_CONFIG_FILE`: alternate client config path
- `OPENVIKING_API_KEY`: API key override
- `OPENVIKING_ACCOUNT`: account identity, default from client config or `ov.conf`
- `OPENVIKING_USER`: user identity, default from client config or `ov.conf`
- `OPENVIKING_AGENT_ID`: agent identity, default from client config or `ov.conf`
- `OPENVIKING_TIMEOUT_MS`: HTTP timeout, default `15000`
- `OPENVIKING_RECALL_LIMIT`: recall result limit, default `6`
- `OPENVIKING_SCORE_THRESHOLD`: recall threshold, default `0.01`

## Tools

### `openviking_recall`

Search OpenViking memory.

Parameters:

- `query`: search query
- `target_uri`: optional search scope, default `viking://user/memories`
- `limit`: optional max results
- `score_threshold`: optional minimum score

### `openviking_store`

Store a memory by creating a short OpenViking session, adding the text, and
committing the session. Memory creation is extraction-dependent; the tool
reports when OpenViking commits the session but extracts zero memory items.

Parameters:

- `text`: information to store
- `role`: optional message role, default `user`

### `openviking_forget`

Delete an exact memory URI. This example intentionally does not auto-delete by
query; use `openviking_recall` first, then pass the exact URI.

Parameters:

- `uri`: exact `viking://user/.../memories/...` or `viking://agent/.../memories/...` URI

### `openviking_health`

Check server reachability.

## Remove

```bash
codex mcp remove openviking-memory
```
