# OpenViking Memory MCP Server for Codex

Small Codex MCP example for explicit OpenViking memory operations.

This example intentionally stays MCP-only:

- no lifecycle hooks
- no background capture worker
- no writes to `~/.codex`
- no checked-in build output

Codex gets five tools:

- `memory_recall`
- `memory_store`
- `memory_write`
- `memory_forget`
- `memory_health`

## Files

- `.codex-plugin/plugin.json`: plugin metadata
- `.mcp.json`: MCP server wiring for Codex
- `src/memory-server.ts`: MCP server source
- `package.json`: build and start scripts
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

Supported environment overrides:

- `OPENVIKING_CONFIG_FILE`: alternate `ov.conf` path
- `OPENVIKING_API_KEY`: API key override
- `OPENVIKING_ACCOUNT`: account identity, default from `ov.conf`
- `OPENVIKING_USER`: user identity, default from `ov.conf`
- `OPENVIKING_AGENT_ID`: agent identity, default `codex`
- `OPENVIKING_TIMEOUT_MS`: HTTP timeout, default `15000`
- `OPENVIKING_RECALL_LIMIT`: recall result limit, default `6`
- `OPENVIKING_SCORE_THRESHOLD`: recall threshold, default `0.01`

## Tools

### `memory_recall`

Search OpenViking memory.

Parameters:

- `query`: search query
- `target_uri`: optional search scope, default `viking://user/memories`
- `limit`: optional max results
- `score_threshold`: optional minimum score

### `memory_store`

Store a memory by creating a short OpenViking session, adding the text, and
committing the session. Memory creation is extraction-dependent; the tool
reports when OpenViking commits the session but extracts zero memory items.

Parameters:

- `text`: information to store
- `role`: optional message role, default `user`

### `memory_write`

Save text verbatim at a specified memory URI and return the URI. Unlike
`memory_store`, does not run the extractor — content lands as-is, one file
per call. Prefer this for explicit "remember this fact" saves.

Parameters:

- `uri`: target memory URI (e.g. `viking://user/<id>/memories/preferences/mem_foo.md`)
- `content`: text to store verbatim
- `mode`: `replace` (default) or `append`

### `memory_forget`

Delete an exact memory URI. This example intentionally does not auto-delete by
query; use `memory_recall` first, then pass the exact URI.

Parameters:

- `uri`: exact `viking://user/.../memories/...` or `viking://agent/.../memories/...` URI

### `memory_health`

Check server reachability.

## Remove

```bash
codex mcp remove openviking-memory
```
