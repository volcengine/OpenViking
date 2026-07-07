# OpenViking Memory Extension for Pi Coding Agent

Long-term semantic memory for [pi](https://github.com/earendil-works/pi) sessions, powered by [OpenViking](https://github.com/volcengine/OpenViking). Recall happens automatically before every prompt, capture happens after every turn, and sessions are committed for persistent memory extraction — all via pi's native extension API.

> Design informed by lessons from all three OpenViking agent plugins: synchronous recall from OpenClaw, production-hardened capture/ranking from Claude Code, and anti-patterns dodged from Hermes's stale prefetch approach. See [DESIGN.md](./DESIGN.md) for the full design spec with comparison tables and event flow diagrams.

## Quick Start

### Prerequisites

- **pi coding agent** installed (`npm i -g @earendil-works/pi-coding-agent`)
- **Node.js 18+** (for the extension's TypeScript runtime)
- **An OpenViking server** reachable — local or remote

### 1. Have an OpenViking server reachable

Either run one locally or point at a remote one. The [quickstart guide](../../docs/en/getting-started/02-quickstart.md) walks through both options. Default port is `1933`; local mode runs without authentication.

Verify it's up:

```bash
curl http://localhost:1933/health   # or your remote URL
```

### 2. Install the extension

Use the shared installer:

```bash
bash examples/memory-plugin-shared/install.sh --harness pi
```

The installer copies the extension to `~/.pi/agent/extensions/openviking` and registers it with `pi install`. The extension loads on next `pi` invocation.

### 3. Configure (optional)

Credentials are resolved from `OPENVIKING_*` environment variables, `~/.openviking/ovcli.conf`, then `~/.openviking/ov.conf`. Run the setup wizard when you need to configure a remote server:

```bash
node ~/.pi/agent/extensions/openviking/scripts/setup.mjs
```

`~/.pi/agent/extensions/openviking/config.json` is for behavior knobs only:

```json
{
  "enabled": true,
  "syncTurns": true,
  "recallTokenBudget": 2000,
  "scoreThreshold": 0.35,
  "minQueryLength": 3,
  "profileTokenBudget": 10000,
  "resumeContextBudget": 32000,
  "commitTokenThreshold": 20000
}
```

Credential environment variables:

| Env Var | Meaning |
|---------|---------|
| `OPENVIKING_URL` | OpenViking server URL |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | Bearer token |
| `OPENVIKING_ACCOUNT` | Trusted-mode account |
| `OPENVIKING_USER` | Trusted-mode user |
| `OPENVIKING_PEER_ID` | Actor peer id |

API keys are sent as `Authorization: Bearer ...`. `OPENVIKING_PEER_ID` is sent as `X-OpenViking-Actor-Peer` and stored as `peer_id` on captured session messages.

### 4. Start Pi

```bash
pi
```

The extension shows an `[OpenViking]` status line on startup. Tools (`viking_search`, `viking_remember`, etc.) are registered automatically. Memories persist across sessions — no additional setup.

## Configuration Reference

### Tuning fields

All fields below live in `config.json`. Defaults are shown.

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `enabled`                | `true`     | Set `false` to disable the extension entirely                            |
| `syncTurns`              | `true`     | Enable auto-capture of conversation turns                                |

### Recall tuning

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `recallTokenBudget`      | `2000`     | Token budget for inline recall content                                   |
| `recallMaxContentChars`  | `500`      | Per-item content cap for search results                                  |
| `recallPreferAbstract`   | `true`     | Prefer L0 abstract over L2 full body when available                      |
| `recallLimit`            | `6`        | Max memories to inject per prompt                                        |
| `scoreThreshold`         | `0.35`     | Min relevance score (0–1)                                                |
| `minQueryLength`         | `3`        | Skip recall for queries shorter than N characters                        |

### Capture tuning

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `captureMode`            | `"semantic"` | `"semantic"` (always capture) or `"keyword"` (trigger-based)           |
| `captureMaxLength`       | `24000`    | Max sanitized text length for the capture decision                       |
| `captureAssistantTurns`  | `true`     | Include assistant turns (text + tool USE inputs)                         |
| `captureToolResults`     | `false`    | Include tool result output (noisy — off by default)                      |
| `captureToolMaxChars`    | `2000`     | Max captured output chars for one tool part                              |
| `commitTokenThreshold`   | `20000`    | Pending-token threshold for client-driven commit                         |
| `commitKeepRecentCount`  | `10`       | Live tail kept after commit                                              |

### Injection tuning

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `profileTokenBudget`     | `10000`    | Token budget for user profile block                                      |
| `resumeContextBudget`    | `32000`    | Token budget for archive overview on session resume                      |

### Misc

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `bypassPatterns`         | `[]`       | Glob patterns to skip extension processing                               |
| `logLevel`               | `"error"`  | `"silent"`, `"error"`, or `"info"`                                      |

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    Pi Coding Agent                    │
│                                                      │
│  session_start  before_agent_start  context  turn_end│
│  session_before_compact  session_shutdown            │
└────────┬──────────────────┬───────────┬──────────────┘
         │                  │           │
         │  ┌───────────────▼───────────▼────────┐
         │  │   extension modules (.ts)           │
         │  │   client / sync / recall / tools    │──────►  OpenViking
         │  └─────────────────────────────────────┘        Server
         │                                                (HTTP API)
         │  ┌──────────────────────────────────────┐
         └──►  7 registered LLM tools              │
            │  viking_search / viking_read / …     │
            └──────────────────────────────────────┘
```

The extension is a single directory of TypeScript files loaded by pi's `jiti` transpiler — no build step, no npm dependencies, no MCP server. All communication goes over HTTP to the OpenViking REST API.

### Event Flow

| Pi Event               | Extension Action                                                                 |
|------------------------|----------------------------------------------------------------------------------|
| `session_start`        | Health check → derive OV session → build profile/archive context → register tools |
| `before_agent_start`   | Fallback tool registration (for `pi -c` resume) + inject session context         |
| `context`              | Search OV with current prompt → inject `<openviking-context>` block              |
| `turn_end`             | Extract branch entries → write or pending-queue OV session messages              |
| `session_before_compact`| Commit pending messages before pi rewrites the transcript                        |
| `session_shutdown`     | Final commit so the last window is archived                                      |

### Recall: Synchronous, Not Stale

Unlike Hermes's stale prefetch (recall from previous turn's query, injected one turn late), this extension searches OpenViking with the **current** user prompt via pi's `context` event. Results are injected into the same turn as `<openviking-context>` blocks. This means:

- **First turn** of a session gets relevant context immediately
- **Topic switches** within a session get correct recall
- No waiting for the next turn to see relevant memories

### Memory Pollution Prevention

Before pushing turns to OpenViking, shared capture sanitization strips injected context blocks such as `<openviking-context>` to prevent a self-referential pollution loop where recall context is captured back as user messages.

### Tool Use Preservation

Tool capture preserves structured tool parts with bounded inputs and outputs. The memory extractor sees what the agent did without indexing unbounded raw output.

## LLM Tools

The extension registers 7 tools that pi's model can invoke on demand:

| Tool                     | Description                                                |
|--------------------------|------------------------------------------------------------|
| `viking_search`          | Semantic search across memories, resources, and skills     |
| `viking_read`            | Read a `viking://` URI at abstract / overview / full level |
| `viking_browse`          | List directory contents or stat a `viking://` URI          |
| `viking_remember`        | Store a fact or preference into long-term memory           |
| `viking_forget`          | Delete a memory by URI or search query                     |
| `viking_add_resource`    | Ingest a URL into OpenViking for indexed retrieval         |
| `viking_archive_expand`  | Expand an archived session back into raw conversation      |

The canonical `/viking` command (type `/viking` in pi's chat) displays connection status, session info, and accepts `commit` for manual synchronous commit.

## Compared to Pi's Built-in Memory

Pi has a built-in `MEMORY.md` file system. This extension **complements** it:

| Feature      | Built-in `MEMORY.md`              | OpenViking extension                              |
|--------------|-----------------------------------|---------------------------------------------------|
| Storage      | Flat markdown                     | Vector DB + structured extraction                 |
| Search       | Loaded into context wholesale     | Semantic similarity + ranking + token budget      |
| Scope        | Per-project                       | Cross-project, cross-session, cross-agent         |
| Capacity     | Context-limited                    | Unlimited (server-side storage)                   |
| Extraction   | Manual rules                      | LLM-powered entity / preference / event extraction|
| Subagents    | Same as parent                    | Isolated session + typed agent namespace          |

## Compared to Claude Code Plugin

Both plugins share the same core design (informed by each other):

| Feature             | Claude Code Plugin                     | Pi Extension                           |
|---------------------|----------------------------------------|----------------------------------------|
| Architecture        | Hook scripts (.mjs) + MCP delegation   | Native TypeScript extension            |
| Recall timing       | Synchronous (UserPromptSubmit hook)     | Synchronous (context event)            |
| Tool delivery       | OV server's MCP endpoint (9 tools)      | pi.registerTool() (7 tools)            |
| Write path          | Detached worker (async)                 | Async promise (pi's event loop)        |
| Installation        | `claude plugin install` + setup script  | Copy directory → auto-discovered       |
| Memory index        | None (flashlight search model)          | Built (map model — model sees what OV knows) |
| Subagent isolation  | Explicit hook management                | Natural process-level isolation        |

## Extension Structure

See [DESIGN.md](./DESIGN.md) for the full design specification — comparison of all three OV plugins, detailed event flow, design rationale, and implementation guidance useful for building OV extensions for any agent harness.

```
pi-coding-agent-extension/
├── config.json          # Default configuration (edit to customize)
├── config.ts            # Config loader (defaults + config.json merge)
├── client.ts            # OpenViking HTTP client (fetch + response envelope)
├── sync.ts              # Turn capture, write queue, session lifecycle
├── recall.ts            # Synchronous recall with ranking + budget
├── tools.ts             # 7 registered LLM tools + /viking command
├── index-builder.ts     # Memory index builder (knowledge map)
├── index.ts             # Extension entry point (event handlers)
└── README.md
```

All TypeScript files are loaded directly by pi's built-in `jiti` transpiler — zero dependencies beyond Node.js.

## Troubleshooting

| Symptom                                 | Cause                                                | Fix                                                         |
|-----------------------------------------|------------------------------------------------------|-------------------------------------------------------------|
| Extension not loading                   | `enabled: false` in config.json                      | Set `"enabled": true`                                       |
| No recall on first prompt               | OpenViking server not running or wrong URL           | `curl http://localhost:1933/health`                         |
| Tools not showing after `pi -c` resume  | Known pi issue (tools not re-registered on resume)   | Workaround built in — tools register in `before_agent_start`|
| Extension crashes on load               | Wrong OV server URL or network issue                 | Check `logLevel` and server accessibility                   |
| No memories extracted                   | Wrong embedding/extraction model in OV config        | Check OV's `embedding` / `vlm` configuration                |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
