# OpenViking Memory Plugin for Codex

Long-term semantic memory for [Codex](https://developers.openai.com/codex), powered by [OpenViking](https://github.com/volcengine/OpenViking).

This is the Codex counterpart to [`claude-code-memory-plugin`](../claude-code-memory-plugin). It hooks Codex's lifecycle to:

- **Auto-recall** relevant memories on every `UserPromptSubmit` and inject them via `hookSpecificOutput.additionalContext`
- **Incremental capture on `Stop`** (turn end): append the new user/assistant turns to a single long-lived OpenViking session keyed by Codex `session_id`. No commit per turn.
- **Commit on `PreCompact`**: trigger OpenViking's memory extractor on the full pre-compact transcript before Codex summarizes it.
- **Commit on `SessionStart` (source=startup|clear)**: active-window heuristic — if exactly one *other* state file was touched within the last 2 min, commit it (the just-ended session). On `≥2`, defer to idle-TTL sweep at the tail. `source=resume` is a hard no-op (short reconnects re-fire `resume` and we don't want to commit a still-active session). See `DESIGN.md` for the full decision tree.
- **MCP runtime bootstrap is lazy**: the MCP launcher (`start-memory-server.mjs`) installs runtime deps on first MCP invocation, not in a hook.

It also exposes explicit MCP tools (`openviking_recall`, `openviking_store`, `openviking_forget`, `openviking_health`) for manual use.

## Quick Start

Installation is first here, matching the shape of the [Claude Code integration doc](../../docs/en/agent-integrations/02-claude-code.md).

### One-line installer (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer checks `codex`, `git`, and Node.js 22+, clones OpenViking to `~/.openviking/openviking-repo` if needed, registers a local `openviking-plugins-local` marketplace, enables `openviking-memory@openviking-plugins-local`, sets `features.plugin_hooks = true`, and pre-populates Codex's plugin cache so the plugin resolves immediately. It uses `~/.openviking/ovcli.conf` when present; otherwise the plugin falls back to `http://127.0.0.1:1933`.

If you'd rather do it by hand, use the manual setup below.

### Manual setup

#### 1. Install prerequisites

```bash
node --version    # >= 22
codex --version   # >= 0.124.0
```

Make sure `codex_hooks` is enabled:

```bash
codex features list | grep codex_hooks
```

Plugin lifecycle hooks also require `plugin_hooks`:

```toml
[features]
plugin_hooks = true
```

#### 2. Install the plugin

The plugin lives at `examples/codex-memory-plugin/`.

```bash
mkdir -p /tmp/ov-codex-mp/.claude-plugin
ln -s /abs/path/to/OpenViking/examples/codex-memory-plugin /tmp/ov-codex-mp/openviking-memory
cat > /tmp/ov-codex-mp/.claude-plugin/marketplace.json <<'EOF'
{
  "name": "openviking-plugins-local",
  "plugins": [
    { "name": "openviking-memory", "source": "./openviking-memory" }
  ]
}
EOF

codex plugin marketplace add /tmp/ov-codex-mp
cat >> ~/.codex/config.toml <<'EOF'

[plugins."openviking-memory@openviking-plugins-local"]
enabled = true
EOF
```

For local development, pre-populate Codex's plugin cache so it resolves immediately:

```bash
INSTALL_DIR=~/.codex/plugins/cache/openviking-plugins-local/openviking-memory
mkdir -p "$INSTALL_DIR"
cp -R /abs/path/to/OpenViking/examples/codex-memory-plugin "$INSTALL_DIR/0.4.0"
```

#### 3. Configure OpenViking

Use the same client config file as the `ov` CLI:

```jsonc
// ~/.openviking/ovcli.conf
{
  "url": "https://ov.example.com",
  "api_key": "<your-key>",
  "account": "default",
  "user": "<your-user>"
}
```

Local server mode works without this file; the plugin falls back to `http://127.0.0.1:1933`.

#### 4. Start Codex

```bash
codex
```

First MCP launch installs runtime deps; later launches reuse them.

### Development from source

Only needed when editing `src/memory-server.ts`:

```bash
cd examples/codex-memory-plugin
npm install
npm run build
```

`codex exec` does not reliably fire plugin lifecycle hooks in current Codex builds. For hook validation, use an interactive `codex` session or the scripts in `hooks/hooks.json` with synthetic JSON input.

## Configuration

Resolution priority, highest to lowest:

1. Environment variables: `OPENVIKING_URL`, `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_AGENT_ID`
2. `ovcli.conf`: `~/.openviking/ovcli.conf` or `OPENVIKING_CLI_CONFIG_FILE`
3. `ov.conf`: `~/.openviking/ov.conf` or `OPENVIKING_CONFIG_FILE`
4. Built-in defaults

Auth is sent as `Authorization: Bearer <key>` plus legacy `X-API-Key` during migration.

Optional Codex-specific tuning can live under `codex` in `ovcli.conf`:

```jsonc
{
  "url": "https://ov.example.com",
  "api_key": "...",
  "codex": {
    "agentId": "codex",
    "recallLimit": 6,
    "captureAssistantTurns": false,
    "autoCommitOnCompact": true
  }
}
```

## Architecture

```
   ┌──────────────────────────────────────────────────────────────┐
   │                            Codex                             │
   └──┬─────────────────┬────────────────┬───────────────────┬────┘
      │                 │                │                   │
 SessionStart      UserPromptSubmit    Stop              PreCompact
 (startup|clear)        │              (per turn)            │
      │                 │                │                   │
 ┌────▼──────────┐ ┌────▼──────┐ ┌──────▼──────┐ ┌──────────▼──────┐
 │ session-start │ │ auto-     │ │ auto-       │ │ pre-compact-    │
 │ -commit.mjs   │ │ recall.mjs│ │ capture.mjs │ │ capture.mjs     │
 │ (active-win   │ │ (search)  │ │ (append +   │ │ (commit + reset │
 │ heuristic +   │ │           │ │ no commit)  │ │ ovSessionId)    │
 │ idle TTL)     │ │           │ │             │ │                 │
 └────┬──────────┘ └────┬──────┘ └──────┬──────┘ └──────────┬──────┘
      │                 │                │                   │
      │             ┌───▼────────────────▼───────────────────▼──┐
      └────────────►│           OpenViking server               │
                    │ /api/v1/search/find                       │
                    │ /api/v1/sessions [+/{id}/{messages,commit}]│
                    │ /api/v1/content/read                      │
                    └───────────────────────────────────────────┘

   ┌──────────────────────────────────────┐
   │  MCP Server (memory-server.ts)       │
   │  Tools for explicit use:             │
   │  • openviking_recall                 │
   │  • openviking_store                  │
   │  • openviking_forget                 │
   │  • openviking_health                 │
   │  Lazily npm ci's its runtime on      │
   │  first launch.                       │
   └──────────────────────────────────────┘
```

## How It Works

> See [`DESIGN.md`](./DESIGN.md) for the commit decision tree — it's the source of truth for *which* OpenViking session is sealed by *which* hook event.

### SessionStart commit logic (source=startup|clear, heuristic + idle TTL)

Codex fires `SessionStart` with one of three `source` values: `startup` (fresh process / `/new` / zouk daemon spawn-without-sessionId), `resume` (`/resume` or short reconnect), and `clear` (`/clear` — the previous transcript is orphaned and a new session_id is created). `resume` is the *only* source we treat as a hard no-op; on `startup` and `clear` we run the same active-window heuristic.

`hooks.json` registers `SessionStart` with `matcher: "clear|startup"` so codex's dispatcher invokes the script on both sources (the matcher is matched against the SessionStart `source` field — see [`codex-rs/hooks/src/events/session_start.rs`](https://github.com/openai/codex/blob/main/codex-rs/hooks/src/events/session_start.rs)). `session-start-commit.mjs` gates internally on `source ∈ {startup, clear}` as defense-in-depth.

On `startup` or `clear`, the script:

1. Counts state files (excluding the new session_id) whose `lastUpdatedAt` is within `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` (default 2 min) of "now":
   - **0 active** → no-op (no orphan to commit)
   - **1 active** → commit it (the just-ended session)
   - **≥2 active** → skip; rely on idle TTL (we can't tell which one ended)
2. **Idle-TTL sweep at the tail**: any state file (regardless of session_id) older than `OPENVIKING_CODEX_IDLE_TTL_MS` (default 30 min) gets committed and cleared. This catches `SIGTERM` / Ctrl+C / `/exit` exits and crashes that left state files orphaned. The sweep runs *only* at SessionStart — the Stop hook deliberately does not sweep, because state-write-on-every-turn already gives us the freshness signal.

On any /commit failure (OV unreachable, non-2xx, timeout) we **preserve state** (don't `clearState`) so the next sweep can retry. A transient OV outage shouldn't lose memory.

MCP runtime install does **not** live in this hook — it lazily runs from `scripts/start-memory-server.mjs` on first MCP launch.

### Auto-recall (every UserPromptSubmit)

`auto-recall.mjs` reads `prompt` from stdin, calls `/api/v1/search/find` for both `viking://user/memories` and `viking://agent/memories` (and `viking://agent/skills`), ranks results with query-aware scoring (leaf boost, preference boost, temporal boost, lexical overlap), reads full content for top-ranked leaves, and emits:

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<relevant-memories>...</relevant-memories>" } }
```

Codex injects `additionalContext` into the model turn, so memories arrive without an extra tool call.

### Stop (turn end → `add_message`, NOT `commit`)

Codex's `Stop` fires per turn, not at session end. So `auto-capture.mjs` keeps **one** long-lived OpenViking session per Codex `session_id` and incrementally appends every new user/assistant turn from the rollout JSONL via `/api/v1/sessions/{id}/messages`. Per-codex-session state lives at `~/.openviking/codex-plugin-state/<safe-session-id>.json` and tracks `{ ovSessionId, capturedTurnCount, lastUpdatedAt }`.

We do **not** call `/commit` per turn — committing extracts memories, and per-turn extraction would over-fragment the memory tree and waste OV's extractor.

### PreCompact (deterministic commit)

`PreCompact` fires before Codex summarizes. `pre-compact-capture.mjs` does:

1. **Catch-up**: append any transcript turns Stop hasn't captured yet (race-safe via `capturedTurnCount`).
2. **Commit** the long-lived OV session for this Codex `session_id` so OV's extractor runs against the full pre-compact transcript.
3. **Reset** state: clear `ovSessionId` so the next `Stop` opens a fresh OV session for the post-compact half. `capturedTurnCount` stays so we don't re-capture pre-compact turns.

### Known gap: SIGTERM / Ctrl+C / `/exit` are silent

Codex fires no hook on process exit. `/compact` (PreCompact) is the only fully-deterministic "context disappearing" signal. If you `/exit` (or Ctrl+C, or kill the process) without first running `/compact`, the OpenViking session for that codex session_id stays open with messages but never has memories extracted in that moment.

Two fallbacks recover the orphan:

1. **Idle-TTL sweep**: the next `SessionStart` (source=startup|clear) on the same machine commits any state file older than 30 min (`OPENVIKING_CODEX_IDLE_TTL_MS`). So as long as you start another codex session within ~30 min, the orphan is reclaimed.
2. **Active-window heuristic**: if you run `/new` or `/clear` shortly after the orphaned session was last touched, the heuristic catches it as the unique "recently-active" state and commits it deterministically.

The remaining limitation: if you never start another codex on this machine, no sweep runs and the OV session stays open server-side. If you care about preserving memory from a particular session before exiting, run `/compact` first or call `openviking_store` with the conclusions you want kept.

### MCP tools (explicit, on demand)

The MCP server provides tools for when Codex or the user needs explicit memory operations. See "Tools" below.

## Codex hook output schema

Codex's hook output schema differs from Claude Code's. Notably:

| Hook | Input field of interest | Output channel for context injection |
|------|------------------------|--------------------------------------|
| `SessionStart`   | `source` (`startup`/`resume`/`clear`), `session_id` | `hookSpecificOutput.additionalContext` |
| `UserPromptSubmit` | `prompt`                                    | `hookSpecificOutput.additionalContext` |
| `Stop`           | `last_assistant_message`, `transcript_path`, `session_id` | `systemMessage` (only) |
| `PreCompact`     | `trigger` (`manual`/`auto`), `transcript_path`, `session_id` | `systemMessage` (only) |

> Note: this plugin acts on `SessionStart` when `source=startup` or `source=clear` (matcher `clear|startup`). `source=resume` is a no-op because codex re-fires it on short reconnects.

Unlike Claude Code, **Codex does not support `decision: "approve"`**; only `decision: "block"`. A no-op is `{}` (which is what these scripts emit when there's nothing to add).

Source: [`codex-rs/hooks/schema/generated/`](https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated).

## Validation SOP

This is the canonical end-to-end validation for an OpenViking plugin. Run it after any plugin change.

```bash
export OPENVIKING_API_KEY=<your-key>
export OPENVIKING_URL=https://ov.example.com   # or your server
ACCT=default

# 1. Trigger something memorable in a Codex session, then close it.
#    e.g.: "I prefer pour-over coffee for memory testing — please remember."

# 2. Verify a session was created and committed.
ov --account "$ACCT" ls viking://session | head
#    Pick the most recently created session id (one we just made).

SID=<paste session id>

# 3. Confirm the session has messages + history archive.
ov --account "$ACCT" ls "viking://session/$SID"
ov --account "$ACCT" ls "viking://session/$SID/history"
#    Expect: messages.jsonl and a history/archive_NNN/ entry.

# 4. Read the messages back to confirm the captured payload.
ov --account "$ACCT" read "viking://session/$SID/messages.jsonl"

# 5. Wait ~1 minute (or `ov wait`) for OV's extraction pipeline.
ov --account "$ACCT" wait --timeout 120

# 6. Verify long-term memories landed under the user (and/or agent) folder.
ov --account "$ACCT" find "<your seed phrase>" -u viking://user/<user>/memories -n 5
#    Expect leaf memories under preferences/, events/, entities/, etc.
```

If step 6 returns no leaf memories, check:

- The capture hook actually ran — `tail -f ~/.openviking/logs/codex-hooks.log` (with `OPENVIKING_DEBUG=1` or `codex.debug=true` in `ovcli.conf`).
- The OV server's extraction queue isn't backed up — `ov --account "$ACCT" status`.
- The committed text passed `shouldCapture` thresholds (`length`, `commands`, `keyword` mode).

## Configuration

| Field (`codex` section) | Default | Description |
|-------------------------|---------|-------------|
| `agentId`               | `codex` | Agent identity for memory isolation |
| `timeoutMs`             | `15000` | HTTP request timeout for recall/general requests (ms) |
| `autoRecall`            | `true`  | Enable auto-recall on every user prompt |
| `recallLimit`           | `6`     | Max memories to inject per turn |
| `scoreThreshold`        | `0.01`  | Min relevance score (0–1) |
| `minQueryLength`        | `3`     | Skip recall for very short queries |
| `logRankingDetails`     | `false` | Per-candidate ranking logs (verbose) |
| `autoCapture`           | `true`  | Enable auto-capture on Stop |
| `captureMode`           | `semantic` | `semantic` (always capture) or `keyword` (trigger-based) |
| `captureMaxLength`      | `24000` | Max text length for capture |
| `captureTimeoutMs`      | `30000` | HTTP request timeout for capture/commit (ms) |
| `captureAssistantTurns` | `false` | Include assistant turns in transcript-incremental capture |
| `captureLastAssistantOnStop` | `true` | Capture `last_assistant_message` separately on every Stop |
| `autoCommitOnCompact`   | `true`  | Commit the full transcript on `PreCompact` |
| `debug`                 | `false` | Write structured debug logs |

Connection settings resolve in this strict priority — env vars always win:

1. **Environment variables** (`OPENVIKING_*`)
2. **`ovcli.conf`** — CLI client config (`url`, `api_key`, `account`, `user`, `agent_id`)
3. **`ov.conf`** — server config (`server.*` + optional `codex.*` tuning block)
4. **Built-in defaults**

Setting `OPENVIKING_URL` alone is enough to run in env-var-only mode (no config files needed) — useful for daemon-spawned agents.

File-path overrides (aligned with `ov` CLI and `claude-code-memory-plugin`):

- `OPENVIKING_CLI_CONFIG_FILE` — alternate `ovcli.conf` path (default `~/.openviking/ovcli.conf`)
- `OPENVIKING_CONFIG_FILE` — alternate `ov.conf` path (default `~/.openviking/ov.conf`). For backward compat, if this points at an ovcli-shaped file (top-level `url`/`api_key`, no `server` section), it is treated as the CLI config.

Connection / identity overrides:

- `OPENVIKING_URL` / `OPENVIKING_BASE_URL` — server URL
- `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` — API key (sent as `Authorization: Bearer` either way)
- `OPENVIKING_ACCOUNT` — account
- `OPENVIKING_USER` — user
- `OPENVIKING_AGENT_ID` — agent identity

State-file / SessionStart tuning:

- `OPENVIKING_CODEX_STATE_DIR`: state file directory (default `~/.openviking/codex-plugin-state`)
- `OPENVIKING_CODEX_ACTIVE_WINDOW_MS`: SessionStart active-window threshold in ms (default `120000` = 2 min)
- `OPENVIKING_CODEX_IDLE_TTL_MS`: SessionStart idle-TTL sweep threshold in ms (default `1800000` = 30 min)

### Auth header

Requests send both `Authorization: Bearer <api_key>` (primary — required by OpenViking Cloud) and `X-API-Key` (legacy — accepted by older self-hosted servers). The legacy header will be dropped once `X-API-Key` is fully retired upstream.

## Hook timeouts

| Hook | Default timeout | Notes |
|------|-----------------|-------|
| `SessionStart`     | `120s` | First session may need time to install runtime deps |
| `UserPromptSubmit` | `8s`   | Recall must stay fast — keep `timeoutMs` low |
| `Stop`             | `45s`  | Gives capture room to finish |
| `PreCompact`       | `60s`  | Whole transcript posts plus commit |

## Debug logging

Set `OPENVIKING_DEBUG=1` or `codex.debug=true` in `ovcli.conf` to write structured JSON-Lines events to `~/.openviking/logs/codex-hooks.log`. Each entry is `{ts, hook, stage, data}` (or `error`).

## MCP Tools

### `openviking_recall`

Search OpenViking memory.

Parameters:

- `query`: search query
- `target_uri`: optional search scope, default `viking://user/memories`
- `limit`: optional max results
- `score_threshold`: optional minimum score

### `openviking_store`

Store a memory by creating a short OpenViking session, adding the text, and committing. Memory creation is extraction-dependent; the tool reports when OpenViking commits the session but extracts zero items.

Parameters:

- `text`: information to store
- `role`: optional message role, default `user`

### `openviking_forget`

Delete an exact memory URI. Use `openviking_recall` first to find the URI.

Parameters:

- `uri`: exact `viking://user/.../memories/...` or `viking://agent/.../memories/...`

### `openviking_health`

Check server reachability.

## Plugin Structure

```
codex-memory-plugin/
├── .codex-plugin/
│   └── plugin.json              # Plugin manifest (hooks + mcp wiring)
├── hooks/
│   └── hooks.json               # SessionStart + UserPromptSubmit + Stop + PreCompact
├── scripts/
│   ├── config.mjs               # Shared config loader (ovcli.conf + env)
│   ├── debug-log.mjs            # Structured JSONL logger
│   ├── runtime-common.mjs       # Plugin data root + install-state helpers
│   ├── bootstrap-runtime.mjs    # SessionStart installer
│   ├── start-memory-server.mjs  # Launches MCP server through the runtime
│   ├── auto-recall.mjs          # UserPromptSubmit hook
│   ├── auto-capture.mjs         # Stop hook
│   └── pre-compact-capture.mjs  # PreCompact hook (commits full transcript)
├── servers/
│   └── memory-server.js         # Compiled MCP server (checked in)
├── src/
│   └── memory-server.ts         # MCP server source
├── .mcp.json                    # MCP server definition (consumed by Codex)
├── package.json
├── tsconfig.json
└── README.md
```

## Differences from the Claude Code Plugin

| Aspect | Claude Code Plugin | Codex Plugin |
|--------|--------------------|--------------|
| Plugin root env var | `CLAUDE_PLUGIN_ROOT` | `CODEX_PLUGIN_ROOT` |
| Plugin data env var | `CLAUDE_PLUGIN_DATA` | `CODEX_PLUGIN_DATA` |
| `UserPromptSubmit` injection | `decision: "approve"` + `hookSpecificOutput.additionalContext` | `hookSpecificOutput.additionalContext` only — `approve` is not a Codex output |
| `Stop` decision | `decision: "approve"` no-op | `{}` no-op — only `block` is a valid Codex `decision` |
| Compaction hook | n/a (Claude Code does not expose one) | `PreCompact` — full-transcript commit before context loss |
| Config section | `claude_code` | `codex` |
| Default config file | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`, falls back to `ov.conf` |
| Identity headers | `X-OpenViking-Agent` only | Adds `X-OpenViking-Account` + `X-OpenViking-User` when configured |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
