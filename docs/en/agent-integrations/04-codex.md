# Codex Memory Plugin

Long-term semantic memory for [Codex](https://developers.openai.com/codex). Auto-recalls relevant memories on every prompt, incrementally captures each turn, commits to OpenViking's memory extractor before compaction, and wires Codex up to OpenViking's native `/mcp` endpoint so the model can `search` / `store` / `read` / `grep` / `glob` / `list` / `forget` / `add_resource` memories directly — no local MCP server to maintain.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

## Quick Start

### One-line installer (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer checks `codex`, `git`, and Node.js 22+, refreshes (or clones on first run) `~/.openviking/openviking-repo`, registers a local `openviking-plugins-local` marketplace, enables `openviking-memory@openviking-plugins-local`, sets `features.plugin_hooks = true`, and pre-populates Codex's plugin cache so the plugin resolves immediately. Rerunning the installer is idempotent — it always pulls latest before installing.

It reads `~/.openviking/ovcli.conf` for the OpenViking URL, renders the `/mcp` endpoint into the cached `.mcp.json`, and appends a `codex()` shell function to your rc that pulls `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT_ID` from ovcli.conf at every codex invocation. The API key stays in `ovcli.conf`; the `.mcp.json` on disk only references `OPENVIKING_API_KEY` via `bearer_token_env_var`, never embeds it.

After install:

```bash
source ~/.zshrc    # or ~/.bashrc
codex              # first run: review /hooks once when prompted
```

### Manual setup

Prerequisites:

```bash
node --version    # >= 22
codex --version   # >= 0.130.0
codex features list | grep codex_hooks
```

Three steps the installer does for you, that you can do manually:

1. **Shell function wrapper** in `~/.zshrc` / `~/.bashrc`. The installer-emitted version (see `setup-helper/install.sh`) additionally re-renders the cached `.mcp.json` bearer field on each launch, which is required if you swap `OPENVIKING_CLI_CONFIG_FILE` between configs with and without `api_key`. A simpler fixed-config equivalent:

   ```bash
   codex() {
     local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
     local _ov_url _ov_key _ov_account _ov_user
     if [ -f "$_ov_conf" ] && command -v node >/dev/null 2>&1; then
       local _ov_env
       _ov_env=$(node -e '
         try {
           const c = JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8"));
           const out = (k, v) => v ? `${k}=${JSON.stringify(String(v))}\n` : "";
           process.stdout.write(
             out("OV_URL", c.url) +
             out("OV_KEY", c.api_key) +
             out("OV_ACCOUNT", c.account) +
             out("OV_USER", c.user)
           );
         } catch {}
       ' "$_ov_conf" 2>/dev/null)
       eval "$_ov_env"
     fi
     _ov_url="${OPENVIKING_URL:-${OV_URL:-}}"
     _ov_key="${OPENVIKING_API_KEY:-${OV_KEY:-}}"
     _ov_account="${OPENVIKING_ACCOUNT:-${OV_ACCOUNT:-}}"
     _ov_user="${OPENVIKING_USER:-${OV_USER:-}}"
     unset OV_URL OV_KEY OV_ACCOUNT OV_USER
     # Empty values are NOT exported — Codex hard-fails on empty
     # bearer_token_env_var targets.
     local -a _env_args=()
     [ -n "$_ov_url" ]     && _env_args+=("OPENVIKING_URL=$_ov_url")
     [ -n "$_ov_key" ]     && _env_args+=("OPENVIKING_API_KEY=$_ov_key")
     [ -n "$_ov_account" ] && _env_args+=("OPENVIKING_ACCOUNT=$_ov_account")
     [ -n "$_ov_user" ]    && _env_args+=("OPENVIKING_USER=$_ov_user")
     _env_args+=("OPENVIKING_AGENT_ID=${OPENVIKING_AGENT_ID:-codex}")
     env "${_env_args[@]}" codex "$@"
   }
   ```

2. **Plugin install** via a local marketplace pointing at the plugin directory. See `setup-helper/install.sh` for the exact `codex plugin marketplace add` invocation.

3. **Placeholder rendering**: the checked-in `.mcp.json` keeps `__OPENVIKING_MCP_URL__` and `hooks/hooks.json` keeps `__OPENVIKING_PLUGIN_ROOT__`; both must be `sed`-substituted to absolute values when the plugin is copied into Codex's cache (`~/.codex/plugins/cache/...`). The installer does this automatically.

## Configuration

Resolution priority for every connection / identity field — env vars always win:

1. **Environment variables** (`OPENVIKING_*`)
2. **`ovcli.conf`** — `~/.openviking/ovcli.conf` or `OPENVIKING_CLI_CONFIG_FILE`
3. **`ov.conf`** — `~/.openviking/ov.conf` or `OPENVIKING_CONFIG_FILE` (only `server.url` / `server.root_api_key` as connection fallback; a legacy `codex.*` tuning block is still honored but deprecated — see [Tuning](#tuning))
4. **Built-in defaults** (`http://127.0.0.1:1933`, unauthenticated)

Hooks resolve this chain on every fire (changes to ovcli.conf take effect on the next hook). The MCP server URL is baked into `.mcp.json` at install time (changing the URL requires a re-install); the API key is read fresh from env on every codex launch via `bearer_token_env_var`, so rotating `OPENVIKING_API_KEY` in ovcli.conf only requires a codex restart, not a re-install.

Auth is sent as `Authorization: Bearer <api_key>` to both the REST API (used by hooks) and the `/mcp` endpoint (used by the model).

For **unauthenticated local OV** (`ovcli.conf` without `api_key`, or no ovcli.conf at all), `.mcp.json` is rendered *without* `bearer_token_env_var`. Codex 0.130 hard-fails MCP startup with `Environment variable ... is empty` when `bearer_token_env_var` points at an empty/unset env var. The `codex()` shell-function wrapper re-renders this field on every codex launch based on whichever ovcli.conf `OPENVIKING_CLI_CONFIG_FILE` is currently pointing at, so swapping configs (e.g. for benchmark isolation) doesn't require a re-install. Identity headers still flow through `env_http_headers`.

### Key environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL (the `/mcp` endpoint is derived from this at install time) |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | — | API key, sent as `Authorization: Bearer` |
| `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_AGENT_ID` | — | Multi-tenant identity headers |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | Alternate `ovcli.conf` path |
| `OPENVIKING_CONFIG_FILE` | `~/.openviking/ov.conf` | Alternate `ov.conf` path |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart active-window threshold |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle-TTL sweep threshold |
| `OPENVIKING_DEBUG` | `false` | Write JSONL events to `~/.openviking/logs/codex-hooks.log` |

### Tuning

Plugin tuning is set via `OPENVIKING_*` environment variables in your shell rc — they take effect on every codex launch.

```sh
# ~/.zshrc
export OPENVIKING_RECALL_LIMIT=6
export OPENVIKING_CAPTURE_ASSISTANT_TURNS=1
export OPENVIKING_AUTO_COMMIT_ON_COMPACT=1
export OPENVIKING_DEBUG=1
```

The full field list is in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin).

> **Legacy `codex.*` block**: earlier versions read tuning fields from a `codex` block in `~/.openviking/ov.conf`; that still works for backward compat. But codex is a client-side plugin and per-machine tuning doesn't belong in a server-scope `ov.conf` — new deployments should prefer env vars.

## Hook behavior

| Hook | When | Behavior |
|------|------|----------|
| `SessionStart` (matcher `clear\|startup`) | Fresh process, `/new`, or `/clear` | Active-window heuristic: if exactly one other state file was touched in the last 2 min, commit it (the just-ended session). Idle-TTL sweep at the tail picks up SIGTERM/`/exit` orphans older than 30 min. `source=resume` is a hard no-op. |
| `UserPromptSubmit` | Every prompt | Search OpenViking REST `/search/find`, rank, inject top results into `hookSpecificOutput.additionalContext`. |
| `Stop` | Every turn end | Append new user/assistant turns to the long-lived OpenViking session keyed by Codex `session_id`. No commit per turn. |
| `PreCompact` | Before Codex summarizes | Catch-up append + commit so OpenViking's extractor runs against the full pre-compact transcript, then null `ovSessionId` so the next `Stop` opens a fresh OV session. |

`Stop` deliberately does not commit per turn — committing extracts memories, and per-turn extraction would over-fragment the memory tree. The decision tree behind which hook seals which OV session is in [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md).

### Known gap: SIGTERM / Ctrl+C / `/exit` are silent

Codex fires no hook on process exit. If you `/exit` without `/compact`, the OV session for that codex session_id stays open server-side. Two fallbacks recover the orphan:

1. The next `SessionStart` (source=startup|clear) idle-TTL sweep commits any state file older than 30 min.
2. The active-window heuristic catches the orphan if you `/new` or `/clear` shortly after.

## MCP tools

The plugin wires Codex up to OpenViking's built-in `/mcp` endpoint via streamable HTTP. Tool list, per-tool semantics, and protocol details live in the [MCP Integration Guide](../guides/06-mcp-integration.md) — not duplicated here.

`.mcp.json` ships with the OV server URL rendered at install time and uses `bearer_token_env_var: "OPENVIKING_API_KEY"` plus `env_http_headers` for the multi-tenant identity headers. The API key never lands on disk in `.mcp.json`; it's pulled from env at codex launch by the shell function wrapper.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MCP server is not logged in. Run codex mcp login` | `OPENVIKING_API_KEY` not in env at codex launch, and OV server returned 401, so Codex fell back to OAuth | Make sure the `codex()` shell function is sourced (`type codex` should say "shell function") and `ovcli.conf` has `api_key` |
| `4 hooks need review before they can run` | First-launch security review | Open `/hooks` in Codex and approve |
| `hook (failed) exited with code 1` after approval | Stale `hooks.json` placeholder; cache wasn't re-rendered | Rerun the one-line installer |
| Hook runs but recall returns nothing | OpenViking server unreachable or wrong URL | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Remote auth 401/403 from hooks but MCP works (or vice versa) | env vs ovcli.conf mismatch | Hooks re-read ovcli.conf every fire; MCP reads env only at codex start. Restart codex if you changed env. |

Verbose debug logging: set `OPENVIKING_DEBUG=1` (or legacy `codex.debug=true` in `ov.conf`) to write JSON-Lines events to `~/.openviking/logs/codex-hooks.log`.

## Differences from the Claude Code plugin

| Aspect | Claude Code Plugin | Codex Plugin |
|--------|--------------------|--------------|
| Plugin root env | `CLAUDE_PLUGIN_ROOT` (expanded by CC) | `CODEX_PLUGIN_ROOT` (NOT expanded by Codex 0.130; installer renders absolute paths) |
| `UserPromptSubmit` output | `decision: "approve"` + `additionalContext` | `additionalContext` only — `approve` is not a Codex output |
| Compaction hook | n/a | `PreCompact` — full-transcript commit before context loss |
| Config section | `claude_code` | `codex` |
| Default config file | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`, falls back to `ov.conf` |
| MCP server | Local stdio (CC's `.mcp.json` doesn't support env-var Bearer) | Streamable-HTTP to OpenViking's native `/mcp` |

## See also

- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — full env-var list, validation SOP, architecture diagram
- [`DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — commit decision tree
- [MCP Integration Guide](../guides/06-mcp-integration.md) — protocol, tools, and how OpenViking exposes `/mcp`
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup
