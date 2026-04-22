# OpenViking Memory Plugin for Claude Code

Long-term semantic memory for Claude Code, powered by [OpenViking](https://github.com/volcengine/OpenViking).

> Ported from the [OpenClaw context-engine plugin](../openclaw-plugin/) and adapted for Claude Code's plugin architecture (MCP + hooks).

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         Claude Code                              │
└────────┬──────────────────────────────────────┬──────────────────┘
         │                                      │
    UserPromptSubmit                           Stop
    (command hook)                        (command hook)
         │                                      │
  ┌──────▼──────────┐                  ┌────────▼─────────┐
  │  auto-recall.mjs│                  │ auto-capture.mjs │
  │                 │                  │                  │
  │ stdin:          │                  │ stdin:           │
  │  user_prompt    │                  │  transcript_path │
  │                 │                  │                  │
  │ 1. parse query  │                  │ 1. read transcript│
  │ 2. search OV    │                  │ 2. extract turns │
  │ 3. rank & pick  │                  │ 3. capture check │
  │ 4. read content │                  │ 4. session/extract│
  │                 │                  │                  │
  │ stdout:         │                  │ stdout:          │
  │  systemMessage  │                  │  decision:approve│
  │  (memories)     │                  │  (auto-captured) │
  └──────┬──────────┘                  └────────┬─────────┘
         │                                      │
         │         ┌──────────────┐             │
         └────────►│   OpenViking │◄────────────┘
                   │   Server     │
    MCP tools ────►│   (Python)   │
                   └──────────────┘

  ┌──────────────────────────────────────┐
  │  MCP Server (memory-server.ts)       │
  │  Tools for explicit use:             │
  │  • memory_recall (manual search)     │
  │  • memory_store  (manual store)      │
  │  • memory_forget (delete memories)   │
  │  • memory_health (health check)      │
  └──────────────────────────────────────┘
```

On `SessionStart`, the plugin bootstraps its Node runtime into `${CLAUDE_PLUGIN_DATA}/runtime`
when Claude exposes that variable. If not, it falls back to `~/.openviking/claude-code-memory-plugin/runtime`.
That makes the MCP adapter self-healing across marketplace installs without checking `node_modules`
into the plugin source tree.

## How It Works

### Runtime Bootstrap (transparent, on session start)

1. Claude starts a session → `SessionStart` hook fires
2. `bootstrap-runtime.mjs` hashes `package.json`, `package-lock.json`, and `servers/memory-server.js`
3. If the runtime directory is missing or stale, it copies runtime files there
4. Runs `npm ci --omit=dev` in that runtime directory
5. Writes `install-state.json` so later sessions can skip reinstall
6. MCP launcher can also bootstrap on demand if it starts before `SessionStart`

### Auto-Recall (transparent, every turn)

1. User submits a message → `UserPromptSubmit` hook fires
2. `auto-recall.mjs` reads `user_prompt` from stdin
3. Calls OpenViking `/api/v1/search/find` for both `viking://user/memories` and `viking://agent/memories`
4. Ranks results with query-aware scoring (leaf boost, preference boost, temporal boost, lexical overlap)
5. Reads full content for top-ranked leaf memories
6. Returns via `systemMessage` → Claude sees `<relevant-memories>` context transparently

### Auto-Capture (transparent, on stop)

1. Claude finishes a response → `Stop` hook fires
2. `auto-capture.mjs` reads `transcript_path` from stdin
3. Parses transcript and extracts recent turns, then keeps user turns only by default
4. Runs capture decision logic (semantic mode or keyword triggers) on the selected user turns
5. Creates OpenViking temp session → adds message → extracts memories
6. Memories stored automatically, no Claude tool call needed

### MCP Tools (explicit, on-demand)

The MCP server provides tools for when Claude or the user needs explicit memory operations:
- **memory_recall** — manual semantic search
- **memory_store** — manual memory storage
- **memory_forget** — delete memories by URI or query
- **memory_health** — check server status

## What's Different from the OpenClaw Plugin?

| Aspect | OpenClaw Plugin | Claude Code Plugin |
|--------|----------------|-------------------|
| Auto-recall | `before_prompt_build` hook + `prependContext` | `UserPromptSubmit` command hook + `systemMessage` |
| Auto-capture | `afterTurn` context-engine method | `Stop` command hook + transcript parsing |
| Explicit tools | `api.registerTool()` | MCP server (stdio transport) |
| Transparency | Both fully transparent | Both fully transparent — no extra Claude tool calls |
| Process mgmt | Plugin manages local subprocess | User starts OpenViking separately |
| Config | Plugin config schema with UI hints | Single JSON config file |
| JS runtime deps | Bundled in plugin process | Installed on first `SessionStart` into `${CLAUDE_PLUGIN_DATA}` or `~/.openviking/claude-code-memory-plugin` |

## Quick Start

### 1. Install OpenViking

```bash
pip install openviking
```

For mac
```bash
brew install pipx
pipx ensurepath
pipx install openviking
```

### 2. Create OpenViking Server Config (Local Mode)

If you use the plugin in `local` mode, prepare your local OpenViking server config first:

```bash
mkdir -p ~/.openviking
# Edit ov.conf: set your embedding API key, model, etc.
vim ~/.openviking/ov.conf
```

#### `~/.openviking/ov.conf` (Local Mode)

```json
{
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "/home/yourname/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local", "port": 1833 }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "<your-ark-api-key>",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "<your-ark-api-key>",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

> `root_api_key`: Once set, all HTTP requests must carry the `X-API-Key` header. Defaults to `null` in local mode (authentication disabled).

### 3. Create Claude Code Client Config

Create a dedicated client config file for the Claude Code plugin:

```bash
mkdir -p ~/.openviking/claude-code-memory-plugin
vim ~/.openviking/claude-code-memory-plugin/config.json
```

#### `~/.openviking/claude-code-memory-plugin/config.json` (Local Mode)

```json
{
  "mode": "local",
  "agentId": "claude-code",
  "recallLimit": 6,
  "captureMode": "semantic",
  "captureTimeoutMs": 30000,
  "captureAssistantTurns": false,
  "logRankingDetails": false
}
```

In `local` mode, the plugin connects to `http://127.0.0.1:${server.port}` from `ov.conf`
(defaults to `1933`). You may also set `apiKey` explicitly in client config; if omitted,
the plugin falls back to `server.root_api_key` from local `ov.conf` when present.

#### `~/.openviking/claude-code-memory-plugin/config.json` (Remote Mode)

```json
{
  "mode": "remote",
  "baseUrl": "https://your-openviking.example.com",
  "apiKey": "<your-api-key>",
  "agentId": "claude-code",
  "account": "default",
  "user": "default",
  "recallLimit": 6,
  "captureMode": "semantic",
  "captureTimeoutMs": 30000,
  "captureAssistantTurns": false,
  "logRankingDetails": false
}
```

### 4. Start OpenViking

```bash
openviking-server
```

### 5. Install Plugin

```bash
/plugin marketplace add Castor6/openviking-plugins
/plugin install claude-code-memory-plugin@openviking-plugin
```

### 6. Start a New Claude Session

```bash
claude
```

The first session automatically prepares the Node runtime for the MCP adapter. By default it uses
`${CLAUDE_PLUGIN_DATA}/runtime`, and falls back to `~/.openviking/claude-code-memory-plugin/runtime`
if Claude does not inject `CLAUDE_PLUGIN_DATA`. No manual `npm install` is required after marketplace install.

## Configuration

The plugin uses a dedicated client config file:

```bash
~/.openviking/claude-code-memory-plugin/config.json
```

Override the path via environment variable:
```bash
export OPENVIKING_CC_CONFIG_FILE="~/custom/path/config.json"
```

In `local` mode, the plugin also looks at the local OpenViking server config only for
fallback values such as `server.port` and `server.root_api_key`:

```bash
export OPENVIKING_CONFIG_FILE="~/custom/path/ov.conf"
```

### Local Mode

| Field | Description |
|-------|-------------|
| `mode` | Fixed to `local`, meaning the plugin connects to the local OpenViking server |
| `apiKey` | Optional override. If omitted, the plugin falls back to `server.root_api_key` in local `ov.conf` |

Local-mode connection behavior:

- `baseUrl` is derived as `http://127.0.0.1:${server.port}` from local `ov.conf`
- If local `ov.conf` is missing, the plugin falls back to `http://127.0.0.1:1933`

### Remote Mode

| Field | Description |
|-------|-------------|
| `mode` | Fixed to `remote`, meaning the plugin connects to an existing remote OpenViking server |
| `baseUrl` | Required. Remote OpenViking HTTP endpoint |
| `apiKey` | Optional OpenViking API key; required when the remote server enables authentication |
| `account` | OpenViking tenant account; required for tenant-scoped APIs |
| `user` | OpenViking tenant user; required for tenant-scoped APIs |

### Shared Behavior Fields

| Field | Default | Description |
|-------|---------|-------------|
| `agentId` | `claude-code` | Agent identity for memory isolation |
| `timeoutMs` | `15000` | HTTP request timeout for recall/general requests (ms) |
| `autoRecall` | `true` | Enable auto-recall on every user prompt |
| `recallLimit` | `6` | Max memories to inject per turn |
| `scoreThreshold` | `0.01` | Min relevance score (0-1) |
| `minQueryLength` | `3` | Skip recall for very short queries |
| `logRankingDetails` | `false` | Emit per-candidate `ranking_detail` logs for recall; otherwise only log a compact ranking summary |
| `autoCapture` | `true` | Enable auto-capture on stop |
| `captureMode` | `semantic` | `semantic` (always capture) or `keyword` (trigger-based) |
| `captureMaxLength` | `24000` | Max text length for capture |
| `captureTimeoutMs` | `30000` | HTTP request timeout for auto-capture requests (ms) |
| `captureAssistantTurns` | `false` | Include assistant turns in auto-capture input; default is user-only capture |
| `debug` | `false` | Enable hook debug logging |
| `debugLogPath` | `~/.openviking/logs/cc-hooks.log` | Override debug log file path |

## Hook Timeouts

The bundled hooks are intentionally asymmetric:

| Hook | Default timeout | Notes |
|------|-----------------|-------|
| `SessionStart` | `120s` | First session may need time to install runtime dependencies into `${CLAUDE_PLUGIN_DATA}` |
| `UserPromptSubmit` | `8s` | Auto-recall should stay fast so prompt submission is not blocked |
| `Stop` | `45s` | Gives auto-capture enough room to finish and persist incremental state |

Keep `captureTimeoutMs` lower than the `Stop` hook timeout so the script can fail gracefully and still update its incremental state.

## Debug Logging

When `debug=true` in client config or `OPENVIKING_DEBUG=1` is enabled, hook logs are written to `~/.openviking/logs/cc-hooks.log`.

- `auto-recall` now logs key stages plus a compact `ranking_summary` by default.
- Set `logRankingDetails=true` only when you need per-candidate scoring logs.
- For deep diagnosis, prefer the standalone scripts `scripts/debug-recall.mjs` and `scripts/debug-capture.mjs` instead of leaving verbose hook logging on all the time.

## Runtime Dependency Bootstrap

The plugin keeps its runtime npm dependencies in a dedicated runtime directory:

- Prefers `${CLAUDE_PLUGIN_DATA}/runtime` and falls back to `~/.openviking/claude-code-memory-plugin/runtime`
- `SessionStart` installs or refreshes dependencies with `npm ci --omit=dev`
- `install-state.json` records the active manifest and server hashes
- MCP startup can also perform the same bootstrap itself, so first-run installs do not depend on hook ordering
- If installation fails, Claude Code remains usable; only the explicit MCP tools stay unavailable until the next successful bootstrap

## Plugin Structure

```
claude-code-memory-plugin/
├── .claude-plugin/
│   └── plugin.json              # Plugin manifest
├── hooks/
│   └── hooks.json               # SessionStart + UserPromptSubmit + Stop hooks
├── scripts/
│   ├── config.mjs               # Shared config loader
│   ├── runtime-common.mjs       # Shared runtime paths + install state helpers
│   ├── bootstrap-runtime.mjs    # SessionStart installer for runtime deps
│   ├── start-memory-server.mjs  # Launches MCP server from plugin data runtime
│   ├── auto-recall.mjs          # Auto-recall hook script
│   └── auto-capture.mjs         # Auto-capture hook script
├── servers/
│   └── memory-server.js         # Compiled MCP server
├── src/
│   └── memory-server.ts         # MCP server source
├── .mcp.json                    # MCP server definition
├── package.json
├── tsconfig.json
└── README.md
```

## Relationship to Claude Code's Built-in Memory

Claude Code has a built-in auto-memory system using `MEMORY.md` files. This plugin **complements** that system:

| Feature | Built-in Memory | OpenViking Plugin |
|---------|----------------|-------------------|
| Storage | Flat markdown files | Vector DB + structured extraction |
| Search | Loaded into context entirely | Semantic similarity search |
| Scope | Per-project | Cross-project, cross-session |
| Capacity | ~200 lines (context limit) | Unlimited (server-side storage) |
| Extraction | Manual rules | AI-powered entity extraction |

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| No memories recalled | Server not running | Start OpenViking server |
| Auto-capture extracts 0 | Wrong API key / model | Check `ov.conf` embedding config |
| MCP tools not available | First-run runtime install failed | Start a new Claude session to retry bootstrap and inspect SessionStart stderr for the npm failure |
| Repeated auto-capture of old context | `Stop` hook timed out before incremental state was saved | Keep `captureAssistantTurns=false`, raise the `Stop` hook timeout, and keep `captureTimeoutMs` below that hook timeout |
| Hook timeout | Server slow / unreachable | Increase the `Stop` hook timeout in `hooks/hooks.json` and tune `captureTimeoutMs` in client config |
| Logs too verbose | Detailed recall ranking logs are enabled | Leave `logRankingDetails=false` for normal use and use the debug scripts for one-off inspection |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
