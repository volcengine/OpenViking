# OpenViking Memory Plugin for Codex

Long-term semantic memory for [Codex](https://developers.openai.com/codex), powered by [OpenViking](https://github.com/volcengine/OpenViking).

This is the Codex counterpart to [`claude-code-memory-plugin`](../claude-code-memory-plugin). It hooks Codex's lifecycle to:

- **Auto-recall** relevant memories on every `UserPromptSubmit` and inject them via `hookSpecificOutput.additionalContext`
- **Auto-capture** the user turn (and last assistant message) on `Stop` by committing a short-lived OpenViking session
- **Pre-compact capture** the entire transcript on `PreCompact` so detail survives Codex's context summarization
- **Bootstrap** the MCP runtime once on `SessionStart`

It also exposes explicit MCP tools (`openviking_recall`, `openviking_store`, `openviking_forget`, `openviking_health`) for manual use.

## Architecture

```
                       ┌────────────────────────────────────────────┐
                       │                  Codex                     │
                       └────────┬───────────┬──────────┬─────────┬──┘
                                │           │          │         │
                          SessionStart  UserPromptSubmit  Stop  PreCompact
                                │           │          │         │
                       ┌────────▼──────┐ ┌──▼─────┐ ┌──▼─────┐ ┌─▼─────────────┐
                       │ bootstrap-    │ │ auto-  │ │ auto-  │ │ pre-compact-  │
                       │ runtime.mjs   │ │ recall │ │ capture│ │ capture       │
                       │  (npm ci)     │ │        │ │        │ │ (full commit) │
                       └────────┬──────┘ └───┬────┘ └────┬───┘ └───┬───────────┘
                                │            │           │         │
                                │       ┌────▼───────────▼─────────▼──┐
                                │       │     OpenViking server         │
                                │       │ /api/v1/search/find           │
                                │       │ /api/v1/sessions              │
                                │       │ /api/v1/sessions/{id}/commit  │
                                └──────►│ /api/v1/content/read          │
                                        └───────────────────────────────┘

  ┌──────────────────────────────────────┐
  │  MCP Server (memory-server.ts)       │
  │  Tools for explicit use:             │
  │  • openviking_recall                 │
  │  • openviking_store                  │
  │  • openviking_forget                 │
  │  • openviking_health                 │
  └──────────────────────────────────────┘
```

## How It Works

### Runtime bootstrap (transparent, on session start)

`SessionStart` fires `bootstrap-runtime.mjs`, which hashes `package.json` + `package-lock.json` + `servers/memory-server.js`, copies them into `${CODEX_PLUGIN_DATA}/runtime` (or `~/.openviking/codex-memory-plugin/runtime` if Codex doesn't inject `CODEX_PLUGIN_DATA`), and runs `npm ci --omit=dev`. `install-state.json` records the resolved hashes so subsequent sessions skip reinstall. The MCP launcher (`start-memory-server.mjs`) can also bootstrap on demand if it starts before `SessionStart`.

### Auto-recall (transparent, every turn)

`UserPromptSubmit` fires `auto-recall.mjs`. It reads `prompt` from stdin, calls `/api/v1/search/find` for both `viking://user/memories` and `viking://agent/memories` (and `viking://agent/skills`), ranks results with query-aware scoring (leaf boost, preference boost, temporal boost, lexical overlap), reads full content for top-ranked leaves, and emits:

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<relevant-memories>...</relevant-memories>" } }
```

Codex injects `additionalContext` into the model turn, so memories arrive without an extra tool call.

### Auto-capture (transparent, on Stop)

`Stop` fires `auto-capture.mjs`. Codex hands us `last_assistant_message`, `transcript_path`, `session_id`, and `turn_id`. The script:

1. Incrementally parses the rollout JSONL at `transcript_path` (skipping turns we've already captured this session)
2. Captures user turns (and assistant turns when `captureAssistantTurns=true`)
3. Captures `last_assistant_message` separately (deduped via hash) so Stop continues to work even if `transcript_path` is unavailable
4. Each capture creates an OpenViking session, posts the text, calls `/api/v1/sessions/{id}/commit`, and lets OV's pipeline extract memories asynchronously

State per `session_id` is kept under `$TMPDIR/openviking-codex-capture-state/`.

### Pre-compact capture (the key Codex extension)

`PreCompact` fires `pre-compact-capture.mjs` *before* Codex compacts the conversation. The script reads the entire transcript, opens **one** OpenViking session, posts every captured turn in order, and commits — so a structured extraction lands in long-term memory before Codex throws the detail away. The `Stop` state is also advanced so the next Stop won't re-capture pre-compact content.

### MCP tools (explicit, on demand)

The MCP server provides tools for when Codex or the user needs explicit memory operations. See "Tools" below.

## Codex hook output schema

Codex's hook output schema differs from Claude Code's. Notably:

| Hook | Input field of interest | Output channel for context injection |
|------|------------------------|--------------------------------------|
| `SessionStart`   | `source` (`startup`/`resume`/`clear`)         | `hookSpecificOutput.additionalContext` |
| `UserPromptSubmit` | `prompt`                                    | `hookSpecificOutput.additionalContext` |
| `Stop`           | `last_assistant_message`, `transcript_path`   | `systemMessage` (only) |
| `PreCompact`     | `trigger` (`manual`/`auto`), `transcript_path`| `systemMessage` (only) |

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
