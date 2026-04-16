# OpenViking Memory Plugin for Claude Code

Long-term semantic memory for Claude Code, powered by [OpenViking](https://github.com/volcengine/OpenViking).

Provide a plugin marketplace repository for one-click installation: [openviking-plugins](https://github.com/Castor6/openviking-plugins)

> Ported from the [OpenClaw context-engine plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin) and adapted for Claude Code's plugin architecture (MCP + hooks).

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

### 2. Create Config

If you don't already have `~/.openviking/ov.conf`(Can override the default path via the environment variable `OPENVIKING_CONFIG_FILE`), create it:

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
    "agfs": { "backend": "local" }
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
> For Windows system paths in the workspace, use / instead of \, for example: `D:/.openviking/data`

Optionally add a `claude_code` section for plugin-specific overrides:

```json
{
  "claude_code": {
    "agentId": "claude-code",
    "recallLimit": 6,
    "captureMode": "semantic",
    "captureTimeoutMs": 30000,
    "captureAssistantTurns": false,
    "logRankingDetails": false
  }
}
```

### 3. Start OpenViking

```bash
openviking-server
```

### 4. Install Plugin

```bash
/plugin marketplace add Castor6/openviking-plugins
/plugin install claude-code-memory-plugin@openviking-plugin
```

### 5. Start a New Claude Session

```bash
claude
```

The first session automatically prepares the Node runtime for the MCP adapter. By default it uses
`${CLAUDE_PLUGIN_DATA}/runtime`, and falls back to `~/.openviking/claude-code-memory-plugin/runtime`
if Claude does not inject `CLAUDE_PLUGIN_DATA`. No manual `npm install` is required after marketplace install.

## Configuration

Uses the same `~/.openviking/ov.conf` as the OpenViking server and OpenClaw plugin.

Override the path via environment variable:
```bash
export OPENVIKING_CONFIG_FILE="~/custom/path/ov.conf"
```

**Connection info** is read from ov.conf's `server` section:

| ov.conf field | Used as | Description |
|---------------|---------|-------------|
| `server.host` + `server.port` | `baseUrl` | Derives `http://{host}:{port}` |
| `server.root_api_key` | `apiKey` | API key for authentication |

**Plugin overrides** go in an optional `claude_code` section:

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

## Hook Timeouts

The bundled hooks are intentionally asymmetric:

| Hook | Default timeout | Notes |
|------|-----------------|-------|
| `SessionStart` | `120s` | First session may need time to install runtime dependencies into `${CLAUDE_PLUGIN_DATA}` |
| `UserPromptSubmit` | `8s` | Auto-recall should stay fast so prompt submission is not blocked |
| `Stop` | `45s` | Gives auto-capture enough room to finish and persist incremental state |

Keep `claude_code.captureTimeoutMs` lower than the `Stop` hook timeout so the script can fail gracefully and still update its incremental state.

## Debug Logging

When `claude_code.debug` or `OPENVIKING_DEBUG=1` is enabled, hook logs are written to `~/.openviking/logs/cc-hooks.log`.

- `auto-recall` now logs key stages plus a compact `ranking_summary` by default.
- Set `claude_code.logRankingDetails=true` only when you need per-candidate scoring logs.
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
| Hook timeout | Server slow / unreachable | Increase the `Stop` hook timeout in `hooks/hooks.json` and tune `claude_code.captureTimeoutMs` in `ov.conf` |
| Logs too verbose | Detailed recall ranking logs are enabled | Leave `logRankingDetails=false` for normal use and use the debug scripts for one-off inspection |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
