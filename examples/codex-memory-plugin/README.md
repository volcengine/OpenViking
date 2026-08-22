# OpenViking Memory Plugin for Codex and TraeCode CLI 2.0

Long-term semantic memory for [Codex](https://developers.openai.com/codex), powered by [OpenViking](https://github.com/volcengine/OpenViking).
TraeCode CLI 2.0 supports the same plugin format; use the shared installer's dedicated `--harness trae-cli` entry.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the
> caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less
> `viking://user/memories` shorthand is rejected by newer servers.

This is the Codex counterpart to [`claude-code-memory-plugin`](../claude-code-memory-plugin). It hooks Codex's lifecycle to:

- **Session-start profile injection** on `startup`, `clear`, and `resume`: load `profile.md` plus abstract-annotated indexes of `preferences/` and `entities/` through the shared CJK-aware profile builder.
- **Auto-recall** relevant memories on every `UserPromptSubmit` and inject them via `hookSpecificOutput.additionalContext`
- **Incremental capture on `Stop`** (turn end): append the new user/assistant turns to a deterministic OpenViking session id `cx-<codex_session_id>`. When `pending_tokens` reaches `OPENVIKING_COMMIT_TOKEN_THRESHOLD`, commit while keeping a recent live tail.
- **Commit on `PreCompact`**: trigger OpenViking's memory extractor on the full pre-compact transcript before Codex summarizes it.
- **Commit on `SessionStart` (source=startup|clear)**: active-window heuristic — if exactly one *other* state file was touched within the last 2 min, commit it (the just-ended session). On `≥2`, defer to idle-TTL sweep at the tail. `source=resume` never commits or sweeps; if the live OV session was already committed, it combines the profile block with the latest archive summary for continuity. See `DESIGN.md` for the full decision tree.

It also starts a local stdio MCP proxy that forwards to OpenViking's native `/mcp` endpoint with credentials resolved from env / `ovcli.conf`, so the model has direct access to the server's retrieval, memory, resource, watch, filesystem, and code-navigation tools.

## Quick Start

There are two install paths. **Pick one — don't mix them** (both surface the same `openviking-memory` plugin; enabling it from both would run the hooks twice). The **one-line installer (A)** is the recommended path for most users; the marketplace install (B) is useful when you already manage `~/.openviking/ovcli.conf` yourself.

### A. One-line installer — `curl | bash` (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness codex
```

For TraeCode CLI 2.0:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae-cli
```

Claude Code and Codex share this installer (drop `--harness codex` to pick interactively). It asks for your language (English/中文), the download source (GitHub, or a TOS mirror for GitHub-blocked regions — pass `--dist tos`; Codex on TOS installs from a TOS-hosted git repo and keeps remote updates), and your OpenViking credentials. It:

1. Checks `codex` and Node.js 18+ (the plugin itself wants Codex's bundled Node 22+ at runtime)
2. Sets up `~/.openviking/ovcli.conf` interactively
3. Registers the `openviking` marketplace — remote git by default (`codex plugin marketplace add https://github.com/volcengine/OpenViking.git`), or this checkout / a TOS archive in dev/archive mode — and enables `openviking-memory@openviking` with `features.plugin_hooks = true`
4. Keeps the checked-in stdio `.mcp.json` intact; `servers/mcp-proxy.mjs` reads your active `ovcli.conf` at runtime
5. Removes old OpenViking rc wrapper blocks and the pre-unification `openviking-plugins-local` marketplace when found
6. Runs plugin-list and stdio MCP validation

After install:

```bash
codex             # first run: review /hooks once
```

### B. Codex marketplace install

This path uses the same checked-in stdio MCP proxy as the installer path. Authenticated and remote/cloud servers work when `~/.openviking/ovcli.conf` or the relevant `OPENVIKING_*` env vars are present in Codex's environment.

The repo ships a Codex marketplace catalog at `.agents/plugins/marketplace.json`, so you can install with Codex's native commands:

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
> - **Codex version**: this path relies on Codex injecting and inline-substituting `${PLUGIN_ROOT}` in plugin hook commands (current Codex does both). On an older Codex that doesn't substitute `${PLUGIN_ROOT}`, the hook script paths won't resolve — use path **A**.
> - **Catalog source**: the catalog entry (`.agents/plugins/marketplace.json`) uses a relative source (`./examples/codex-memory-plugin`). `codex plugin add` therefore installs the plugin from the same marketplace snapshot/ref that you added. This keeps fork, branch, tag, and upstream-main installs reproducible and testable without rewriting the catalog.

This path works out of the box against an unauthenticated local OpenViking at `http://127.0.0.1:1933`. For remote/cloud servers, create `~/.openviking/ovcli.conf` with `url`, `api_key`, and optional `account` / `user`; the proxy reads it when Codex starts.

### Manual setup

If you don't want the installer touching your rc, do these things yourself:

1. **Write `ovcli.conf` once** so hooks and MCP share the same connection:

   ```json
   {
     "url": "https://your-openviking-server.example.com",
     "api_key": "<your-api-key>",
     "account": "my-team",
     "user": "alice"
   }
   ```

   Or run the bundled interactive wizard: `node scripts/setup.mjs` (from the plugin directory).

2. **Add the plugin** via the remote marketplace (path B above), or via a local directory marketplace: `codex plugin marketplace add <checkout>/examples` reads `examples/.agents/plugins/marketplace.json` and yields the same `openviking-memory@openviking` id. `hooks/hooks.json` needs no rendering on modern Codex: it uses the native `${PLUGIN_ROOT}` token, which Codex injects into the hook env and substitutes inline.

## Configuration

Connection / identity source (applies to hooks, MCP, and `ov` commands run inside Codex):

1. **Default (auto)**: env-var credentials (`OPENVIKING_URL` / `OPENVIKING_BASE_URL`, `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID`) win when any is set; otherwise the active `ovcli.conf` is used: `OPENVIKING_CLI_CONFIG_FILE` or `~/.openviking/ovcli.conf`. With no credential env vars set, `ov config switch <name>` changes the active credentials for the CLI, hooks, MCP, and child `ov` commands together.
2. **Forced**: set `OPENVIKING_CREDENTIAL_SOURCE=cli` to force `ovcli.conf`, or `OPENVIKING_CREDENTIAL_SOURCE=env` to force env-var credentials.
3. **Fallback**: without credential env vars or an ovcli config, `ov.conf` is used (`server.url` / `server.root_api_key` plus legacy `codex.*` tuning); then `http://127.0.0.1:1933` unauthenticated.

Hooks and the MCP proxy call the same resolver directly, so the model tools and lifecycle hooks follow the same target.

Auth is sent as `Authorization: Bearer <api_key>` to both the REST API (used by hooks) and the `/mcp` endpoint (used by the model); the hooks also send the same key as `X-API-Key` for compatibility with older servers.

By default the plugin derives a peer from the current workspace path using Claude's project-directory naming rule: every non-letter-or-digit character becomes `-`, with no path normalization. For example, `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking`. Hooks pass the effective peer as `peer_id` for captured session messages and as `X-OpenViking-Actor-Peer` for retrieval/filesystem calls; MCP gets the same header mapping.

Set `actor_peer_id` in `ovcli.conf` (or `OPENVIKING_PEER_ID` with `OPENVIKING_CREDENTIAL_SOURCE=env`) to override the workspace-derived peer. The legacy `codex.peerId` / `codex.peer_id` fields in `ov.conf` still resolve as a fallback. Set `OPENVIKING_WORKSPACE_PEER=0` or `codex.workspacePeer=false` to turn off workspace-derived peers.

Recall defaults to the broad mode: global memory, the current workspace, and other workspace memories can all be recalled, with other workspaces penalized and rendered later. Set `OPENVIKING_RECALL_PEER_SCOPE=actor` or `codex.recallPeerScope="actor"` for the isolation mode, which only sees global memory plus the current workspace. In deployments where one bot serves multiple real people, such as zouk, vikingbot, or AstrBot, use the isolation mode with an explicit actor peer so one person's memories are not recalled into another person's session.

The checked-in `.mcp.json` contains only a stdio command. It never stores server URLs, bearer-token env mappings, or identity headers, so switching `ovcli.conf` changes the MCP target on the next Codex launch without cache rendering.

### Tuning the plugin

All plugin behavior is controlled by `OPENVIKING_*` environment variables. Connection and identity should normally live in `ovcli.conf`; tuning vars can be exported in your shell rc when you want every Codex launch to pick them up.

```sh
# ~/.zshrc — examples
export OPENVIKING_RECALL_LIMIT=10
export OPENVIKING_RECALL_COMPRESS=1
export OPENVIKING_RECALL_COMPRESS_MODEL=gpt-5.3-codex-spark
export OPENVIKING_RECALL_COMPRESS_THINKING=default
export OPENVIKING_RECALL_COMPRESS_BASE_URL=https://api.example.com/v1
export OPENVIKING_RECALL_TIMEOUT_MS=120000
export OPENVIKING_CAPTURE_ASSISTANT_TURNS=1
export OPENVIKING_AUTO_COMMIT_ON_COMPACT=1
export OPENVIKING_PROFILE_TOKEN_BUDGET=10000
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
 │ (profile +    │ │ (search + │ │ (append +   │ │ (commit + reset │
 │ active-win +  │ │ compress) │ │ threshold   │ │ ovSessionId)    │
 │ idle TTL +    │ │           │ │             │ │                 │
 │ resume archive)││           │ │             │ │                 │
 └────┬──────────┘ └────┬──────┘ └──────┬──────┘ └──────────┬──────┘
      │                 │                │                   │
      │             ┌───▼────────────────▼───────────────────▼──┐
      └────────────►│        OpenViking REST API                │
                    │ /api/v1/search/{recall,search}             │
                    │ /api/v1/sessions [+/{id}/{messages,commit}]│
                    │ /api/v1/content/read                      │
                    └─────────────────┬─────────────────────────┘
                                      │
   Codex ◄── stdio MCP proxy ──► /mcp (find, search, read,
              (env/ovcli.conf)      remember, resources, watches,
                                  filesystem)
```

The checked-in `.mcp.json` starts `servers/mcp-proxy.mjs` with `node`. The proxy keeps stdout protocol-clean, reads the same credential sources as the hooks, sends auth and identity headers to `/mcp`, caches the server `mcp-session-id`, and transparently reinitializes once if the server restarts.

For details on OpenViking's MCP endpoint, tools, and protocol, see the [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md). The tools list and per-tool semantics are documented there once, not duplicated here.

## How It Works

> See [`DESIGN.md`](./DESIGN.md) for the commit decision tree — it's the source of truth for *which* OpenViking session is sealed by *which* hook event.

### SessionStart profile injection and commit logic

Codex fires `SessionStart` with one of three `source` values: `startup` (fresh process / `/new` / zouk daemon spawn-without-sessionId), `resume` (`/resume` or short reconnect), and `clear` (`/clear` — the previous transcript is orphaned and a new session_id is created). `resume` never commits or sweeps; on `startup` and `clear` we run the same active-window heuristic.

`hooks.json` registers `SessionStart` with `matcher: "clear|startup|resume"` so codex's dispatcher invokes the script on all three relevant sources. `session-start-commit.mjs` gates internally so only `startup` and `clear` commit/sweep.

On all three sources, the hook uses the same shared `buildProfileBlock()` implementation as the Claude Code, OpenCode, and pi integrations. It reads the user's `profile.md` and adds URI plus abstract indexes for `preferences/` and `entities/`, with a CJK-aware token budget. The default budget is `10000`; set `OPENVIKING_PROFILE_TOKEN_BUDGET` or `plugin.codex.profileTokenBudget` to change it. Set `OPENVIKING_NO_AUTO_INJECT=1` or `plugin.codex.noAutoInject=true` to disable only this fixed profile/background injection; per-prompt semantic recall remains controlled separately by `OPENVIKING_AUTO_RECALL`.

On `startup` or `clear`, the script:

1. Counts state files (excluding the new session_id) whose `lastUpdatedAt` is within `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` (default 2 min) of "now":
   - **0 active** → no-op (no orphan to commit)
   - **1 active** → commit it (the just-ended session), unless it has no live `ovSessionId` left to commit
   - **≥2 active** → skip; rely on idle TTL (we can't tell which one ended)
2. **Idle-TTL sweep at the tail**: any live session state older than `OPENVIKING_CODEX_IDLE_TTL_MS` (default 30 min) gets committed while preserving its transcript cursor for resume.
3. **Cursor retention in the same pass**: a state file with no live OV session is kept as a resume cursor until `OPENVIKING_CODEX_COMMITTED_TTL_MS` (default 30 days), or dropped after the idle TTL if it never captured a turn.

On any /commit failure (OV unreachable, non-2xx, timeout) we **preserve state** (keep `ovSessionId` set) so the next sweep can retry.

On `resume`, the script skips commit/sweep. It still injects the profile block. If local state has no live `ovSessionId`, it also reads `/api/v1/sessions/{cx-session-id}/context` and combines the latest committed archive overview into the same `SessionStart` output. The archive block includes a `viking://~/sessions/{cx-session-id}/history/` URI and tells the model to use the OpenViking MCP `read`/`search` tools for exact prior commands, file paths, tool outputs, or messages. Set `OPENVIKING_RESUME_ARCHIVE_INJECT=0` to disable the archive half without disabling profile injection.

### Auto-recall (every UserPromptSubmit)

`auto-recall.mjs` reads `prompt` and `session_id` from stdin. It first asks `/api/v1/search/recall` for bounded, type-quota candidates and passes those entries through the same relevance compressor used by the fallback path. If that endpoint is unavailable, the hook derives the long-lived OpenViking session id (`cx-<safe-session-id>`) directly from the Codex session id (no plugin state read, so a corrupt state file can't crash recall), calls `/api/v1/search/search` with that `session_id`, ranks results, and reads full content for top-ranked leaves before compression.

```json
{ "hookSpecificOutput": { "hookEventName": "UserPromptSubmit", "additionalContext": "<openviking-context source=\"auto-recall\" format=\"digest\">\nOpenViking memory digest:\n- ...\n</openviking-context>" } }
```

Codex injects `additionalContext` into the model turn, so memories arrive without an extra tool call. By default the hook runs a Codex compression pass over recalled candidates before injection, dropping weakly-related memories and preserving only a short digest. If the compressor returns `NO_RELEVANT_MEMORY`, empty text, or non-digest chatter, the hook emits `{}` and injects nothing. The whole hook has its own `OPENVIKING_RECALL_TIMEOUT_MS` deadline (default 120s); the bundled `hooks.json` gives Codex 130s so the script can return `{}` before Codex kills it. Digests may keep `viking://` source URIs and point the model at the OpenViking MCP `read`/`search` tools for details when the inline bullet is intentionally short. The outer `<openviking-context ...>` wrapper is deterministic, not compressor-generated; capture strips it to distinguish recalled context from the user's prompt. Set `OPENVIKING_RECALL_COMPRESS=0` to fall back to deterministic short formatting.

The compressor profile is recreated on every `SessionStart` and cached under `OPENVIKING_CODEX_STATE_DIR` so cross-session config changes are picked up but each `UserPromptSubmit` does not probe models. Default fallback order:

1. configured `OPENVIKING_RECALL_COMPRESS_MODEL` + `OPENVIKING_RECALL_COMPRESS_THINKING`
2. `gpt-5.3-codex-spark` with thinking `default`
3. `gpt-5.6-luna` with thinking `low`
4. off (deterministic digest, no `codex exec` compression)

Config knobs:

| Env var | Default | Meaning |
|---|---|---|
| `OPENVIKING_RECALL_LIMIT` | `10` | Legacy quota-scaling input; explicit values are converted to six coding quotas, not enforced as a final result cap. |
| `OPENVIKING_RECALL_COMPRESS` | `1` | Set `0` / `off` to disable `codex exec` compression. |
| `OPENVIKING_RECALL_COMPRESS_MODEL` | unset | Custom first-choice compressor model. Set `off` to disable compression. |
| `OPENVIKING_RECALL_COMPRESS_THINKING` | unset | Custom `model_reasoning_effort`; `default` omits the Codex config override. Alias: `OPENVIKING_RECALL_COMPRESS_REASONING_EFFORT`. |
| `OPENVIKING_RECALL_COMPRESS_BASE_URL` | unset | Base URL for the nested compressor's provider. Use this when `--ignore-user-config` prevents the compressor from reading the main Codex provider configuration. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP` | `1` | Recreate/cache compressor profile in `SessionStart`. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TIMEOUT_MS` | `15000` | Per-candidate startup probe timeout. |
| `OPENVIKING_RECALL_COMPRESS_DETECT_TTL_MS` | `604800000` | Cache TTL used by `UserPromptSubmit` when reading the latest profile. |
| `OPENVIKING_RECALL_MAX_TOKENS` | `1600` | Token budget the server assembles the context block within, independent of the local compressor input limit. |
| `OPENVIKING_RECALL_DEDUP_TURNS` | `5` | Cross-turn cooldown: URIs served in the last N turns are skipped. |
| `OPENVIKING_RECALL_QUERY_EXPANSION` | `auto` | `auto` lets the server widen short prompts using session context; `off` disables it. |

Recall now asks the server to assemble the context block in one request
(`POST /api/v1/search/search` with `mode="context"`), so budgeting, detail tiers
and cross-turn dedup are shared with every other harness. Deployments without
that endpoint fall back to `/api/v1/search/recall`, and that outcome is cached so
only the first turn pays for the probe. Server-owned Context defaults are omitted
unless explicitly configured, so the plugin follows the server instead of copying
values such as `limit=10` or `max_tokens=1600`. An explicit legacy `recallLimit`
is converted to per-category coding quotas, not a final result cap. Values
from 1 through 5 therefore produce an effective total quota of 6, one retrieval
slot for each coding domain. Local `codex exec` compression is
unchanged and still runs on top of whichever path answered.

Client-side knobs can also live in `~/.openviking/ovcli.conf` under
`plugin` (shared) or `plugin.codex` (this harness only); resolution order is env
vars → `plugin.codex` → `plugin` → the legacy `codex` block in `ov.conf` →
defaults.

### Stop (turn end → `add_message`, threshold commit)

`auto-capture.mjs` derives one long-lived OpenViking session id per Codex `session_id` as `cx-<safe-session-id>` and incrementally appends every new user/assistant turn via `/api/v1/sessions/{id}/messages`. The `/messages` endpoint auto-creates the session on first append. Per-codex-session state lives at `~/.openviking/codex-plugin-state/<safe-session-id>.json`. Capture sanitizes obvious hook noise, metadata wrappers, and plugin-injected `<openviking-context ...>` blocks before append. Tool calls and results become dedicated `tool` parts and `tool_output` is reported verbatim — the server externalizes anything larger than `tool_output_externalization.threshold_chars` (default `20000`) and leaves a synopsis stub plus `tool_output_ref`, so the original stays readable via `/api/v1/sessions/{id}/tool-results`. `OPENVIKING_CAPTURE_TOOL_MAX_CHARS` (default `1000000`) is only a guard against pathological payloads.

After a successful append, Stop reads the session meta and commits when `pending_tokens >= OPENVIKING_COMMIT_TOKEN_THRESHOLD` (default `20000`). Threshold commits pass `keep_recent_count=OPENVIKING_COMMIT_KEEP_RECENT_COUNT` (default `10`) so the newest turns remain live for continuity while older context is archived and extracted. `PreCompact` still commits everything before compaction.

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
| `SessionStart`   | `source` (`startup`/`resume`/`clear`), `session_id`, `cwd` | `hookSpecificOutput.additionalContext`; may also include `systemMessage` when an orphaned session was committed |
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
│   ├── auto-capture.mjs         # Stop hook (append + threshold commit)
│   ├── session-start-commit.mjs # SessionStart hook (profile + active-window + idle TTL + resume archive)
│   └── pre-compact-capture.mjs  # PreCompact hook
├── servers/
│   ├── mcp-proxy.mjs            # stdio -> OpenViking /mcp bridge
│   └── mcp-proxy.test.mjs       # proxy contract tests
├── setup-helper/
│   └── install.sh               # One-line installer
├── .mcp.json                    # stdio MCP wiring
├── DESIGN.md
├── VERIFICATION.md
└── README.md
```

No `src/` or `package.json`: there is no build step. Hook scripts and the MCP proxy are zero-dep `.mjs` files running on Codex's bundled Node 22 or a compatible system Node.

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
| MCP server | Local stdio proxy to OpenViking `/mcp` | Local stdio proxy to OpenViking `/mcp` |

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
