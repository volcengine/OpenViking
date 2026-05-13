# Codex Memory Plugin

Long-term semantic memory for [Codex](https://developers.openai.com/codex). Auto-recalls relevant memories on every prompt, incrementally captures each turn, and commits to OpenViking's memory extractor before compaction — the model doesn't need to call any MCP tool explicitly.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

## Quick Start

### One-line installer (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer checks `codex`, `git`, and Node.js 22+, refreshes (or clones on first run) `~/.openviking/openviking-repo`, registers a local `openviking-plugins-local` marketplace, enables `openviking-memory@openviking-plugins-local`, sets `features.plugin_hooks = true`, and pre-populates Codex's plugin cache so the plugin resolves immediately. Rerunning the installer is idempotent — it always pulls latest before installing.

It uses `~/.openviking/ovcli.conf` when present; otherwise the plugin falls back to `http://127.0.0.1:1933` unless `OPENVIKING_URL` / `OPENVIKING_API_KEY` are set in the env.

After install, start Codex:

```bash
codex
```

Codex will prompt `/hooks` to review the four new lifecycle hooks the first time — approve them once. From then on, recall runs on every prompt and capture runs on every `Stop`.

### Manual setup

Prerequisites:

```bash
node --version    # >= 22
codex --version   # >= 0.130.0
codex features list | grep codex_hooks
```

Enable plugin lifecycle hooks in `~/.codex/config.toml`:

```toml
[features]
plugin_hooks = true
```

From an OpenViking checkout, register a local marketplace:

```bash
mkdir -p /tmp/ov-codex-mp/.claude-plugin
ln -s "$(pwd)/examples/codex-memory-plugin" /tmp/ov-codex-mp/openviking-memory
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

Pre-populate Codex's cache so the plugin resolves on first launch:

```bash
INSTALL_DIR=~/.codex/plugins/cache/openviking-plugins-local/openviking-memory
mkdir -p "$INSTALL_DIR"
cp -R "$(pwd)/examples/codex-memory-plugin" "$INSTALL_DIR/0.4.1"
```

Configure the OpenViking client, sharing the same file as the `ov` CLI:

```jsonc
// ~/.openviking/ovcli.conf
{
  "url": "https://ov.example.com",
  "api_key": "<your-key>",
  "account": "default",
  "user": "<your-user>"
}
```

For local-server mode (`http://127.0.0.1:1933`) you can skip this file.

`npm install && npm run build` is only needed when editing `src/memory-server.ts` — the checked-in plugin already includes `servers/memory-server.js`.

## Configuration

Resolution priority for every connection / identity field — env vars always win:

1. **Environment variables** (`OPENVIKING_*`)
2. **`ovcli.conf`** — `~/.openviking/ovcli.conf` or `OPENVIKING_CLI_CONFIG_FILE`
3. **`ov.conf`** — `~/.openviking/ov.conf` or `OPENVIKING_CONFIG_FILE` (top-level `server.*` + optional `codex.*` tuning block)
4. **Built-in defaults** (`http://127.0.0.1:1933`, unauthenticated)

Setting `OPENVIKING_URL` alone is enough to run in env-var-only mode (no config files needed) — useful for daemon-spawned agents.

Auth is sent as `Authorization: Bearer <api_key>` plus the legacy `X-API-Key` header during the transition window.

### Key environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API key (sent as `Authorization: Bearer` either way) |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT_ID` | — | Multi-tenant identity headers |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | Alternate `ovcli.conf` path |
| `OPENVIKING_CONFIG_FILE` | `~/.openviking/ov.conf` | Alternate `ov.conf` path |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart active-window threshold |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle-TTL sweep threshold |
| `OPENVIKING_DEBUG` | `false` | Write JSONL events to `~/.openviking/logs/codex-hooks.log` |

Optional Codex-specific tuning lives under `codex` in `ovcli.conf`:

```jsonc
{
  "url": "https://ov.example.com",
  "api_key": "...",
  "codex": {
    "agentId": "codex",
    "recallLimit": 6,
    "autoCommitOnCompact": true,
    "debug": false
  }
}
```

The full field list is in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#configuration).

## Hook behavior

| Hook | When | Behavior |
|------|------|----------|
| `SessionStart` (matcher `clear\|startup`) | Fresh process, `/new`, or `/clear` | Active-window heuristic: if exactly one other state file was touched in the last 2 min, commit it (the just-ended session). Idle-TTL sweep at the tail picks up SIGTERM/`/exit` orphans older than 30 min. `source=resume` is a hard no-op. |
| `UserPromptSubmit` | Every prompt | Search OpenViking, rank, inject top results into `hookSpecificOutput.additionalContext`. |
| `Stop` | Every turn end | Append new user/assistant turns to the long-lived OpenViking session keyed by Codex `session_id`. No commit per turn. |
| `PreCompact` | Before Codex summarizes | Catch-up append + commit so OpenViking's extractor runs against the full pre-compact transcript, then null `ovSessionId` so the next `Stop` opens a fresh OV session. |

`Stop` deliberately does not commit per turn — committing extracts memories, and per-turn extraction would over-fragment the memory tree. The decision tree behind which hook seals which OV session is in [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md).

The MCP server (`servers/memory-server.js`) is launched lazily by `scripts/start-memory-server.mjs` on first MCP invocation — `npm ci` runs into `${CODEX_PLUGIN_DATA}/runtime` once and is reused after that.

### Known gap: SIGTERM / Ctrl+C / `/exit` are silent

Codex fires no hook on process exit. If you `/exit` without `/compact`, the OV session for that codex session_id stays open server-side. Two fallbacks recover the orphan:

1. The next `SessionStart` (source=startup|clear) idle-TTL sweep commits any state file older than 30 min.
2. The active-window heuristic catches the orphan if you `/new` or `/clear` shortly after.

If you need to preserve memory from a specific session, run `/compact` first or call `openviking_store` with the conclusions you want kept.

## MCP tools

For explicit memory operations, the plugin also exposes four MCP tools:

- `openviking_recall` — search memories
- `openviking_store` — store a memory by creating a short OV session and committing
- `openviking_forget` — delete an exact memory URI
- `openviking_health` — server reachability check

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MCP startup failed: connection closed: initialize response` | MCP launcher couldn't find the script (e.g., stale plugin cache) | Re-run the [one-line installer](#one-line-installer-recommended) — it now refreshes `~/.openviking/openviking-repo` |
| `4 hooks need review before they can run` | First-launch security review | Open `/hooks` in Codex and approve |
| Hook runs but recall returns nothing | OpenViking server unreachable or wrong URL | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Remote auth 401/403 | API key wrong or multi-tenant headers missing | Check `OPENVIKING_API_KEY`; multi-tenant also needs `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |
| `Stop` hook timeout | Server slow + sync write path | Raise the `Stop` timeout in `hooks/hooks.json` |

Verbose debug logging: set `OPENVIKING_DEBUG=1` or `codex.debug=true` in `ovcli.conf` to write JSON-Lines events to `~/.openviking/logs/codex-hooks.log`.

## Differences from the Claude Code plugin

| Aspect | Claude Code Plugin | Codex Plugin |
|--------|--------------------|--------------|
| Plugin root env | `CLAUDE_PLUGIN_ROOT` | `CODEX_PLUGIN_ROOT` |
| `UserPromptSubmit` output | `decision: "approve"` + `additionalContext` | `additionalContext` only — `approve` is not a Codex output |
| Compaction hook | n/a | `PreCompact` — full-transcript commit before context loss |
| Config section | `claude_code` | `codex` |
| Default config file | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`, falls back to `ov.conf` |

## See also

- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — full env-var list, validation SOP, architecture diagram
- [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — commit decision tree
- [MCP Integration Guide](../guides/06-mcp-integration.md) — for clients without lifecycle hooks
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup
