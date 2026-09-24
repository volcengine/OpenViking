# OpenViking Memory Extension for Pi Coding Agent

Long-term semantic memory and context takeover for [pi](https://github.com/earendil-works/pi) sessions, powered by [OpenViking](https://github.com/volcengine/OpenViking). Recall happens automatically before every prompt, capture happens after every turn, and OpenViking can own long-term context by replacing committed history with an archive overview in pi's `context` hook.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the
> caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less
> `viking://user/memories` shorthand is rejected by newer servers.

> Design informed by lessons from all three OpenViking agent plugins: synchronous recall from OpenClaw, production-hardened capture/ranking from Claude Code, and anti-patterns dodged from Hermes's stale prefetch approach. See [DESIGN.md](./DESIGN.md) for the module-by-module design, including the context-takeover layer.

## Quick Start

### Prerequisites

- **pi coding agent** installed (`npm i -g @earendil-works/pi-coding-agent`)
- **Node.js 22.19.0+** and **npm**
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

The installer installs the locked npm dependencies in a temporary directory before replacing the extension at `~/.pi/agent/extensions/openviking`, which is one of pi's auto-discovery roots, so pi loads it on the next `pi` invocation — no `packages` entry is needed. (Registering the same path with `pi install` would load it twice, so the installer avoids that and clears any stale entry left by older versions.)

For a manual copy, generate the shared runtime first, copy the extension, and install its dependencies:

```bash
node examples/memory-plugin-shared/sync.mjs
mkdir -p ~/.pi/agent/extensions/openviking
cp -R examples/pi-coding-agent-extension/. ~/.pi/agent/extensions/openviking/
npm ci --prefix ~/.pi/agent/extensions/openviking --omit=dev --ignore-scripts
```

### 3. Configure (optional)

Credentials are resolved from `OPENVIKING_*` environment variables, `~/.openviking/ovcli.conf`, then `~/.openviking/ov.conf`. Run the setup wizard when you need to configure a remote server:

```bash
node ~/.pi/agent/extensions/openviking/scripts/setup.mjs
```

Behaviour and peer-scoping knobs live in `~/.openviking/ovcli.conf` beside the credentials, in the shared `plugin` section or in the `plugin.pi` override. The extension has no configuration file of its own:

```json
{
  "url": "http://127.0.0.1:1933",
  "api_key": "your-api-key-here",
  "plugin": {
    "recallLimit": 6,
    "pi": {
      "enabled": true,
      "autoCapture": true,
      "peerId": "",
      "workspacePeer": true,
      "recallPeerScope": "all",
      "recallTokenBudget": 2000,
      "scoreThreshold": 0.35,
      "minQueryLength": 3,
      "profileTokenBudget": 10000,
      "skillCatalog": true,
      "skillCatalogTokenBudget": 1200,
      "resumeContextBudget": 32000,
      "commitTokenThreshold": 20000,
      "takeoverEnabled": true,
      "takeoverTokenThreshold": 30000,
      "takeoverKeepRecentTurns": 3,
      "takeoverOverviewBudget": 3000,
      "takeoverOverviewPollMs": 2000,
      "takeoverOverviewPollMax": 15
    }
  }
}
```

Keys in `plugin` apply to every harness; keys in `plugin.pi` apply to this extension and override them. Resolution is `OPENVIKING_*` environment variables → the workspace's `.openviking/config.json`, `.openviking/config.local.json` and machine registry entry → `plugin.pi` → `plugin` → built-in defaults. Every knob, with its type, default, range, environment variable and accepted older spellings, is declared in [`examples/memory-plugin-shared/lib/config-schema.mjs`](../memory-plugin-shared/lib/config-schema.mjs); `syncTurns` still works wherever `autoCapture` is written above.

Credential environment variables:

| Env Var | Meaning |
|---------|---------|
| `OPENVIKING_URL` | OpenViking server URL |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | Bearer token |
| `OPENVIKING_ACCOUNT` | Trusted-mode account |
| `OPENVIKING_USER` | Trusted-mode user |
| `OPENVIKING_PEER_ID` | Actor peer id |
| `OPENVIKING_WORKSPACE_PEER` | Derive an actor peer from the workspace's git identity by default; set `0` to send no peer |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` recalls other project memories with a score penalty; `actor` only sees global plus the current project |
| `OPENVIKING_DEBUG_LOG` | Write JSON Lines debug records to this path. `OV_DEBUG_LOG` is a deprecated alias kept for existing setups |

Recall asks the server to assemble the context block in one request
(`POST /api/v1/search/search` with `mode="context"`), so token budgeting, detail
tiers and cross-turn dedup are shared with every other harness. Deployments
without that endpoint fall back to `/api/v1/search/recall`, and that outcome is
cached so only the first turn pays for the probe.

API keys are sent as `Authorization: Bearer ...`. By default the extension derives a peer from the git identity of the process workspace path: the normalized `origin` URL of the repository the workspace sits in, else that repository's root path. Outside a git repository no peer is sent at all, and what is remembered there goes to the user-level space `viking://user/<you>/memories`. `git@github.com:volcengine/OpenViking.git` becomes `github.com-volcengine-openviking`; the path fallback keeps the older naming rule where every non-letter-or-digit character becomes `-`, so `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking`. One repository therefore keeps one peer across subdirectories, worktrees, clones and machines, while a fork's different origin keeps it separate. The extension reads the workspace's `.openviking/config.json`, so a `peer.id` or `peer.source` written there applies. The effective peer is sent as `X-OpenViking-Actor-Peer` and stored as `peer_id` on captured session messages. `OPENVIKING_PEER_ID` takes precedence over every file; below it, a workspace `peer.id` or the `plugin` section's `peerId` takes precedence over `ovcli.conf`'s `actor_peer_id` and `ov.conf`'s `pi.peerId`; and a peer from any of those takes precedence over workspace derivation. Memories written under the older path-derived peer stay reachable under the default broad recall, which sweeps every peer under the user.

Recall defaults to the broad mode: global memory, the current workspace, and other workspace memories can all be recalled, with other workspaces penalized and rendered later. Set `OPENVIKING_RECALL_PEER_SCOPE=actor` for the isolation mode, which only sees global memory plus the current workspace. In deployments where one bot serves multiple real people, such as zouk, vikingbot, or AstrBot, use the isolation mode with an explicit actor peer so one person's memories are not recalled into another person's session.

### 4. Start Pi

```bash
pi
```

The extension shows an `[OpenViking]` status line on startup. The server's own MCP tools (`openviking_search`, `openviking_remember`, and the rest of whatever it exposes) are registered automatically. Memories persist across sessions — no additional setup.

## Configuration Reference

### Tuning fields

All fields below live in `ovcli.conf`'s `plugin` section, or in the `plugin.pi` override. Defaults are shown.

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `enabled`                | `true`     | Set `false` to disable the extension entirely                            |
| `autoCapture`            | `true`     | Enable auto-capture of conversation turns. `syncTurns` is the older name and still works |

### Peer scoping

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `peerId`                 | `""`      | Local explicit peer fallback; shared credential sources take precedence  |
| `workspacePeer`          | `true`     | Derive a peer from the workspace's git identity when no explicit peer is set; `false` sends no peer |
| `recallPeerScope`        | `"all"`   | Use `"actor"` for strict current-peer recall or `"all"` for broad recall |

### Recall tuning

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `recallTokenBudget`      | `2000`     | Token budget for inline recall content                                   |
| `recallMaxContentChars`  | `500`      | Per-item content cap for search results                                  |
| `recallPreferAbstract`   | `true`     | Prefer L0 abstract over L2 full body when available                      |
| `recallLimit`            | `10`       | Legacy quota-scaling input converted to six coding quotas, not a final cap |
| `scoreThreshold`         | `0.35`     | Min relevance score (0–1)                                                |
| `minQueryLength`         | `3`        | Skip recall for queries shorter than N characters                        |
| `recallLedger`           | `true`     | Persist injected blocks and re-apply them to historical user messages so provider prompt-prefix caches keep hitting |

### Recall injection ledger

Pi's `context` hook hands extensions a deep copy of the session messages, so
an injected `<openviking-context>` block is never written back to session
storage. Without compensation, every request's history diverges from what the
provider saw last turn, and strict-prefix prompt caches (DeepSeek and other
OpenAI-compatible providers) miss from the first injected message onward
(#4137). The ledger records exactly which block was injected into which user
message (keyed by stable Pi entry id + content hash) in
`~/.openviking/pi-recall-ledger/<session>.json` and re-applies them on every
request, keeping the prefix byte-identical across turns while the newest
message still gets fresh, current-query recall. Disable with
`"recallLedger": false` or `OPENVIKING_RECALL_LEDGER=0`. Losing the ledger
file only costs one cache miss; alignment resumes on the next turn. Stable
entry ids let compacted retained messages and `/tree` branches recover their
own original blocks even when active-context positions change.

Explicit `recallLimit` values from 1 through 5 produce an effective total
quota of 6 because each coding category keeps one retrieval slot. Direct API
integrations should configure category `quotas` when they need exact ceilings.

### Capture tuning

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `captureMode`            | `"semantic"` | `"semantic"` (always capture) or `"keyword"` (trigger-based)           |
| `captureMaxLength`       | `24000`    | Max sanitized text length for the capture decision                       |
| `captureAssistantTurns`  | `true`     | Include assistant turns (text + tool USE inputs)                         |
| `captureToolResults`     | `false`    | Declared in the shared schema, but this extension never reads it: `lib/capture-adapter.mjs` keeps every structured tool part, so tool results are captured either way, bounded by `captureToolMaxChars` |
| `captureToolMaxChars`    | `1000000`  | Guard cap on one tool part's `tool_output`; the server externalizes oversized output |
| `commitTokenThreshold`   | `20000`    | Pending-token threshold for client-driven commit                         |
| `commitKeepRecentCount`  | `10`       | Live tail kept after commit                                              |

### Context takeover

Takeover is enabled by default. OpenViking commits archived history, reads the
overview of that exact archive, then the `context` hook replaces covered
conversation turns with a synthetic `[OpenViking Session Context]` user message
while keeping the recent live tail. The boundary advances only after every
captured message in the cut has reached the server and the new archive has a
non-empty overview. Permanent capture gaps leave the local history with Pi.

Takeover never waits for the summary inside a turn: pi hosts cap each extension
event handler at 30s, so a summary that is not ready yet is checked once per
later turn and before the next prompt. An archive the server marks finished
or failed without a summary (for example with Working Memory disabled) is
dropped instead of being checked forever.

When `openviking_list` and `openviking_read` are active, the injected overview
also includes the archive URI and a short recovery instruction. `openviking_list`
locates the archive and `openviking_read` reads `messages.jsonl` in `offset` /
`limit` chunks. This file contains captured historical messages; semantic search
does not return the archive source verbatim, and capture may have sanitized,
filtered or truncated the original Pi transcript.

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `takeoverEnabled`        | `true`     | Let OpenViking own long-term context through the `context` hook. Env: `OPENVIKING_TAKEOVER` |
| `takeoverTokenThreshold` | `30000`    | Synced-token pressure that triggers commit and boundary advance           |
| `takeoverKeepRecentTurns`| `3`        | Recent user turns retained in full fidelity                              |
| `takeoverOverviewBudget` | `3000`     | Token budget for the injected archive overview                           |
| `takeoverOverviewPollMs` | `2000`     | Delay between overview reads while answering pi's compaction             |
| `takeoverOverviewPollMax`| `15`       | Max overview reads for pi's compaction; capped by the 25s handler budget |

### Injection tuning

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `profileTokenBudget`     | `10000`    | Token budget for user profile block                                      |
| `skillCatalog`           | `true`     | Add the `<available-skills>` catalog to the profile block. Env: `OPENVIKING_SKILL_CATALOG` |
| `skillCatalogTokenBudget`| `1200`     | Separate token budget for the skill catalog (0–20000; `0` turns it off). Env: `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET` |
| `resumeContextBudget`    | `32000`    | Token budget for archive overview on session resume                      |

The profile block is built at session start and added to pi's system prompt on every turn. Its `<available-skills>` catalog comes from one `GET /api/v1/skills`: your own skills first, then the ones shared under `viking://agent/skills`, leaving out a shared skill that has the same name as one of yours, with each description cut to about 40 tokens. When the descriptions do not fit `skillCatalogTokenBudget`, the catalog lists names only (with a `... +N more` tail if even the names do not all fit), and when not even one name fits, a one-line count. With no skills, or on a server without that endpoint, it is left out. The model reads a skill's `SKILL.md` with `viking_read` before following it.

### Misc

| Field                    | Default    | Description                                                              |
|--------------------------|------------|--------------------------------------------------------------------------|
| `bypassSessionPatterns`  | `[]`       | Glob patterns matched against the cwd; a hit skips all OpenViking work for the session. `bypassPatterns` is the older name and still works. Env: `OPENVIKING_BYPASS_SESSION_PATTERNS` (CSV), `OPENVIKING_BYPASS_SESSION=1` |
| `logLevel`               | `"error"`  | `"silent"`, `"error"`, or `"info"`                                      |
| `debugLogPath`           | `""`       | Write JSON Lines debug records to this path; empty disables the log      |

Bypass now runs through the same matcher every other OpenViking harness uses, so the patterns are real globs: `*` stops at a path separator and `**` crosses them. A bare path used to match its subdirectories as a prefix, and no longer does — write `"/tmp/scratch**"` where `"/tmp/scratch"` used to be enough.

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
         │  │   extension modules (.ts)           │        OpenViking
         │  │   client / sync / recall / takeover │──────►  REST API
         │  └─────────────────────────────────────┘        (recall, session
         │                                                  sync, takeover,
         │                                                  health)
         │  ┌──────────────────────────────────────┐
         └──►  tools.ts + lib/mcp-bridge.mjs       │──────►  OpenViking
            │  the server's own tools/list,        │         /mcp endpoint
            │  prefixed openviking_                │         (model tools)
            └──────────────────────────────────────┘
```

The extension's TypeScript files are loaded by pi's `jiti` transpiler without a build step. Recall, session sync, context takeover and the startup health check use the REST API. Model-facing tools use the official `@modelcontextprotocol/client` SDK over Streamable HTTP and mirror the server's `/mcp` catalogue. Configuration and identity headers reuse the shared library.

MCP initialization and tool discovery share a 5-second deadline. Each tool call uses `timeoutMs` (15 seconds by default), including any reconnection. Credentials and connection settings are reloaded before calls; a transport failure is returned immediately and the next call reconnects. Calls are never automatically replayed. Cancellation ends the client's wait and cannot undo a write already accepted by the server.

Arguments are checked against the server's original schema before pi's own validation. Invalid nulls, scalar-to-array substitutions and stringified JSON are rejected rather than repaired. Text results share a 50 KiB / 2000-line limit across all content blocks.

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

The extension keeps no tool catalogue of its own. At session start it asks the server's `/mcp` endpoint for `tools/list` and registers one pi tool per descriptor it gets back, named `openviking_` + the server's own name, with the server's description and argument schema. A server that gains, drops or re-documents a tool changes pi's tool surface at the next session, with no extension release.

Against a current server, that is these 15:

| Tool                        | Description                                                             |
|-----------------------------|-------------------------------------------------------------------------|
| `openviking_find`           | Fast ranked retrieval across memories, resources and skills             |
| `openviking_search`         | Deep retrieval with session context and intent analysis; `mode="context"` returns an assembled block |
| `openviking_read`           | Read one or more `viking://` file URIs, with line-based `offset`/`limit` |
| `openviking_list`           | List one sorted page under a `viking://` directory                      |
| `openviking_tree`           | Show a recursive directory tree, optionally with abstracts              |
| `openviking_remember`       | Store messages as long-term memory and commit them for extraction       |
| `openviking_write`          | Write text to a `viking://` file                                        |
| `openviking_edit`           | Replace an exact string in an existing `viking://` file                 |
| `openviking_add_resource`   | Ingest a URL, repository or local file as a resource                    |
| `openviking_list_watches`   | List the auto-refresh watch tasks visible to this user                  |
| `openviking_cancel_watch`   | Cancel a watch task by its target URI                                   |
| `openviking_grep`           | Regex search inside `viking://` files                                   |
| `openviking_glob`           | Find `viking://` files matching a glob pattern                          |
| `openviking_forget`         | Permanently delete a `viking://` URI                                    |
| `openviking_health`         | Check that the OpenViking server is healthy                             |

That table is a snapshot of one server, not a contract; `/viking` reports how many tools registered against your own.

The canonical `/viking` command (type `/viking` in pi's chat) displays connection status, session info, how many tools registered — or the handshake error when none did — and accepts `commit` for manual synchronous commit.

## Upgrading from 0.3.x

0.4.2 makes context takeover conservative: it keeps Pi's system/tool state,
anchors the trim boundary on a Pi entry id, waits for confirmed capture
delivery, reads the overview belonging to the exact new archive without
blocking a turn on it, and exposes a paginated archive-recovery hint when the
required MCP tools are active. Sessions with a permanent capture gap stay on
Pi's native compaction. A boundary saved by 0.4.1 or earlier is converted on
the first request.

0.4.0 replaces the seven hand-written `viking_*` REST tools with the server's own MCP tool surface. There is no alias period: the old names are gone.

- **Tool names.** Every tool is now `openviking_` + the server's own name: `viking_search` → `openviking_search`, `viking_read` → `openviking_read`, `viking_remember` → `openviking_remember`, `viking_forget` → `openviking_forget`, `viking_add_resource` → `openviking_add_resource`. `viking_browse` is covered by `openviking_list`, `openviking_tree` and `openviking_glob`; its stat mode and `viking_archive_expand` have no successor.
- **`--tools` / `--exclude-tools` allowlists.** pi matches those by exact name, so an allowlist that still says `viking_search` drops the tool silently instead of erroring. Rename the entries by hand.
- **`remember` is its own session.** It used to append a message to the live pi session and wait for that session's commit. The MCP tool opens a dedicated `mcp-store-*` session, writes the messages and commits it immediately, so what you asked to remember goes into extraction on the spot.
- **`read` returns full text only.** The `level="abstract"` and `level="overview"` tiers are gone, and MCP `read` cannot read a directory's overview — `.overview.md` does not appear in `list` either. Use `openviking_search(mode="context", detail="overview")` or `openviking_tree(include_abstract=true)` instead. Context takeover is unaffected: the extension still fetches the archive overview itself over REST.
- **`forget` is URI-only.** It deletes the URI it is handed. The old deletion-by-query path, with its score > 0.8 guard and its `recursive=false` default, went with the tool that had it.
- **`add_resource` and local files.** For a filesystem path the server answers with a one-time upload URL, and the model has to POST the file there with `bash`. Remote URLs still ingest directly. This is how every other MCP harness behaves.
- **A ROOT api key now leaves the session with no tools.** Root keys are refused on `/mcp` with 403. Before, the REST tools registered and every call came back empty; now nothing registers, the status line shows `tools ✗`, and `/viking` names the 403. Create a user or admin key and put that in `ovcli.conf`.
- **`mcpEnabled: false` now applies to pi.** The shared key in `ovcli.conf`'s `plugin` section already turned the MCP surface off for other harnesses. Set for pi it means no handshake and no tools — and that is not reported as a failure; recall, capture and takeover carry on.
- **Already running pi-mcp-adapter?** Do not also point it at OpenViking. The same tools would then appear twice in one session under two different names.

A failed handshake never fails startup: recall, session sync and takeover keep working, the session simply has no OpenViking tools. The bridge retries on a later turn, so a server started after pi is picked up without restarting pi.

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
| Tool delivery       | OV server's MCP endpoint (count follows the server) | Official MCP client, published through pi.registerTool() (same count) |
| Write path          | Detached worker (async)                 | Async promise (pi's event loop)        |
| Installation        | `claude plugin install` + setup script  | Copy directory → auto-discovered       |
| Memory index        | Shared profile block (memories + skills), injected at session start | Same block, folded into the system prompt every turn |
| Subagent isolation  | Explicit hook management                | Natural process-level isolation        |

## Extension Structure

See [DESIGN.md](./DESIGN.md) for what each module is responsible for, how the modules meet pi's events, and the design ancestry shared with the other OpenViking plugins.

```
pi-coding-agent-extension/
├── config.ts            # Config loader (shared schema + ovcli.conf layers)
├── client.ts            # OpenViking HTTP client (fetch + response envelope)
├── sync.ts              # Turn capture, write queue, session lifecycle
├── recall.ts            # Synchronous recall with ranking + budget
├── takeover.ts          # Thin pi binding around lib/takeover-core.mjs
├── tools.ts             # Publishes the server's MCP tools as pi tools
├── lib/mcp-bridge.mjs   # Official SDK connection lifecycle
├── lib/mcp-result.mjs   # pi content conversion and output limit
├── lib/takeover-core.mjs # Pure context-takeover state machine
├── lib/recall-ledger.mjs # Injected recall blocks, replayed to keep prompt caches warm
├── index.ts             # Extension entry point (event handlers + /viking command)
├── package.json         # Name and version (the User-Agent's, and the release gate's)
├── DESIGN.md            # Module-by-module design, including context takeover
└── README.md
```

TypeScript is loaded directly by pi's jiti transpiler. The official MCP client is installed from the committed npm lockfile; node_modules is not bundled in marketplace archives.

## Troubleshooting

| Symptom                                 | Cause                                                | Fix                                                         |
|-----------------------------------------|------------------------------------------------------|-------------------------------------------------------------|
| Extension not loading                   | `enabled: false` in `ovcli.conf`'s `plugin` section  | Set `"enabled": true`                                       |
| No recall on first prompt               | OpenViking server not running or wrong URL           | `curl http://localhost:1933/health`                         |
| Tools not showing after `pi -c` resume  | Known pi issue (tools not re-registered on resume)   | Workaround built in — tools register in `before_agent_start`|
| Extension crashes on load               | Wrong OV server URL or network issue                 | Check `logLevel` and server accessibility                   |
| No memories extracted                   | Wrong embedding/extraction model in OV config        | Check OV's `embedding` / `vlm` configuration                |
| Takeover never advances                  | Pending addMessage replay or commit failed, or the archive summary never arrives (Working Memory disabled on the server) | Set `OPENVIKING_DEBUG_LOG=/tmp/ov-pi.log` and retry `/viking commit` |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
