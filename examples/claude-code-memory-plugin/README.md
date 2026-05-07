# OpenViking Memory Plugin for Claude Code

Long-term semantic memory for Claude Code, powered by [OpenViking](https://github.com/volcengine/OpenViking). Recall happens automatically before every prompt, capture happens automatically after every turn — no MCP tool calls required from the model.

> A public Claude Code plugin marketplace listing is planned but not yet published. For now, install from local source (see below).

## Quick Start

### One-line installer (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

macOS / Linux only. The script verifies dependencies, asks whether you'll connect to a **self-hosted** server or to **Volcengine OpenViking Cloud** (`https://api.vikingdb.cn-beijing.volces.com/openviking`), sets up `~/.openviking/ovcli.conf` (prompts only if absent), clones the repo to `~/.openviking/openviking-repo`, adds the `claude` shell-function wrapper to your rc, and runs `claude plugin install`. Re-running is safe.

If you'd rather do it by hand, follow the four steps below.

### Manual setup

#### 1. Have an OpenViking server reachable

Either run one locally or point at a remote one. The [quickstart guide](../../docs/en/getting-started/02-quickstart.md) walks through both options, including how to issue API keys for remote use. Default port is `1933`; local mode runs without authentication.

Verify it's up:

```bash
curl http://localhost:1933/health   # or your remote URL
```

#### 2. Tell the plugin where the server is

Easiest path — write `~/.openviking/ovcli.conf` (the same file `ov` CLI uses):

```json
{
  "url": "https://your-openviking-server.example.com",
  "api_key": "<your-api-key>",
  "account": "my-team",
  "user": "alice",
  "agent_id": "claude-code"
}
```

For purely local mode (`http://127.0.0.1:1933` with no auth) you can skip this step entirely — the plugin will silently use the local default.

If `ov.conf` is what you already maintain, the plugin reads it too — see [Configuration](#configuration) for the full priority chain and per-field overrides.

#### 3. Install the plugin

The repo's `examples/.claude-plugin/marketplace.json` exposes the plugin as a local marketplace entry. From the OpenViking repo root:

```bash
claude plugin marketplace add "$(pwd)/examples"
claude plugin install claude-code-memory-plugin@openviking-plugins-local
```

> Both commands install at user scope by default — the plugin is active from any directory. We don't pass `--scope user` explicitly because older Claude Code 2.0.x builds (e.g. 2.0.76) reject the flag. On newer builds that do accept `--scope`, you can lift a local-scoped install to user scope with `claude plugin enable claude-code-memory-plugin@openviking-plugins-local --scope user`.
>
> The marketplace entry points Claude Code at the source directory. Edits to `scripts/`, `hooks/`, and config files take effect on the next hook invocation — no reinstall. But moving / renaming / deleting the source dir, or `git checkout`-ing to a branch without these files, breaks the plugin. A public marketplace listing for one-click install will follow.

##### Legacy mode (Claude Code < 2.0)

`claude plugin` ships in Claude Code 2.0+ (Oct 2025). Older builds still have `claude mcp add` and the hooks system, so the same functionality can be wired up by hand:

```bash
PLUGIN_DIR="$(pwd)/examples/claude-code-memory-plugin"

claude mcp remove openviking -s user 2>/dev/null
claude mcp add --scope user --transport http openviking \
  '${OPENVIKING_URL:-http://127.0.0.1:1933}/mcp' \
  --header 'Authorization: Bearer ${OPENVIKING_API_KEY:-}' \
  --header 'X-OpenViking-Account: ${OPENVIKING_ACCOUNT:-}' \
  --header 'X-OpenViking-User: ${OPENVIKING_USER:-}' \
  --header 'X-OpenViking-Agent: ${OPENVIKING_AGENT_ID:-}'

# Merge plugin hooks into ~/.claude/settings.json (with backup).
mkdir -p ~/.claude && [ -f ~/.claude/settings.json ] || echo '{}' > ~/.claude/settings.json
cp -p ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%s)
sed "s|\${CLAUDE_PLUGIN_ROOT}|$PLUGIN_DIR|g" "$PLUGIN_DIR/hooks/hooks.json" > /tmp/ov-hooks.json
jq --slurpfile h /tmp/ov-hooks.json '.hooks = ((.hooks // {}) * $h[0].hooks)' \
  ~/.claude/settings.json > /tmp/ov-settings.json
jq -e . /tmp/ov-settings.json >/dev/null && mv /tmp/ov-settings.json ~/.claude/settings.json
rm -f /tmp/ov-hooks.json
```

The single-quoted `${VAR}` literals in `claude mcp add` are intentional — Claude Code expands them at MCP launch time using whatever the shell wrapper injects. Don't switch to double quotes; your shell would expand them to empty strings before the command runs. The one-line installer does all of this for you and prompts before touching `~/.claude/settings.json`.

#### 4. Start Claude Code

```bash
claude
```

If it doesn't seem to fire, set `OPENVIKING_DEBUG=1` and check `~/.openviking/logs/cc-hooks.log`.

## Configuring MCP

The plugin's hooks read `ovcli.conf` / `ov.conf` automatically. The bundled **MCP server entry does not** — Claude Code parses `.mcp.json` itself and supports **only `${VAR}` substitution**, so the plugin can't transparently feed config-file values into the MCP server URL or auth headers.

**Decision tree — do you need to do anything?**

```
Where is your OpenViking server?
├─ Local (127.0.0.1, no auth)
│    └─ ✅ Nothing to do — the bundled .mcp.json already works.
└─ Remote
     └─ ✅ Add the function-wrapper below to your shell rc.
```

**Recommended path — wrap `claude` to inject env from `ovcli.conf` on each invocation:**

```bash
# ~/.zshrc or ~/.bashrc
claude() {
  local _ov_conf="${OPENVIKING_CLI_CONFIG_FILE:-$HOME/.openviking/ovcli.conf}"
  if [ -f "$_ov_conf" ] && command -v jq >/dev/null 2>&1; then
    local _ov_url _ov_key
    _ov_url=$(jq -r '.url // empty'     "$_ov_conf" 2>/dev/null)
    _ov_key=$(jq -r '.api_key // empty' "$_ov_conf" 2>/dev/null)
    OPENVIKING_URL="${OPENVIKING_URL:-$_ov_url}" \
    OPENVIKING_API_KEY="${OPENVIKING_API_KEY:-$_ov_key}" \
      command claude "$@"
  else
    command claude "$@"
  fi
}
```

Re-source your rc (`source ~/.zshrc`, or `source ~/.bashrc` on bash) and restart `claude` — `/mcp` should then show your remote URL with valid auth.

> **Why a function instead of `export`?** A globally exported API key leaks into every child process spawned from your shell — npm scripts, build tools, crash dumps, `/proc/<pid>/environ`. The function wrapper limits the secret to the `claude` process tree only.
>
> Don't have `ovcli.conf` yet? See the [Deployment Guide → CLI](../../docs/en/guides/03-deployment.md#cli) to set one up.

**Other options if the function wrapper isn't viable:**

- **Edit the plugin's `.mcp.json` directly** with hardcoded values. Future plugin updates may overwrite it.
- **Add a separate MCP entry** to your project `.mcp.json` or `~/.claude.json`. See the [MCP integration guide](../../docs/en/guides/06-mcp-integration.md).

**Symptom of misconfiguration**: hooks (auto-recall, auto-capture) work fine because they read config files via Node, but the on-demand MCP tools (`search`, `read`, `store`, …) silently connect to `http://127.0.0.1:1933` with empty auth headers, and `/mcp` shows the wrong URL.

## Configuration

### Resolution priority

Every plugin field follows this chain (highest → lowest):

1. **Environment variables** (`OPENVIKING_*` — see tables below)
2. **`ovcli.conf`** — CLI client config (`~/.openviking/ovcli.conf` or `OPENVIKING_CLI_CONFIG_FILE`); only carries connection fields (`url`, `api_key`, `account`, `user`, `agent_id`)
3. **`ov.conf`** — server config (`~/.openviking/ov.conf` or `OPENVIKING_CONFIG_FILE`); the plugin reads `server.url`, `server.root_api_key`, and a legacy `claude_code` block if present (see [Legacy `claude_code` block](#legacy-claude_code-block-in-ovconf))
4. **Built-in defaults** (`http://127.0.0.1:1933`, no auth)

> ⚠️ **Hooks only.** This chain is implemented in `scripts/config.mjs` and consumed by hook scripts. It does **not** apply to MCP server registration — see [Configuring MCP](#configuring-mcp).

### Environment variables

All plugin behavior can be set via env vars. Connection / identity vars affect both hooks and (when exported in your shell rc) the MCP server; tuning vars only affect hooks.

#### Connection / identity

| Env Var                                          | Description                                                              |
|--------------------------------------------------|--------------------------------------------------------------------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL`         | Full server URL (e.g. `https://remote.example.com`)                      |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | API key; sent as `Authorization: Bearer <key>`                           |
| `OPENVIKING_ACCOUNT`                             | Multi-tenant account (`X-OpenViking-Account` header)                     |
| `OPENVIKING_USER`                                | Multi-tenant user (`X-OpenViking-User` header)                           |
| `OPENVIKING_AGENT_ID`                            | Agent identity, default `claude-code` (`X-OpenViking-Agent` header)      |

#### Recall tuning

| Env Var                                | Default      | Description                                                              |
|----------------------------------------|--------------|--------------------------------------------------------------------------|
| `OPENVIKING_AUTO_RECALL`               | `true`       | Enable auto-recall on every user prompt                                  |
| `OPENVIKING_RECALL_LIMIT`              | `6`          | Max memories to inject per turn                                          |
| `OPENVIKING_RECALL_TOKEN_BUDGET`       | `2000`       | Token budget for inline content; over-budget items degrade to URI hints  |
| `OPENVIKING_RECALL_MAX_CONTENT_CHARS`  | `500`        | Per-item content cap                                                     |
| `OPENVIKING_RECALL_PREFER_ABSTRACT`    | `true`       | Prefer abstract over full body when available                            |
| `OPENVIKING_SCORE_THRESHOLD`           | `0.35`       | Min relevance score (0–1)                                                |
| `OPENVIKING_MIN_QUERY_LENGTH`          | `3`          | Skip recall for very short queries                                       |
| `OPENVIKING_LOG_RANKING_DETAILS`       | `false`      | Per-candidate scoring logs (verbose)                                     |

#### Capture tuning

| Env Var                                | Default      | Description                                                              |
|----------------------------------------|--------------|--------------------------------------------------------------------------|
| `OPENVIKING_AUTO_CAPTURE`              | `true`       | Enable auto-capture; also gates write hooks (PreCompact / SessionEnd / SubagentStop) |
| `OPENVIKING_CAPTURE_MODE`              | `semantic`   | `semantic` (always capture) or `keyword` (trigger-based)                 |
| `OPENVIKING_CAPTURE_MAX_LENGTH`        | `24000`      | Max sanitized text length for the capture decision                       |
| `OPENVIKING_CAPTURE_ASSISTANT_TURNS`   | `true`       | Include assistant turns (text + tool I/O). Set to `0` for user-only.     |
| `OPENVIKING_COMMIT_TOKEN_THRESHOLD`    | `20000`      | Pending-token threshold for client-driven commit                         |
| `OPENVIKING_RESUME_CONTEXT_BUDGET`     | `32000`      | Token budget when fetching archive overview on session resume            |

#### Lifecycle / behavior / misc

| Env Var                                | Default      | Description                                                              |
|----------------------------------------|--------------|--------------------------------------------------------------------------|
| `OPENVIKING_TIMEOUT_MS`                | `15000`      | HTTP timeout for recall + general requests (ms)                          |
| `OPENVIKING_CAPTURE_TIMEOUT_MS`        | `30000`      | HTTP timeout for capture path (must stay under the `Stop` hook timeout)  |
| `OPENVIKING_WRITE_PATH_ASYNC`          | `true`       | Detach write hooks into a background worker so CC isn't blocked on commit RTT |
| `OPENVIKING_BYPASS_SESSION`            | `false`      | One-shot: `1`/`true` skips every hook in the current process             |
| `OPENVIKING_BYPASS_SESSION_PATTERNS`   | `""`         | CSV of glob patterns matched against `session_id` or `cwd`               |
| `OPENVIKING_MEMORY_ENABLED`            | (auto)       | `0`/`false`/`no`=force off; `1`/`true`/`yes`=force on                    |
| `OPENVIKING_DEBUG`                     | `false`      | `1`/`true`=write hook logs to `~/.openviking/logs/cc-hooks.log`          |
| `OPENVIKING_DEBUG_LOG`                 | `~/.openviking/logs/cc-hooks.log` | Override log path                                   |
| `OPENVIKING_CONFIG_FILE`               | `~/.openviking/ov.conf`           | Override `ov.conf` path                             |
| `OPENVIKING_CLI_CONFIG_FILE`           | `~/.openviking/ovcli.conf`        | Override `ovcli.conf` path                          |

Pure-env example (no config file required):

```bash
OPENVIKING_MEMORY_ENABLED=1 \
OPENVIKING_URL=https://openviking.example.com \
OPENVIKING_API_KEY=sk-xxx \
OPENVIKING_ACCOUNT=my-team \
OPENVIKING_USER=alice \
OPENVIKING_RECALL_LIMIT=8 \
claude
```

### Enable / disable

1. **`OPENVIKING_MEMORY_ENABLED` env var** — `0`/`false`/`no` forces off; `1`/`true`/`yes` forces on (when forced on without config files, connection info must come from env vars)
2. **`claude_code.enabled` in `ov.conf`** — `false` disables
3. **Config file existence** — enabled if `ov.conf` or `ovcli.conf` exists; otherwise silently disabled (no error, hooks pass through)

### Bypass a session

Use Claude Code in a `/tmp` PoC directory without polluting your long-term memory:

```bash
# Persistent: any session whose session_id or cwd matches a pattern
export OPENVIKING_BYPASS_SESSION_PATTERNS='/tmp/**,**/scratch/**,/Users/me/Dev/throwaway/*'

# Or one-shot:
OPENVIKING_BYPASS_SESSION=1 claude
```

When bypass is active, every hook approves immediately without contacting OpenViking.

### Legacy `claude_code` block in `ov.conf`

Earlier plugin versions configured tuning fields under a `claude_code` block in `~/.openviking/ov.conf`. That still works for backward compatibility — every env var above has a camelCase counterpart (`OPENVIKING_RECALL_LIMIT` → `claude_code.recallLimit`, `OPENVIKING_BYPASS_SESSION_PATTERNS` → `claude_code.bypassSessionPatterns` as a JSON array, etc.). Env vars take priority. New deployments should prefer env vars and shell rc — server config files shouldn't carry per-developer-machine tuning.

## Hook timeouts

Defaults in `hooks/hooks.json`:

| Hook                | Timeout | Notes                                                                                                  |
|---------------------|---------|--------------------------------------------------------------------------------------------------------|
| `SessionStart`      | `120s`  | Generous because resume/compact may pull a large archive overview                                      |
| `UserPromptSubmit`  | `8s`    | Auto-recall must stay fast so prompt submission never feels blocked                                    |
| `Stop`              | `45s`   | Auto-capture parses transcript + pushes turns; async detach makes the user-perceived time near-zero    |
| `PreCompact`        | `30s`   | Synchronous commit before Claude Code mutates the transcript                                           |
| `SessionEnd`        | `30s`   | Final commit; async-detached                                                                           |
| `SubagentStart`     | `10s`   | Lightweight: just persists isolation state                                                             |
| `SubagentStop`      | `45s`   | Reads subagent transcript and commits; async-detached                                                  |

Keep `claude_code.captureTimeoutMs` below the `Stop` timeout so the script can fail gracefully and still update its incremental state.

## Debug logging

Set `claude_code.debug: true` in `ov.conf` or `OPENVIKING_DEBUG=1` to write hook logs to `~/.openviking/logs/cc-hooks.log`.

- `auto-recall` logs key stages plus a compact `ranking_summary` by default.
- Set `claude_code.logRankingDetails: true` only when investigating per-candidate scoring; output is verbose.
- For deep diagnosis, run the standalone scripts `scripts/debug-recall.mjs` and `scripts/debug-capture.mjs` against a sample input rather than leaving the hook log on permanently.

## Troubleshooting

| Symptom                                    | Cause                                                        | Fix                                                                                                |
|--------------------------------------------|--------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| Plugin not activating                      | No `ov.conf` / `ovcli.conf` found                            | Create one, or set `OPENVIKING_MEMORY_ENABLED=1` plus the URL/API_KEY env vars                     |
| Hooks fire but recall is empty             | OpenViking server not running, or wrong URL                  | `curl http://localhost:1933/health` (or your remote URL)                                           |
| Auto-capture extracts 0 memories           | Wrong embedding/extraction model in `ov.conf`                | Check `embedding` / `vlm` config; review server logs                                               |
| MCP tools hit `127.0.0.1` instead of remote| `.mcp.json` only resolves `${VAR}`, no ovcli.conf integration | See [Configuring MCP](#configuring-mcp) — export env vars or edit `.mcp.json` |
| Remote auth 401 / 403                      | API key / account / user header mismatch                     | Verify `OPENVIKING_API_KEY`, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER` (or their `ov.conf` counterparts) |
| `Stop` hook times out                      | Server slow + sync write path                                | Leave `writePathAsync: true` (default), or raise the `Stop` timeout in `hooks/hooks.json`          |
| Old context keeps re-appearing in OV       | Pre-fix versions captured the recall block back into OV      | Update to current version — `auto-capture` now strips `<openviking-context>` before pushing        |
| Logs are noisy                             | `logRankingDetails: true` left on                            | Set `false`; use `debug-recall.mjs` / `debug-capture.mjs` for one-off inspection                   |

## Compared to Claude Code's built-in memory

Claude Code has a built-in `MEMORY.md` file system. This plugin **complements** it:

| Feature      | Built-in `MEMORY.md`              | OpenViking plugin                                  |
|--------------|-----------------------------------|----------------------------------------------------|
| Storage      | Flat markdown                     | Vector DB + structured extraction                  |
| Search       | Loaded into context wholesale     | Semantic similarity + ranking + token budget       |
| Scope        | Per-project                       | Cross-project, cross-session, cross-agent          |
| Capacity     | ~200 lines (context limit)        | Unlimited (server-side storage)                    |
| Extraction   | Manual rules                      | LLM-powered entity / preference / event extraction |
| Subagents    | Same as parent                    | Isolated session + typed agent namespace           |

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                      Claude Code                           │
│                                                            │
│  SessionStart   UserPromptSubmit   Stop   PreCompact       │
│  SessionEnd     SubagentStart      SubagentStop            │
└────┬───────────────┬───────────────┬───────────┬───────────┘
     │               │               │           │
     │   ┌───────────▼───────────┐   │           │
     │   │  hook scripts (.mjs)  │   │           │     ┌──────────────┐
     │   │  read transcript +    │───┼───────────┼────►│              │
     │   │  call OV HTTP API     │   │           │     │  OpenViking  │
     │   └───────────────────────┘   │           │     │  Server      │
     │                               │           │     │  (Python)    │
     │                  ┌────────────▼───────────▼───►│              │
     │                  │  MCP tools (HTTP /mcp)      │              │
     │                  │  search / read / store / …  │              │
     └─────────────────►│                             │              │
        OV session      └─────────────────────────────►              │
        context inject                                └──────────────┘
```

There is no bundled MCP server, no TypeScript build step, and no runtime npm bootstrap. Hooks are plain `.mjs` files that talk to OpenViking over HTTP; MCP comes from the OpenViking server's `/mcp` endpoint.

A persistent OpenViking session is created on first contact and reused for the entire Claude Code session. The OV session ID is `cc-<sha256(cc_session_id)>`, so resume / compact / multi-hook events all target the same session, and OV's `auto_commit_threshold` drives archival + memory extraction naturally.

### Hook responsibilities

| Hook                  | Trigger                                  | Action                                                                                            |
|-----------------------|------------------------------------------|---------------------------------------------------------------------------------------------------|
| `UserPromptSubmit`    | Each user turn                           | Search OV → rank → inject `<openviking-context>` block within a token budget                      |
| `Stop`                | Claude finishes a response               | Parse transcript → push new user turns to OV session → commit when pending tokens cross threshold |
| `SessionStart`        | New / resumed / post-compact session     | On `resume`/`compact`, fetch the latest archive overview and inject it as additional context      |
| `PreCompact`          | Before Claude Code rewrites the transcript | Commit pending messages so they become an archive before CC mutates the transcript                |
| `SessionEnd`          | Claude Code session closes               | Final commit so the last window is archived                                                       |
| `SubagentStart`       | Parent spawns a subagent via Task tool   | Derive an isolated OV session ID for the subagent, persist start state                            |
| `SubagentStop`        | Subagent finishes                        | Read subagent transcript → push to isolated session with subagent-typed agent header → commit     |

### Async write path

`Stop`, `SessionEnd`, and `SubagentStop` use a detached-worker pattern: the parent hook drains stdin, prints `{decision:"approve"}` to unblock Claude Code, then spawns a detached clone to do the HTTP work. The user never waits for OV. `PreCompact` stays synchronous because Claude Code mutates the transcript right after.

Disable with `claude_code.writePathAsync: false` if you need deterministic ordering during debugging.

### Memory pollution prevention

`auto-capture` strips `<openviking-context>`, `<system-reminder>`, `<relevant-memories>`, and `[Subagent Context]` blocks from each turn before pushing to OV. Without this, the recall context the plugin injects this turn would be captured back as part of the user's "message" next turn, creating a self-referential pollution loop.

### MCP tools available from the server

The plugin's `.mcp.json` connects to the OpenViking server's native HTTP MCP endpoint at `/mcp`. The server exposes 9 tools that Claude can call on demand:

| Tool           | Description                                                 |
|----------------|-------------------------------------------------------------|
| `search`       | Semantic search across memories, resources, and skills      |
| `read`         | Read one or more `viking://` URIs                           |
| `list`         | List entries under a `viking://` directory                  |
| `store`        | Store messages into long-term memory (triggers extraction)  |
| `add_resource` | Add a local file or URL as a resource                       |
| `grep`         | Regex content search across `viking://` files               |
| `glob`         | Find files matching a glob pattern                          |
| `forget`       | Delete any `viking://` URI                                  |
| `health`       | Check OpenViking server health                              |

See the [MCP integration guide](../../docs/en/guides/06-mcp-integration.md) for tool parameters.

### Plugin structure

```
claude-code-memory-plugin/
├── .claude-plugin/
│   └── plugin.json          # plugin manifest
├── hooks/
│   └── hooks.json           # 7 hook registrations
├── scripts/
│   ├── config.mjs           # shared config loader (env > ovcli.conf > ov.conf)
│   ├── debug-log.mjs        # log helper for ~/.openviking/logs/cc-hooks.log
│   ├── auto-recall.mjs      # UserPromptSubmit
│   ├── auto-capture.mjs     # Stop
│   ├── session-start.mjs    # SessionStart
│   ├── session-end.mjs      # SessionEnd
│   ├── pre-compact.mjs      # PreCompact
│   ├── subagent-start.mjs   # SubagentStart
│   ├── subagent-stop.mjs    # SubagentStop
│   ├── debug-recall.mjs     # standalone diagnostic for recall
│   ├── debug-capture.mjs    # standalone diagnostic for capture
│   └── lib/
│       ├── ov-session.mjs   # OV HTTP client + session helpers + bypass check
│       └── async-writer.mjs # detached-worker helper for write-path hooks
├── .mcp.json                # MCP server config (HTTP /mcp on OpenViking)
├── package.json             # type:module marker only — no runtime deps
└── README.md
```

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
