# OpenViking Memory Extension for Pi Coding Agent

Long-term semantic memory and context takeover for [pi](https://github.com/earendil-works/pi) sessions, powered by [OpenViking](https://github.com/volcengine/OpenViking). Recall happens automatically before every prompt, capture happens after every turn, and OpenViking can own long-term context by replacing committed history with an archive overview in pi's `context` hook.

> Design informed by lessons from all three OpenViking agent plugins: synchronous recall from OpenClaw, production-hardened capture/ranking from Claude Code, and anti-patterns dodged from Hermes's stale prefetch approach. See [DESIGN.md](./DESIGN.md) for the base design and [TAKEOVER.md](./TAKEOVER.md) for the context-takeover layer.

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
  "commitTokenThreshold": 20000,
  "takeover": {
    "enabled": true,
    "tokenThreshold": 30000,
    "keepRecentTurns": 3,
    "overviewBudget": 3000,
    "overviewPollMs": 2000,
    "overviewPollMax": 15
  }
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
| `OPENVIKING_WORKSPACE_PEER` | Derive an actor peer from the current workspace by default; set `0` to disable |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` recalls other project memories with a score penalty; `actor` only sees global plus the current project |

API keys are sent as `Authorization: Bearer ...`. By default the extension derives a peer from the process workspace path using Claude's project-directory naming rule: every non-letter-or-digit character becomes `-`, with no path normalization. For example, `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking`. The effective peer is sent as `X-OpenViking-Actor-Peer` and stored as `peer_id` on captured session messages. `OPENVIKING_PEER_ID` overrides the workspace-derived value.

Recall defaults to the broad mode: global memory, the current workspace, and other workspace memories can all be recalled, with other workspaces penalized and rendered later. Set `OPENVIKING_RECALL_PEER_SCOPE=actor` for the isolation mode, which only sees global memory plus the current workspace. In deployments where one bot serves multiple real people, such as zouk, vikingbot, or AstrBot, use the isolation mode with an explicit actor peer so one person's memories are not recalled into another person's session.

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

### Context takeover

Takeover is enabled by default. OpenViking commits archived history, polls the
session overview, then the `context` hook replaces covered conversation turns
with a synthetic `[OpenViking Session Context]` user message while keeping the
recent live tail.

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `takeover.enabled`       | `true`     | Let OpenViking own long-term context through the `context` hook           |
| `takeover.tokenThreshold`| `30000`    | Synced-token pressure that triggers commit and boundary advance           |
| `takeover.keepRecentTurns`| `3`       | Recent user turns retained in full fidelity                              |
| `takeover.overviewBudget`| `3000`    | Token budget for the injected archive overview                           |
| `takeover.overviewPollMs`| `2000`    | Delay between overview polling attempts after commit                     |
| `takeover.overviewPollMax`| `15`     | Max overview polling attempts before fail-open                           |

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
| `session_start`        | Health check → derive OV session → build profile context → restore takeover state |
| `before_agent_start`   | Idempotent startup for `pi -c` + queue the current prompt for recall              |
| `context`              | Run current-prompt recall after UI rendering, then inject takeover and recall context |
| `turn_end`             | Extract branch entries → write or pending-queue OV messages → maybe advance boundary |
| `session_before_compact`| Takeover mode returns OV overview as pi compaction summary; otherwise commits pending messages |
| `session_shutdown`     | Persist takeover state or final non-takeover commit                              |

### Recall: Synchronous, Not Stale

Unlike Hermes's stale prefetch (recall from previous turn's query, injected one turn late), this extension searches OpenViking with the **current** user prompt via pi's `context` event. Pi renders the submitted user message before this hook, so recall latency does not hold the message off-screen. Results are still injected into the same model turn as `<openviking-context>` blocks. This means:

- **First turn** of a session gets relevant context immediately
- **Topic switches** within a session get correct recall
- No waiting for the next turn to see relevant memories

### Memory Pollution Prevention

Before pushing turns to OpenViking, shared capture sanitization strips injected context blocks such as `<openviking-context>` to prevent a self-referential pollution loop where recall context is captured back as user messages.

In takeover mode the adapter uses faithful capture: acknowledgments and short
turns are retained because they may later be represented only through the OV
archive overview. Empty text, slash commands, and OpenViking status messages
remain filtered.

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
| Tool delivery       | OV server's MCP endpoint (16 tools)     | pi.registerTool() (7 tools)            |
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
├── takeover.ts          # Thin pi binding around lib/takeover-core.mjs
├── tools.ts             # 7 registered LLM tools + /viking command
├── lib/takeover-core.mjs # Pure context-takeover state machine
├── index.ts             # Extension entry point (event handlers)
├── TAKEOVER.md          # Context-takeover design
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
| Takeover never advances                  | Pending addMessage replay, commit, or overview polling failed | Set `OV_DEBUG_LOG=/tmp/ov-pi.log` and retry `/viking commit` |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
