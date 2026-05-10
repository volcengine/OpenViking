# OpenViking Memory Plugin for Codex

Long-term semantic memory for [Codex](https://developers.openai.com/codex), powered by [OpenViking](https://github.com/volcengine/OpenViking).

This is the Codex counterpart to [`claude-code-memory-plugin`](../claude-code-memory-plugin). It hooks Codex's lifecycle to:

- **Auto-recall** relevant memories on every `UserPromptSubmit` and inject them via `hookSpecificOutput.additionalContext`
- **Incremental capture on `Stop`** (turn end): append the new user/assistant turns to a single long-lived OpenViking session keyed by Codex `session_id`. No commit per turn.
- **Commit on `PreCompact`**: trigger OpenViking's memory extractor on the full pre-compact transcript before Codex summarizes it.
- **Idle sweep on `Stop`**: opportunistically commit OV sessions whose Codex session has been silent past the idle TTL (default 30 min) — best-effort session-end signal because Codex has no `SessionEnd` hook today.
- **MCP runtime bootstrap is lazy**: the MCP launcher (`start-memory-server.mjs`) installs runtime deps on first MCP invocation. We do **not** register a `SessionStart` hook, so short reconnects don't re-trigger `npm ci`.

It also exposes explicit MCP tools (`openviking_recall`, `openviking_store`, `openviking_forget`, `openviking_health`) for manual use.

## Architecture

```
                ┌──────────────────────────────────────┐
                │                Codex                 │
                └──────┬───────────────┬────────────┬──┘
                       │               │            │
                UserPromptSubmit      Stop      PreCompact
                       │               │            │
                ┌──────▼─────┐  ┌──────▼─────┐  ┌──▼─────────────┐
                │ auto-      │  │ auto-      │  │ pre-compact-   │
                │ recall.mjs │  │ capture.mjs│  │ capture.mjs    │
                │ (search)   │  │ (append +  │  │ (commit +      │
                │            │  │ idle-sweep)│  │ reset session) │
                └──────┬─────┘  └──────┬─────┘  └──────┬─────────┘
                       │               │               │
                       │       ┌───────▼───────────────▼────┐
                       └──────►│      OpenViking server     │
                               │ /api/v1/search/find        │
                               │ /api/v1/sessions           │
                               │ /api/v1/sessions/{id}/     │
                               │   messages | commit        │
                               │ /api/v1/content/read       │
                               └────────────────────────────┘

  ┌──────────────────────────────────────┐
  │  MCP Server (memory-server.ts)       │
  │  Tools for explicit use:             │
  │  • openviking_recall                 │
  │  • openviking_store                  │
  │  • openviking_forget                 │
  │  • openviking_health                 │
  │  Lazily npm ci's its runtime on      │
  │  first launch (not on SessionStart). │
  └──────────────────────────────────────┘
```

## How It Works

### Why no `SessionStart` hook

Codex fires `SessionStart` on every short reconnect and resume — not just genuine new sessions. Registering a hook that runs `npm ci` on every `SessionStart` is the wrong shape: it would reinstall the runtime on every reconnect, and short reconnects don't need a memory boundary. Instead we **lazily bootstrap** the MCP runtime in `scripts/start-memory-server.mjs` the first time codex actually launches the MCP server. The bootstrap is content-hashed and idempotent (`scripts/runtime-common.mjs`), so subsequent launches are no-ops.

### Auto-recall (every UserPromptSubmit)

`auto-recall.mjs` reads `prompt` from stdin, calls `/api/v1/search/find` for both `viking://user/memories` and `viking://agent/memories` (and `viking://agent/skills`), ranks results with query-aware scoring (leaf boost, preference boost, temporal boost, lexical overlap), reads full content for top-ranked leaves, and emits:

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<relevant-memories>...</relevant-memories>" } }
```

Codex injects `additionalContext` into the model turn, so memories arrive without an extra tool call.

### Stop (turn end → `add_message`, NOT `commit`)

Codex's `Stop` fires per turn, not at session end. So `auto-capture.mjs` keeps **one** long-lived OpenViking session per Codex `session_id` and incrementally appends every new user/assistant turn from the rollout JSONL via `/api/v1/sessions/{id}/messages`. Per-codex-session state lives at `~/.openviking/codex-plugin-state/<safe-session-id>.json` and tracks `{ ovSessionId, capturedTurnCount, lastUpdatedAt }`.

We do **not** call `/commit` per turn — committing extracts memories, and per-turn extraction would over-fragment the memory tree and waste OV's extractor.

### Idle sweep (best-effort session-end commit)

Codex has no `SessionEnd` hook today (the schema only ships `SessionStart`, `UserPromptSubmit`, `Stop`, `PreCompact`, `PostCompact`, and tool-use events). To still produce memories from sessions that exit gracefully without compacting, every `Stop` invocation also runs an idle sweep at the end: any tracked codex session whose state file is older than `IDLE_TTL` (default 30 min, override with `OPENVIKING_CODEX_IDLE_TTL_MS`) gets committed and its state file removed.

### PreCompact (deterministic commit)

`PreCompact` fires before Codex summarizes. `pre-compact-capture.mjs` does:

1. **Catch-up**: append any transcript turns Stop hasn't captured yet (race-safe via `capturedTurnCount`).
2. **Commit** the long-lived OV session for this Codex `session_id` so OV's extractor runs against the full pre-compact transcript.
3. **Reset** state: clear `ovSessionId` so the next `Stop` opens a fresh OV session for the post-compact half. `capturedTurnCount` stays so we don't re-capture pre-compact turns.

### MCP tools (explicit, on demand)

The MCP server provides tools for when Codex or the user needs explicit memory operations. See "Tools" below.

## Codex hook output schema

Codex's hook output schema differs from Claude Code's. Notably:

| Hook | Input field of interest | Output channel for context injection |
|------|------------------------|--------------------------------------|
| `UserPromptSubmit` | `prompt`                                    | `hookSpecificOutput.additionalContext` |
| `Stop`           | `last_assistant_message`, `transcript_path`, `session_id` | `systemMessage` (only) |
| `PreCompact`     | `trigger` (`manual`/`auto`), `transcript_path`, `session_id` | `systemMessage` (only) |

> Note: this plugin no longer registers `SessionStart`. Codex fires it on short reconnects too, and the MCP runtime install belongs in `start-memory-server.mjs` (lazy on first MCP call), not in a per-reconnect hook.

Unlike Claude Code, **Codex does not support `decision: "approve"`**; only `decision: "block"`. A no-op is `{}` (which is what these scripts emit when there's nothing to add).

Source: [`codex-rs/hooks/schema/generated/`](https://github.com/openai/codex/tree/main/codex-rs/hooks/schema/generated).

## Quick Start

### 1. Install Node.js 22+ and Codex 0.124+

```bash
node --version    # >= 22
codex --version   # >= 0.124.0
```

Make sure `codex_hooks` is enabled (it's stable since April 2026):

```bash
codex features list | grep codex_hooks
# codex_hooks  stable  true
```

### 2. Configure OpenViking client

The plugin reads connection settings from `~/.openviking/ovcli.conf` (the same file the `ov` CLI uses). For a cloud OpenViking deployment:

```jsonc
{
  "url": "https://ov.example.com",
  "api_key": "<your-key>",
  "account": "default",
  "user": "<your-user>"
}
```

For a local server, omit `url` and the plugin will fall back to `~/.openviking/ov.conf`'s `server.host` / `server.port`.

Plugin-specific overrides go in an optional `codex` section:

```jsonc
{
  "url": "https://ov.example.com",
  "api_key": "...",
  "codex": {
    "agentId": "codex",
    "recallLimit": 6,
    "captureMode": "semantic",
    "captureAssistantTurns": false,
    "autoCommitOnCompact": true
  }
}
```

### 3. Install the plugin

The plugin lives at `examples/codex-memory-plugin/` in the OpenViking repo. Once a marketplace ships it, install with:

```bash
codex plugin marketplace add <marketplace-source>
# then enable in ~/.codex/config.toml:
# [plugins."openviking-memory@<marketplace-name>"]
# enabled = true
```

For local development, point a tiny marketplace fixture at this directory:

```bash
mkdir -p /tmp/ov-codex-mp/.claude-plugin
ln -s /abs/path/to/OpenViking/examples/codex-memory-plugin /tmp/ov-codex-mp/openviking-memory
cat > /tmp/ov-codex-mp/.claude-plugin/marketplace.json <<'EOF'
{
  "name": "openviking-codex-local",
  "plugins": [
    { "name": "openviking-memory", "source": "./openviking-memory" }
  ]
}
EOF
codex plugin marketplace add /tmp/ov-codex-mp

# Enable the plugin
cat >> ~/.codex/config.toml <<'EOF'

[plugins."openviking-memory@openviking-codex-local"]
enabled = true
EOF

# Codex installs plugins lazily — for fastest iteration, copy the plugin into
# the cache so it resolves immediately:
INSTALL_DIR=~/.codex/plugins/cache/openviking-codex-local/openviking-memory
mkdir -p "$INSTALL_DIR"
cp -R /abs/path/to/OpenViking/examples/codex-memory-plugin "$INSTALL_DIR/0.2.0"
```

### 4. Build the MCP server

```bash
cd examples/codex-memory-plugin
npm install
npm run build
```

The MCP server compiles to `servers/memory-server.js`, which `start-memory-server.mjs` launches via the bootstrapped runtime.

### 5. Start a Codex session

```bash
codex
```

The first session installs runtime deps; subsequent sessions skip reinstall.

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

Connection settings (URL, account, user, api_key) come from `ovcli.conf` plus standard env overrides:

- `OPENVIKING_CONFIG_FILE`: alternate config path (defaults to `~/.openviking/ovcli.conf`, then `~/.openviking/ov.conf`)
- `OPENVIKING_URL`: override server URL
- `OPENVIKING_API_KEY`: override API key
- `OPENVIKING_ACCOUNT`: override account
- `OPENVIKING_USER`: override user
- `OPENVIKING_AGENT_ID`: override agent identity

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
