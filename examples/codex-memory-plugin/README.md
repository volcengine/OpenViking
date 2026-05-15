# OpenViking Memory Plugin for Codex

Long-term semantic memory for [Codex](https://developers.openai.com/codex), powered by [OpenViking](https://github.com/volcengine/OpenViking).

This is the Codex counterpart to [`claude-code-memory-plugin`](../claude-code-memory-plugin). It hooks Codex's lifecycle to:

- **Auto-recall** relevant memories on every `UserPromptSubmit` and inject them via `hookSpecificOutput.additionalContext`
- **Incremental capture on `Stop`** (turn end): append the new user/assistant turns to a deterministic OpenViking session id `cx-<codex_session_id>`. No commit per turn.
- **Commit on `PreCompact`**: trigger OpenViking's memory extractor on the full pre-compact transcript before Codex summarizes it.
- **Commit on `SessionStart` (source=startup|clear)**: active-window heuristic — if exactly one *other* state file was touched within the last 2 min, commit it (the just-ended session). On `≥2`, defer to idle-TTL sweep at the tail. `source=resume` is a hard no-op (short reconnects re-fire `resume` and we don't want to commit a still-active session). See `DESIGN.md` for the full decision tree.

It also wires Codex up to OpenViking's native `/mcp` endpoint (streamable HTTP, Bearer auth), so the model has direct access to the `search`, `store`, `read`, `list`, `grep`, `glob`, `forget`, `add_resource`, and `health` tools — no local MCP server process to maintain.

## Quick Start

### One-line installer (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer:

1. Checks `codex`, `git`, and Node.js 22+
2. Clones or refreshes `~/.openviking/openviking-repo`
3. Registers a local `openviking-plugins-local` marketplace, enables `openviking-memory@openviking-plugins-local`, sets `features.plugin_hooks = true`
4. Renders the cached `.mcp.json` URL from `ovcli.conf` (or `OPENVIKING_URL`)
5. Renders the cached `hooks.json` with absolute script paths (Codex 0.130 doesn't inject `CODEX_PLUGIN_ROOT` into hook env)
6. Appends a `codex()` shell function to your rc that pulls `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` from `ovcli.conf` at invocation — keeps the key out of `.mcp.json` on disk

After install:

```bash
source ~/.zshrc   # or ~/.bashrc
codex             # first run: review /hooks once
```

### Manual setup

If you don't want the installer touching your rc, do these three things yourself:

1. **Wire a `codex()` shell function** that injects OpenViking creds at invocation time. The installer-emitted version (see `setup-helper/install.sh`) additionally re-renders the cached `.mcp.json` bearer field on each launch — required if you swap `OPENVIKING_CLI_CONFIG_FILE` between configs with and without `api_key`. A simpler equivalent for a fixed config:

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
     # Build env-prefix dynamically so empty values are NOT passed as empty
     # strings — Codex hard-fails on empty bearer_token_env_var targets.
     local -a _env_args=()
     [ -n "$_ov_url" ]     && _env_args+=("OPENVIKING_URL=$_ov_url")
     [ -n "$_ov_key" ]     && _env_args+=("OPENVIKING_API_KEY=$_ov_key")
     [ -n "$_ov_account" ] && _env_args+=("OPENVIKING_ACCOUNT=$_ov_account")
     [ -n "$_ov_user" ]    && _env_args+=("OPENVIKING_USER=$_ov_user")
     _env_args+=("OPENVIKING_AGENT_ID=${OPENVIKING_AGENT_ID:-codex}")
     env "${_env_args[@]}" codex "$@"
   }
   ```

2. **Add the plugin** via a local marketplace pointing at this directory. See `setup-helper/install.sh` for the exact `codex plugin marketplace add` invocation.

3. **Render the `__OPENVIKING_MCP_URL__` placeholder** in `.mcp.json` and the `__OPENVIKING_PLUGIN_ROOT__` placeholders in `hooks/hooks.json` to absolute values. The installer does this automatically when copying the plugin into Codex's cache; for manual setup you do it once with `sed`.

## Configuration

Connection / identity resolution order (highest to lowest, applies to both hooks and MCP):

1. **Environment variables**: `OPENVIKING_URL` / `OPENVIKING_BASE_URL`, `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_AGENT_ID`
2. **`ovcli.conf`**: `~/.openviking/ovcli.conf` or `OPENVIKING_CLI_CONFIG_FILE`
3. **`ov.conf`**: `~/.openviking/ov.conf` or `OPENVIKING_CONFIG_FILE` (only `server.url` / `server.root_api_key` as connection fallback; tuning fields under a legacy `codex.*` block are honored but deprecated — see [Tuning the plugin](#tuning-the-plugin))
4. **Built-in defaults**: `http://127.0.0.1:1933`, unauthenticated

The shell function wrapper handles step 1 for you by promoting ovcli.conf fields into env vars before exec'ing codex. Hooks then re-resolve the full chain inside Node; the MCP server URL is baked into `.mcp.json` at install time and the API key flows in via `OPENVIKING_API_KEY` (referenced by `bearer_token_env_var` in `.mcp.json`).

Auth is sent as `Authorization: Bearer <api_key>` to both the REST API (used by hooks) and the `/mcp` endpoint (used by the model).

For **unauthenticated local OV** (`ovcli.conf` without `api_key`, or no ovcli.conf at all), `.mcp.json` is rendered *without* `bearer_token_env_var`. Codex 0.130 hard-fails MCP startup with `Environment variable ... is empty` if `bearer_token_env_var` points at an empty/unset env var, so it must be omitted entirely when there's no key.

The `codex()` shell-function wrapper **re-renders this field on every codex launch** based on the currently-active `ovcli.conf` (the one `OPENVIKING_CLI_CONFIG_FILE` points at, falling back to `~/.openviking/ovcli.conf`). That means you can switch between authenticated and unauthenticated OV — e.g. to isolate a benchmark run from production memory — by just changing `OPENVIKING_CLI_CONFIG_FILE` before invoking `codex`, with no re-install needed. The wrapper also omits empty env-var assignments entirely (so `OPENVIKING_API_KEY=` is never passed to codex), keeping `env_http_headers` for identity (`X-OpenViking-Account` / `User` / `Agent`) intact.

### Tuning the plugin

All plugin behavior is controlled by `OPENVIKING_*` environment variables — set them in your shell rc (`~/.zshrc` / `~/.bashrc`) so every `codex` launch picks them up. The shell-function wrapper installed alongside the plugin already exports identity vars from `ovcli.conf`; tuning vars sit next to it.

```sh
# ~/.zshrc — examples
export OPENVIKING_RECALL_LIMIT=6
export OPENVIKING_CAPTURE_ASSISTANT_TURNS=1
export OPENVIKING_AUTO_COMMIT_ON_COMPACT=1
export OPENVIKING_DEBUG=1
```

Full list: see the `Misc env vars` block in `scripts/config.mjs`. Every field has a `OPENVIKING_*` counterpart and env vars always win.

#### Legacy `codex` block in `ov.conf`

Earlier plugin versions configured tuning fields under a `codex` block in `~/.openviking/ov.conf`. That still works for backward compat — every env var above has a camelCase counterpart (`OPENVIKING_RECALL_LIMIT` → `codex.recallLimit`, etc.) — but **new deployments should prefer env vars**: this is the codex CLI's per-machine plugin tuning, and the server-side `ov.conf` is the wrong place for it. (It's read from `ov.conf`, not `ovcli.conf`, by historical accident in `scripts/config.mjs`.)

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
      └────────────►│        OpenViking REST API                │
                    │ /api/v1/search/find                       │
                    │ /api/v1/sessions [+/{id}/{messages,commit}]│
                    │ /api/v1/content/read                      │
                    └─────────────────┬─────────────────────────┘
                                      │
   Codex ◄──────── streamable-HTTP MCP ◄ /mcp (search, store, read, list,
                   (bearer token via       grep, glob, forget,
                    OPENVIKING_API_KEY)    add_resource, health)
```

The plugin no longer bundles a local stdio MCP server. Codex talks to OpenViking's built-in `/mcp` endpoint directly via streamable HTTP, with `bearer_token_env_var: "OPENVIKING_API_KEY"` in `.mcp.json` so the key stays in `ovcli.conf` and the shell function — never on disk in `.mcp.json` itself.

For details on OpenViking's MCP endpoint, tools, and protocol, see the [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md). The tools list and per-tool semantics are documented there once, not duplicated here.

## How It Works

> See [`DESIGN.md`](./DESIGN.md) for the commit decision tree — it's the source of truth for *which* OpenViking session is sealed by *which* hook event.

### SessionStart commit logic (source=startup|clear, heuristic + idle TTL)

Codex fires `SessionStart` with one of three `source` values: `startup` (fresh process / `/new` / zouk daemon spawn-without-sessionId), `resume` (`/resume` or short reconnect), and `clear` (`/clear` — the previous transcript is orphaned and a new session_id is created). `resume` is the *only* source we treat as a hard no-op; on `startup` and `clear` we run the same active-window heuristic.

`hooks.json` registers `SessionStart` with `matcher: "clear|startup"` so codex's dispatcher invokes the script on both sources. `session-start-commit.mjs` gates internally on `source ∈ {startup, clear}` as defense-in-depth.

On `startup` or `clear`, the script:

1. Counts state files (excluding the new session_id) whose `lastUpdatedAt` is within `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` (default 2 min) of "now":
   - **0 active** → no-op (no orphan to commit)
   - **1 active** → commit it (the just-ended session)
   - **≥2 active** → skip; rely on idle TTL (we can't tell which one ended)
2. **Idle-TTL sweep at the tail**: any state file (regardless of session_id) older than `OPENVIKING_CODEX_IDLE_TTL_MS` (default 30 min) gets committed and cleared.

On any /commit failure (OV unreachable, non-2xx, timeout) we **preserve state** (don't `clearState`) so the next sweep can retry.

### Auto-recall (every UserPromptSubmit)

`auto-recall.mjs` reads `prompt` from stdin, calls `/api/v1/search/find`, ranks results, reads full content for top-ranked leaves, and emits:

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<relevant-memories>...</relevant-memories>" } }
```

Codex injects `additionalContext` into the model turn, so memories arrive without an extra tool call.

### Stop (turn end → `add_message`, NOT `commit`)

`auto-capture.mjs` derives one long-lived OpenViking session id per Codex `session_id` as `cx-<safe-session-id>` and incrementally appends every new user/assistant turn via `/api/v1/sessions/{id}/messages`. The `/messages` endpoint auto-creates the session on first append. Per-codex-session state lives at `~/.openviking/codex-plugin-state/<safe-session-id>.json`. No `/commit` per turn — that would over-fragment memory extraction.

### PreCompact (deterministic commit)

`pre-compact-capture.mjs`:

1. Catch-up append for any turns Stop hasn't captured yet (race-safe via `capturedTurnCount`)
2. Commit the long-lived OV session so the extractor runs against the full pre-compact transcript
3. Reset `ovSessionId` to `null` so the next `Stop` re-derives the same `cx-<safe-session-id>` and appends the post-compact half under that deterministic OV session id

### Known gap: SIGTERM / Ctrl+C / `/exit` are silent

Codex fires no hook on process exit. `/compact` is the only fully-deterministic "context disappearing" signal. If you `/exit` without `/compact`, the OV session for that codex session_id stays open. Two fallbacks recover the orphan:

1. The idle-TTL sweep at the next `SessionStart` commits any state file older than 30 min
2. The active-window heuristic catches it if you `/new` or `/clear` shortly after

## Codex hook output schema

Codex's hook output schema differs from Claude Code's. Notably:

| Hook | Input field of interest | Output channel for context injection |
|------|------------------------|--------------------------------------|
| `SessionStart`   | `source` (`startup`/`resume`/`clear`), `session_id` | `hookSpecificOutput.additionalContext` |
| `UserPromptSubmit` | `prompt`                                    | `hookSpecificOutput.additionalContext` |
| `Stop`           | `last_assistant_message`, `transcript_path`, `session_id` | `systemMessage` (only) |
| `PreCompact`     | `trigger` (`manual`/`auto`), `transcript_path`, `session_id` | `systemMessage` (only) |

Unlike Claude Code, **Codex does not support `decision: "approve"`**; only `decision: "block"`. A no-op is `{}` (which is what these scripts emit when there's nothing to add).

## Plugin Structure

```
codex-memory-plugin/
├── .codex-plugin/
│   └── plugin.json              # Plugin manifest (hooks + mcp wiring)
├── hooks/
│   └── hooks.json               # SessionStart + UserPromptSubmit + Stop + PreCompact
│                                  (uses __OPENVIKING_PLUGIN_ROOT__ placeholder;
│                                   installer renders to absolute paths)
├── scripts/
│   ├── config.mjs               # Shared config loader (ovcli.conf + env)
│   ├── debug-log.mjs            # Structured JSONL logger
│   ├── session-state.mjs        # Per-codex-session OV session state
│   ├── auto-recall.mjs          # UserPromptSubmit hook (REST /search/find)
│   ├── auto-capture.mjs         # Stop hook (REST /sessions/{id}/messages)
│   ├── session-start-commit.mjs # SessionStart hook (active-window + idle TTL)
│   └── pre-compact-capture.mjs  # PreCompact hook
├── setup-helper/
│   └── install.sh               # One-line installer
├── .mcp.json                    # Streamable-HTTP MCP wiring (renders __OPENVIKING_MCP_URL__)
├── DESIGN.md
├── VERIFICATION.md
└── README.md
```

No `src/`, `servers/`, `node_modules/`, or `package.json`: there is no local MCP server to build or run. All hook scripts are zero-dep `.mjs` running on Codex's bundled Node 22.

## Differences from the Claude Code Plugin

| Aspect | Claude Code Plugin | Codex Plugin |
|--------|--------------------|--------------|
| Plugin root env var | `CLAUDE_PLUGIN_ROOT` (expanded by CC) | `CODEX_PLUGIN_ROOT` (NOT expanded by Codex 0.130; installer renders absolute paths into the cached copies) |
| `UserPromptSubmit` injection | `decision: "approve"` + `hookSpecificOutput.additionalContext` | `hookSpecificOutput.additionalContext` only — `approve` is not a Codex output |
| `Stop` decision | `decision: "approve"` no-op | `{}` no-op — only `block` is a valid Codex `decision` |
| Compaction hook | n/a (Claude Code does not expose one) | `PreCompact` — full-transcript commit before context loss |
| Config section | `claude_code` | `codex` |
| Default config file | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`, falls back to `ov.conf` |
| MCP server | Local stdio (CC quirk: `.mcp.json` doesn't support env var auth) | Streamable HTTP to OpenViking's native `/mcp` (Codex supports `bearer_token_env_var`) |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
