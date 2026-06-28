# OpenViking Memory Plugin for Codex

Long-term semantic memory for [Codex](https://developers.openai.com/codex), powered by [OpenViking](https://github.com/volcengine/OpenViking).

This is the Codex counterpart to [`claude-code-memory-plugin`](../claude-code-memory-plugin). It hooks Codex's lifecycle to:

- **Auto-recall** relevant memories on every `UserPromptSubmit` and inject them via `hookSpecificOutput.additionalContext`
- **Incremental capture on `Stop`** (turn end): append the new user/assistant turns to a deterministic OpenViking session id `cx-<codex_session_id>`. No commit per turn.
- **Commit on `PreCompact`**: trigger OpenViking's memory extractor on the full pre-compact transcript before Codex summarizes it.
- **Commit on `SessionStart` (source=startup|clear)**: active-window heuristic — if exactly one *other* state file was touched within the last 2 min, commit it (the just-ended session). On `≥2`, defer to idle-TTL sweep at the tail. `source=resume` never commits or sweeps; if the live OV session was already committed, it may inject the latest archive summary for continuity. See `DESIGN.md` for the full decision tree.

It also wires Codex up to OpenViking's native `/mcp` endpoint (streamable HTTP, Bearer auth), so the model has direct access to the `search`, `store`, `read`, `list`, `grep`, `glob`, `forget`, `add_resource`, and `health` tools — no local MCP server process to maintain.

## Quick Start

There are two install paths. **Pick one — don't mix them** (both surface the same `openviking-memory` plugin; enabling it from both would run the hooks twice).

### A. Codex marketplace install (recommended for local / self-hosted)

The repo ships a Codex marketplace catalog at `.agents/plugins/marketplace.json`, so you can install with Codex's native commands — no `curl | bash`, no shell-rc wrapper:

```bash
# 1. add the OpenViking marketplace (use volcengine/OpenViking once merged
#    upstream, or <your-fork>/OpenViking while testing a fork)
codex plugin marketplace add volcengine/OpenViking

# 2. install the plugin from that marketplace
#    (older Codex builds spell this `codex plugin install`)
codex plugin add openviking-memory@openviking
```

Then enable plugin hooks (if your Codex build doesn't already) by adding to `~/.codex/config.toml`:

```toml
[features]
plugin_hooks = true
```

Finally start Codex and trust the plugin hooks once:

```bash
codex            # then run /hooks inside Codex to review & approve the hooks
```

> **Requirements & notes**
>
> - **Codex version**: this path relies on Codex injecting and inline-substituting `${PLUGIN_ROOT}` in plugin hook commands (current Codex does both). On an older Codex that doesn't substitute `${PLUGIN_ROOT}`, the hook script paths won't resolve — use path **B**, whose installer renders `${PLUGIN_ROOT}` to absolute paths for old Codex.
> - **Catalog source**: the catalog entry (`.agents/plugins/marketplace.json`) uses a relative source (`./examples/codex-memory-plugin`). `codex plugin add` therefore installs the plugin from the same marketplace snapshot/ref that you added. This keeps fork, branch, tag, and upstream-main installs reproducible and testable without rewriting the catalog.

This path works **out of the box against an unauthenticated local OpenViking** at `http://127.0.0.1:1933`: the shipped `.mcp.json` defaults to that `/mcp` URL and the hooks reference Codex's native `${PLUGIN_ROOT}` token (Codex injects it into the hook env and substitutes it inline), so the model gets the `/mcp` tools and all four lifecycle hooks with **zero rendering**.

For an **authenticated or remote/cloud** server on this path:

- **Hooks** (the REST calls) read `~/.openviking/ovcli.conf` (or `OPENVIKING_*` env) directly — set `url` / `api_key` / `account` / `user` there.
- **MCP auth**: Codex reads the bearer token from an env var, so export `OPENVIKING_API_KEY` in the shell that launches Codex *and* add `"bearer_token_env_var": "OPENVIKING_API_KEY"` to the installed plugin's cached `.mcp.json`. It's omitted by default because Codex **hard-fails** MCP startup when a `bearer_token_env_var` points at an unset/empty variable. Also note Codex doesn't let user config override a plugin's MCP `url`, so cloud users who want everything wired automatically should prefer path **B**.

### B. One-line installer — `curl | bash` (recommended for Volcengine Cloud / managed credentials)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer:

1. Checks `codex`, `git`, and Node.js 22+
2. Clones or refreshes `~/.openviking/openviking-repo`
3. Registers a local `openviking-plugins-local` marketplace, enables `openviking-memory@openviking-plugins-local`, sets `features.plugin_hooks = true`
4. Renders the cached `.mcp.json` URL **and** bearer/identity headers from your active `ovcli.conf`
5. Renders the cached `hooks.json` `${PLUGIN_ROOT}` token to absolute script paths (so even older Codex builds that don't substitute `${PLUGIN_ROOT}` work)
6. Appends a `codex()` shell function to your rc that resolves the active OpenViking CLI config at invocation, injects the matching env for Codex/MCP, and strips stale inherited credential env vars

After install:

```bash
source ~/.zshrc   # or ~/.bashrc
codex             # first run: review /hooks once
```

### Manual setup

If you don't want the installer touching your rc, do these three things yourself:

1. **Wire the plugin shell wrapper** so Codex gets the same active credentials as the `ov` CLI. The wrapper calls `scripts/ov-credentials.mjs`, re-renders the cached `.mcp.json` bearer field on each launch, and launches Codex with stale credential env vars stripped before the resolved values are added back:

   ```bash
   [ -f "$HOME/.openviking/openviking-repo/examples/codex-memory-plugin/setup-helper/wrapper.sh" ] \
     && . "$HOME/.openviking/openviking-repo/examples/codex-memory-plugin/setup-helper/wrapper.sh"
   ```

2. **Add the plugin** via a local marketplace pointing at this directory. See `setup-helper/install.sh` for the exact `codex plugin marketplace add` invocation.

3. **Point the cached `.mcp.json` at your server.** The checked-in `.mcp.json` defaults to `http://127.0.0.1:1933/mcp` with no bearer. For a remote/authenticated server, set the `url` and add `"bearer_token_env_var": "OPENVIKING_API_KEY"` — `node scripts/ov-credentials.mjs sync-mcp <path-to-cached .mcp.json>` does both from your active `ovcli.conf`. `hooks/hooks.json` needs **no** rendering on modern Codex: it uses the native `${PLUGIN_ROOT}` token, which Codex injects into the hook env and substitutes inline. Only older Codex builds that don't substitute `${PLUGIN_ROOT}` require replacing it with an absolute path (the installer does this for its cache copy).

   Codex App stores the installed plugin under its cache, not in this source
   tree. Common cache locations are:

   - macOS/Linux: `~/.codex/plugins/cache/openviking/openviking-memory/<version>/`
   - Windows: `%USERPROFILE%\.codex\plugins\cache\openviking\openviking-memory\<version>\`

   To re-render the installed MCP config on Windows after changing
   `~/.openviking/ovcli.conf`:

   ```powershell
   $PluginCache = Get-ChildItem "$env:USERPROFILE\.codex\plugins\cache\openviking\openviking-memory" -Directory |
     Sort-Object LastWriteTime -Descending |
     Select-Object -First 1
   node "$($PluginCache.FullName)\scripts\ov-credentials.mjs" sync-mcp "$($PluginCache.FullName)\.mcp.json"
   Get-Content -Raw "$($PluginCache.FullName)\.mcp.json"
   ```

   A rendered authenticated config looks like:

   ```json
   {
     "mcpServers": {
       "openviking-memory": {
         "url": "http://localhost:1933/mcp",
         "bearer_token_env_var": "OPENVIKING_API_KEY",
         "env_http_headers": {
           "X-OpenViking-Account": "OPENVIKING_ACCOUNT",
           "X-OpenViking-User": "OPENVIKING_USER"
         }
       }
     }
   }
   ```

   Keep the API key in `ovcli.conf` or the environment; do not write the
   secret value directly into `.mcp.json`. Restart Codex after changing MCP
   config or environment variables.

   You can also ask Codex to repair this on the user's machine with this
   prompt:

   ```text
   Fix my installed OpenViking Memory Codex plugin MCP config.
   Locate the newest openviking-memory plugin cache under CODEX_HOME, or ~/.codex if unset
   (Windows: %CODEX_HOME%, or %USERPROFILE%\.codex if unset). Use ~/.openviking/ovcli.conf
   to run scripts/ov-credentials.mjs sync-mcp against the cached .mcp.json. Do not write
   the API key value into .mcp.json; show the redacted result and remind me to restart Codex.
   ```

## Configuration

Connection / identity source (applies to hooks, MCP, and `ov` commands run inside Codex):

1. **Default**: active `ovcli.conf` wins when present: `OPENVIKING_CLI_CONFIG_FILE` or `~/.openviking/ovcli.conf`. Use `ov config switch <name>` to change the active credentials for the CLI, hooks, MCP, and child `ov` commands together.
2. **Env-forced**: set `OPENVIKING_CREDENTIAL_SOURCE=env` to force `OPENVIKING_URL` / `OPENVIKING_BASE_URL`, `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_PEER_ID`.
3. **Fallback**: without an ovcli config, env vars are used; then `ov.conf` (`server.url` / `server.root_api_key` plus legacy `codex.*` tuning); then `http://127.0.0.1:1933` unauthenticated.

The shell wrapper promotes the resolved ovcli credentials into env vars before exec'ing Codex because Codex's MCP runtime can only read auth from env. Hooks call the same resolver directly. The wrapper also exports `OPENVIKING_CLI_CONFIG_FILE`, so an `ov ...` command run inside Codex uses the same active config.
When credentials are forced from env, the wrapper materializes a mode-0600 runtime ovcli config under `~/.openviking/codex-plugin-state/` and points `OPENVIKING_CLI_CONFIG_FILE` at it, so child `ov` commands still use the same credentials as hooks and MCP.

Auth is sent as `Authorization: Bearer <api_key>` to both the REST API (used by hooks) and the `/mcp` endpoint (used by the model).

Set `actor_peer_id` in `ovcli.conf` (or `OPENVIKING_PEER_ID` with `OPENVIKING_CREDENTIAL_SOURCE=env`) when multiple Codex peers share the same OpenViking user and should keep separate peer memory. Hooks pass it as `peer_id` for captured session messages and as `X-OpenViking-Actor-Peer` for retrieval/filesystem calls; MCP gets the same header mapping. The legacy `codex.peerId` / `codex.peer_id` fields in `ov.conf` still resolve as a fallback.

For **unauthenticated local OV** (`ovcli.conf` without `api_key`, or no ovcli.conf at all), `.mcp.json` is rendered *without* `bearer_token_env_var`. Codex 0.130 hard-fails MCP startup with `Environment variable ... is empty` if `bearer_token_env_var` points at an empty/unset env var, so it must be omitted entirely when there's no key.

The `codex()` shell-function wrapper **re-renders this field on every codex launch** based on the currently-active `ovcli.conf` (the one `OPENVIKING_CLI_CONFIG_FILE` points at, falling back to `~/.openviking/ovcli.conf`). That means you can switch between authenticated and unauthenticated OV — e.g. to isolate a benchmark run from production memory — with `ov config switch <name>` or by changing `OPENVIKING_CLI_CONFIG_FILE` before invoking `codex`, with no re-install needed. The wrapper omits empty env-var assignments and strips stale credential env vars first, so `OPENVIKING_API_KEY=` or an inherited key for another user is never accidentally passed to Codex.

**Wrapping extra launch commands.** If you start Codex through a different command — a custom wrapper like `codex-custom`, or a multi-word launcher (a base command plus a sub-command) — the installer can wrap those too. Answer its "Extra launch commands" prompt, or pass `OPENVIKING_CODEX_WRAP_EXTRA='codex-custom'` when running it (the env-var form is also what to use when piping the installer through a non-interactive shell). The list is stored in the same rc marker block (read by the wrapper as `$OPENVIKING_CODEX_WRAP_EXTRA`); for a multi-word entry, only invocations whose leading args match the sub-command get the credential injection + `.mcp.json` re-render, so every *other* use of that command passes through untouched. List the *real* launch command, never a shell alias of it: an alias expands to its target before the wrapper runs, so wrap what it points at — `alias cx=codex` already rides the base `codex` wrapper (add nothing), while `alias cx=codex-custom` is covered by listing `codex-custom`. Alias names are skipped if listed.

### Tuning the plugin

All plugin behavior is controlled by `OPENVIKING_*` environment variables — set them in your shell rc (`~/.zshrc` / `~/.bashrc`) so every `codex` launch picks them up. The shell-function wrapper installed alongside the plugin already exports identity vars from `ovcli.conf`; tuning vars sit next to it.

```sh
# ~/.zshrc — examples
export OPENVIKING_RECALL_LIMIT=6
export OPENVIKING_RECALL_COMPRESS=1
export OPENVIKING_RECALL_COMPRESS_MODEL=gpt-5.3-codex-spark
export OPENVIKING_RECALL_COMPRESS_THINKING=default
export OPENVIKING_RECALL_TIMEOUT_MS=120000
export OPENVIKING_CAPTURE_ASSISTANT_TURNS=1
export OPENVIKING_AUTO_COMMIT_ON_COMPACT=1
export OPENVIKING_DEBUG=1
```

Full list: see the `Misc env vars` block in `scripts/config.mjs`. Tuning fields have `OPENVIKING_*` counterparts and env vars win for those tuning fields.

#### Legacy `codex` block in `ov.conf`

Earlier plugin versions configured tuning fields under a `codex` block in `~/.openviking/ov.conf`. That still works for backward compat — every env var above has a camelCase counterpart (`OPENVIKING_RECALL_LIMIT` → `codex.recallLimit`, etc.) — but **new deployments should prefer env vars**: this is the codex CLI's per-machine plugin tuning, and the server-side `ov.conf` is the wrong place for it. (It's read from `ov.conf`, not `ovcli.conf`, by historical accident in `scripts/config.mjs`.)

## Architecture

```
   ┌──────────────────────────────────────────────────────────────┐
   │                            Codex                             │
   └──┬─────────────────┬────────────────┬───────────────────┬────┘
      │                 │                │                   │
 SessionStart      UserPromptSubmit    Stop              PreCompact
 (startup|clear|resume) │              (per turn)            │
      │                 │                │                   │
 ┌────▼──────────┐ ┌────▼──────┐ ┌──────▼──────┐ ┌──────────▼──────┐
 │ session-start │ │ auto-     │ │ auto-       │ │ pre-compact-    │
 │ -commit.mjs   │ │ recall.mjs│ │ capture.mjs │ │ capture.mjs     │
 │ (active-win   │ │ (search + │ │ (append +   │ │ (commit + reset │
 │ heuristic +   │ │ compress) │ │ no commit)  │ │ ovSessionId)    │
 │ idle TTL +    │ │           │ │             │ │                 │
 │ resume inject)│ │           │ │             │ │                 │
 └────┬──────────┘ └────┬──────┘ └──────┬──────┘ └──────────┬──────┘
      │                 │                │                   │
      │             ┌───▼────────────────▼───────────────────▼──┐
      └────────────►│        OpenViking REST API                │
                    │ /api/v1/search/search                      │
                    │ /api/v1/sessions [+/{id}/{messages,commit}]│
                    │ /api/v1/content/read                      │
                    └─────────────────┬─────────────────────────┘
                                      │
   Codex ◄──────── streamable-HTTP MCP ◄ /mcp (search, store, read, list,
                   (bearer token via       grep, glob, forget,
                    OPENVIKING_API_KEY)    add_resource, health)
```

The plugin no longer bundles a local stdio MCP server. Codex talks to OpenViking's built-in `/mcp` endpoint directly via streamable HTTP. The checked-in `.mcp.json` ships only the local-default `url` (no `bearer_token_env_var`), so a `codex plugin marketplace add` install connects to an unauthenticated local OV out of the box. For an authenticated server, the installer/wrapper adds `bearer_token_env_var: "OPENVIKING_API_KEY"` to the cached copy when an API key is configured (and `codex plugin add` users add it by hand) — the key itself stays in `ovcli.conf` / the env, never on disk in `.mcp.json`.

For details on OpenViking's MCP endpoint, tools, and protocol, see the [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md). The tools list and per-tool semantics are documented there once, not duplicated here.

## How It Works

> See [`DESIGN.md`](./DESIGN.md) for the commit decision tree — it's the source of truth for *which* OpenViking session is sealed by *which* hook event.

### SessionStart commit logic (source=startup|clear, heuristic + idle TTL)

Codex fires `SessionStart` with one of three `source` values: `startup` (fresh process / `/new` / zouk daemon spawn-without-sessionId), `resume` (`/resume` or short reconnect), and `clear` (`/clear` — the previous transcript is orphaned and a new session_id is created). `resume` never commits or sweeps; on `startup` and `clear` we run the same active-window heuristic.

`hooks.json` registers `SessionStart` with `matcher: "clear|startup|resume"` so codex's dispatcher invokes the script on all three relevant sources. `session-start-commit.mjs` gates internally so only `startup` and `clear` commit/sweep.

On `startup` or `clear`, the script:

1. Counts state files (excluding the new session_id) whose `lastUpdatedAt` is within `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` (default 2 min) of "now":
   - **0 active** → no-op (no orphan to commit)
   - **1 active** → commit it (the just-ended session)
   - **≥2 active** → skip; rely on idle TTL (we can't tell which one ended)
2. **Idle-TTL sweep at the tail**: any state file (regardless of session_id) older than `OPENVIKING_CODEX_IDLE_TTL_MS` (default 30 min) gets committed and cleared.

On any /commit failure (OV unreachable, non-2xx, timeout) we **preserve state** (don't `clearState`) so the next sweep can retry.

On `resume`, the script skips commit/sweep. If local state has no live `ovSessionId`, it reads `/api/v1/sessions/{cx-session-id}/context` and injects the latest committed archive overview. The injected block includes a `viking://user/sessions/{cx-session-id}/history/` URI and tells the model to use the OpenViking MCP `read`/`search` tools for exact prior commands, file paths, tool outputs, or messages. Set `OPENVIKING_RESUME_ARCHIVE_INJECT=0` to disable this.

### Auto-recall (every UserPromptSubmit)

`auto-recall.mjs` reads `prompt` and `session_id` from stdin, derives the long-lived OpenViking session id (`cx-<safe-session-id>`) directly from the Codex session id (no plugin state read, so a corrupt state file can't crash recall), calls `/api/v1/search/search` with that `session_id`, ranks results, reads full content for top-ranked leaves, and emits:

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<openviking-context source=\"auto-recall\" format=\"digest\">\nOpenViking memory digest:\n- ...\n</openviking-context>" } }
```

Codex injects `additionalContext` into the model turn, so memories arrive without an extra tool call. By default the hook runs a Codex compression pass over recalled candidates before injection, dropping weakly-related memories and preserving only a short digest. If the compressor returns `NO_RELEVANT_MEMORY`, empty text, or non-digest chatter, the hook emits `{}` and injects nothing. The whole hook has its own `OPENVIKING_RECALL_TIMEOUT_MS` deadline (default 120s); the bundled `hooks.json` gives Codex 130s so the script can return `{}` before Codex kills it. Digests may keep `viking://` source URIs and point the model at the OpenViking MCP `read`/`search` tools for details when the inline bullet is intentionally short. The outer `<openviking-context ...>` wrapper is deterministic, not compressor-generated; capture strips it to distinguish recalled context from the user's prompt. Set `OPENVIKING_RECALL_COMPRESS=0` to fall back to deterministic short formatting.

The compressor profile is recreated on every `SessionStart` and cached under `OPENVIKING_CODEX_STATE_DIR` so cross-session config changes are picked up but each `UserPromptSubmit` does not probe models. Default fallback order:

1. configured `OPENVIKING_RECALL_COMPRESS_MODEL` + `OPENVIKING_RECALL_COMPRESS_THINKING`
2. `gpt-5.3-codex-spark` with thinking `default`
3. `gpt-5.5` with thinking `low`
4. off (deterministic digest, no `codex exec` compression)

Config knobs:

| Env var | Default | Meaning |
|---|---|---|
| `OPENVIKING_RECALL_COMPRESS` | `1` | Set `0` / `off` to disable `codex exec` compression. |
| `OPENVIKING_RECALL_COMPRESS_MODEL` | unset | Custom first-choice compressor model. Set `off` to disable compression. |
| `OPENVIKING_RECALL_COMPRESS_THINKING` | unset | Custom `model_reasoning_effort`; `default` omits the Codex config override. Alias: `OPENVIKING_RECALL_COMPRESS_REASONING_EFFORT`. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP` | `1` | Recreate/cache compressor profile in `SessionStart`. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TIMEOUT_MS` | `15000` | Per-candidate startup probe timeout. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TTL_MS` | `604800000` | Cache TTL used by `UserPromptSubmit` when reading the latest profile. |

### Stop (turn end → `add_message`, NOT `commit`)

`auto-capture.mjs` derives one long-lived OpenViking session id per Codex `session_id` as `cx-<safe-session-id>` and incrementally appends every new user/assistant turn via `/api/v1/sessions/{id}/messages`. The `/messages` endpoint auto-creates the session on first append. Per-codex-session state lives at `~/.openviking/codex-plugin-state/<safe-session-id>.json`. No `/commit` per turn — that would over-fragment memory extraction. Capture sanitizes obvious hook noise, metadata wrappers, and plugin-injected `<openviking-context ...>` blocks before append; tool calls/results are retained as compact `[tool-call ...]` / `[tool-result ...]` lines capped by `OPENVIKING_CAPTURE_TOOL_MAX_CHARS` (default 2000).

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
| `UserPromptSubmit` | `prompt`, `session_id`                     | `hookSpecificOutput.additionalContext` |
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
│                                  (uses Codex's native ${PLUGIN_ROOT} token; no
│                                   rendering needed on modern Codex)
├── scripts/
│   ├── config.mjs               # Shared config loader (ovcli.conf + env)
│   ├── capture-utils.mjs        # Transcript text extraction, filtering, tool compression
│   ├── debug-log.mjs            # Structured JSONL logger
│   ├── recall-compressor-profile.mjs # Compressor profile detection/cache
│   ├── session-state.mjs        # Per-codex-session OV session state
│   ├── auto-recall.mjs          # UserPromptSubmit hook (REST /search/search)
│   ├── auto-capture.mjs         # Stop hook (REST /sessions/{id}/messages)
│   ├── session-start-commit.mjs # SessionStart hook (active-window + idle TTL)
│   └── pre-compact-capture.mjs  # PreCompact hook
├── setup-helper/
│   └── install.sh               # One-line installer
├── .mcp.json                    # Streamable-HTTP MCP wiring (local-default url; installer adds auth)
├── DESIGN.md
├── VERIFICATION.md
└── README.md
```

No `src/`, `servers/`, `node_modules/`, or `package.json`: there is no local MCP server to build or run. All hook scripts are zero-dep `.mjs` running on Codex's bundled Node 22.

The Codex marketplace catalog that exposes this plugin for `codex plugin marketplace add` lives at the **repo root** in `.agents/plugins/marketplace.json` (Codex resolves a marketplace manifest from the source root, not from this subdirectory). The catalog points at `./examples/codex-memory-plugin` using a relative source, so the installed plugin follows the same marketplace snapshot/ref that the user added.

## Differences from the Claude Code Plugin

| Aspect | Claude Code Plugin | Codex Plugin |
|--------|--------------------|--------------|
| Plugin root env var | `CLAUDE_PLUGIN_ROOT` (expanded by CC) | `${PLUGIN_ROOT}` (injected into hook env + substituted inline by modern Codex; installer also renders it to absolute paths for older Codex) |
| `UserPromptSubmit` injection | `decision: "approve"` + `hookSpecificOutput.additionalContext` | `hookSpecificOutput.additionalContext` only — `approve` is not a Codex output |
| `Stop` decision | `decision: "approve"` no-op | `{}` no-op — only `block` is a valid Codex `decision` |
| Compaction hook | n/a (Claude Code does not expose one) | `PreCompact` — full-transcript commit before context loss |
| Config section | `claude_code` | `codex` |
| Default config file | `~/.openviking/ov.conf` | `~/.openviking/ovcli.conf`, falls back to `ov.conf` |
| MCP server | Local stdio (CC quirk: `.mcp.json` doesn't support env var auth) | Streamable HTTP to OpenViking's native `/mcp` (Codex supports `bearer_token_env_var`) |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
