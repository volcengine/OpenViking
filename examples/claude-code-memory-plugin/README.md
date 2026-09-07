# OpenViking Memory Plugin for Claude Code

Long-term semantic memory for Claude Code, powered by [OpenViking](https://github.com/volcengine/OpenViking). Recall happens automatically before every prompt, capture happens automatically after every turn — no MCP tool calls required from the model.

> **Requires an OpenViking server with `viking://~` home-alias support.** Recall targets the
> caller's own context space through `viking://~/memories` and `viking://~/skills`; the uid-less
> `viking://user/memories` shorthand is rejected by newer servers.

> Installable straight from the repo's marketplace catalog — no separate distribution repo. See [Manual setup](#manual-setup) for the two-command remote install.

## Quick Start

### One-line installer (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness claude
```

macOS / Linux only. Claude Code and Codex share this installer (drop `--harness claude` to pick interactively): it asks for your language (English/中文), the download source (GitHub, or a TOS mirror for GitHub-blocked regions — pass `--dist tos`), and your OpenViking credentials, then installs `openviking-memory` via the remote marketplace. The stdio MCP proxy reads `ovcli.conf` at runtime, so no shell wrapper or `.mcp.json` rendering is needed. Re-running is safe.

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
  "user": "alice"
}
```

For purely local mode (`http://127.0.0.1:1933` with no auth) you can skip this step entirely — the plugin will silently use the local default.

If `ov.conf` is what you already maintain, the plugin reads it too — see [Configuration](#configuration) for the full priority chain and per-field overrides.

#### 3. Install the plugin

**Remote marketplace (recommended)** — no clone needed. The repo root ships a `.claude-plugin/marketplace.json` whose entry fetches this plugin via `git-subdir`:

```bash
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install openviking-memory@openviking
```

(`claude plugin marketplace add volcengine/OpenViking` works too, but clones the whole repo as the marketplace.)

If you skipped step 2, configure the connection afterwards: write `~/.openviking/ovcli.conf` by hand, run `node <plugin-dir>/scripts/setup.mjs` (an interactive wizard bundled with the plugin), or just run the one-line installer.

**Local directory (development)** — registers this checkout so edits to `scripts/` and `hooks/` take effect on the next hook invocation without reinstalling. From the OpenViking repo root:

```bash
claude plugin marketplace add "$(pwd)/examples"
claude plugin install openviking-memory@openviking
```

> Both commands install at user scope by default — the plugin is active from any directory. We don't pass `--scope user` explicitly because older Claude Code 2.0.x builds (e.g. 2.0.76) reject the flag. On newer builds that do accept `--scope`, you can lift a local-scoped install to user scope with `claude plugin enable openviking-memory@openviking --scope user`.
>
> Directory-mode caveat: moving / renaming / deleting the source dir, or `git checkout`-ing to a branch without these files, breaks the plugin. Both modes register a marketplace named `openviking`, so the plugin id is always `openviking-memory@openviking`; switch modes by removing the marketplace and re-adding the other source (the installer does this automatically).

##### Legacy mode (Claude Code < 2.0)

`claude plugin` ships in Claude Code 2.0+ (Oct 2025). Older builds still have `claude mcp add` and the hooks system, so the same functionality can be wired up by hand:

```bash
PLUGIN_DIR="$(pwd)/examples/claude-code-memory-plugin"

# stdio MCP proxy — reads ovcli.conf / OPENVIKING_* itself, no header wiring needed.
claude mcp remove openviking -s user 2>/dev/null
claude mcp add --scope user openviking -- node "$PLUGIN_DIR/servers/mcp-proxy.mjs"

# Merge plugin hooks into ~/.claude/settings.json (with backup).
mkdir -p ~/.claude && [ -f ~/.claude/settings.json ] || echo '{}' > ~/.claude/settings.json
cp -p ~/.claude/settings.json ~/.claude/settings.json.bak.$(date +%s)
sed "s|\${CLAUDE_PLUGIN_ROOT}|$PLUGIN_DIR|g" "$PLUGIN_DIR/hooks/hooks.json" > /tmp/ov-hooks.json
jq --slurpfile h /tmp/ov-hooks.json '.hooks = ((.hooks // {}) * $h[0].hooks)' \
  ~/.claude/settings.json > /tmp/ov-settings.json
jq -e . /tmp/ov-settings.json >/dev/null && mv /tmp/ov-settings.json ~/.claude/settings.json
rm -f /tmp/ov-hooks.json
```

The one-line installer automates exactly this when it detects a pre-2.0 build (it keeps a source checkout under `~/.openviking/openviking-repo` for the absolute paths above).

#### 4. Start Claude Code

```bash
claude
```

If it doesn't seem to fire, set `OPENVIKING_DEBUG=1` and check `~/.openviking/logs/cc-hooks.log`.

## Configuring MCP

The plugin's hooks and MCP entry now use the same configuration chain. The checked-in `.mcp.json` starts `servers/mcp-proxy.mjs` as a local stdio MCP server; that proxy reads `OPENVIKING_*`, `~/.openviking/ovcli.conf`, and `~/.openviking/ov.conf`, then forwards JSON-RPC to the OpenViking server's native `/mcp` endpoint with the right auth and identity headers.

For normal plugin installs, there is nothing extra to export and no `.mcp.json` value to render. Update `ovcli.conf` or the relevant `OPENVIKING_*` env vars and restart Claude Code; the proxy will use the same target as the hook scripts.

The proxy requires Node.js 18+ and writes debug logs only when `OPENVIKING_DEBUG=1` or `claude_code.debug=true` is configured. stdout is reserved for MCP protocol bytes.

## Configuration

### Resolution priority

Every plugin field follows this chain (highest → lowest):

1. **Environment variables** (`OPENVIKING_*` — see tables below)
2. **Workspace registry** — this machine's entry for the current repository, `~/.openviking/workspaces/<slot>.json`
3. **`<repo-root>/.openviking/config.local.json`** — private, gitignored workspace settings
4. **`<repo-root>/.openviking/config.json`** — workspace settings the team commits
5. **`ovcli.conf`** — CLI client config (`~/.openviking/ovcli.conf` or `OPENVIKING_CLI_CONFIG_FILE`); connection fields (`url`, `api_key`, `account`, `user`) plus the `plugin` section, `plugin.claude_code` ahead of the shared `plugin`
6. **`ov.conf`** — server config (`~/.openviking/ov.conf` or `OPENVIKING_CONFIG_FILE`); the plugin reads `server.url`, `server.root_api_key`, and a legacy `claude_code` block if present (see [Legacy `claude_code` block](#legacy-claude_code-block-in-ovconf))
7. **Built-in defaults** (`http://127.0.0.1:1933`, no auth)

The three workspace layers carry only the settings listed under [Workspace configuration files](#workspace-configuration-files); connection and credentials are never read from them.

The same connection and identity fields are also used by the stdio MCP proxy.

### Environment variables

All plugin behavior can be set via env vars. Connection / identity vars affect both hooks and the MCP proxy; tuning vars only affect hooks.

#### Connection / identity

| Env Var                                          | Description                                                              |
|--------------------------------------------------|--------------------------------------------------------------------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL`         | Full server URL (e.g. `https://remote.example.com`)                      |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | API key; sent as `Authorization: Bearer <key>`                           |
| `OPENVIKING_ACCOUNT`                             | Multi-tenant account (`X-OpenViking-Account` header)                     |
| `OPENVIKING_USER`                                | Multi-tenant user (`X-OpenViking-User` header)                           |
| `OPENVIKING_PEER_ID`                             | Optional stable peer for recall and captured session messages            |
| `OPENVIKING_PEER_SOURCE`                         | How the workspace peer is derived: `git` (default), `cwd`, `none`, or a template |
| `OPENVIKING_WORKSPACE_PEER`                      | Derive a peer from the current workspace by default; set `0` to disable  |

By default the plugin derives the peer from git rather than from where the repository happens to sit: the normalized `origin` URL, else the repository root path. Outside a repository nothing is sent, and what is remembered there goes to your user-level space at `viking://user/<you>/memories`. In `/Users/x/Dev/OpenViking` with origin `git@github.com:volcengine/OpenViking.git` the peer is `github.com-volcengine-openviking`, and it stays that from any subdirectory, worktree, machine or clone — so every clone of one repository shares one project memory, while a fork, having a different origin, stays separate. Data-plane recall/profile requests send the effective peer as `X-OpenViking-Actor-Peer`; captured session messages store it as body `peer_id`. `OPENVIKING_PEER_ID` overrides the derived value. Subagent capture uses the parent workspace peer when available, and falls back to Claude's `agent_id` only when no explicit or workspace peer exists.

`OPENVIKING_PEER_SOURCE` (or `plugin.peerSource` / `plugin.claude_code.peerSource` in `ovcli.conf`, or `peer.source` in a workspace config file) picks the rule:

| Value        | Meaning                                                                                                                     |
|--------------|-----------------------------------------------------------------------------------------------------------------------------|
| `git`        | Default. Same as `["{git_remote}", "{git_root}"]`: normalized origin, else repository root. Outside a repository nothing is sent. No prefix is added |
| `cwd`        | The previous behaviour, byte for byte — every non-letter-or-digit character becomes `-`, so `/Users/x/Dev/OpenViking` becomes `-Users-x-Dev-OpenViking` |
| `none`       | Send no peer at all; `OPENVIKING_WORKSPACE_PEER=0` still means this                                                          |
| a template   | `"git-{git_remote}"`, `"team-{dir}"`, or a list tried in order; a template with an empty variable falls through to the next   |

The variables are `{git_remote}`, `{git_root}`, `{cwd}` and `{dir}` — see [Workspace Peers](../memory-plugin-shared/README.md#workspace-peers) for what each resolves to. `{git_root}` is empty outside a repository; `{cwd}` is never empty but sits in no default chain, so a bare path becomes a peer only when you ask for one; `{dir}` is the workspace root's directory name — the repository root, or the directory holding `.openviking/config.json` — and is empty when the directory is not a workspace. Derivation is pure filesystem work, no `git` subprocess, so it also holds where `git` is missing from `PATH` or would refuse the repository over dubious ownership.

To give a directory that is not a repository its own peer, create `.openviking/config.json` there holding `{"version": 1, "peer": {"id": "my-project"}}`.

Upgrading from the path-derived peer needs no action: memories written under the old id stay reachable. With the default `peer_scope: "all"` the server's cross-peer sweep already covers them at no cost; with `actor` scope the plugin asks the old peer separately. There is no deadline, and `OPENVIKING_PEER_SOURCE=cwd` restores the old id outright.

#### Recall tuning

| Env Var                                | Default      | Description                                                              |
|----------------------------------------|--------------|--------------------------------------------------------------------------|
| `OPENVIKING_AUTO_RECALL`               | `true`       | Enable auto-recall on every user prompt                                  |
| `OPENVIKING_RECALL_LIMIT`              | `10`         | Legacy quota-scaling input; converted to six coding quotas, not a final cap |
| `OPENVIKING_RECALL_TOKEN_BUDGET`       | `2000`       | Inline token budget for the final raw-find fallback only                 |
| `OPENVIKING_RECALL_MAX_CONTENT_CHARS`  | `500`        | Per-item content cap                                                     |
| `OPENVIKING_RECALL_PREFER_ABSTRACT`    | `true`       | Prefer abstract over full body when available                            |
| `OPENVIKING_RECALL_PEER_SCOPE`          | `all`        | `all` can recall other project memories with a score penalty; `actor` only sees global plus the current project |
| `OPENVIKING_RECALL_MAX_TOKENS`         | `1600`       | Token budget for the server-assembled context block (independent of local compression limits) |
| `OPENVIKING_RECALL_DEDUP_TURNS`        | `5`          | Cross-turn cooldown: URIs served in the last N turns are skipped          |
| `OPENVIKING_RECALL_QUERY_EXPANSION`    | `auto`       | `auto` lets the server widen short prompts using session context; `off` disables it |
| `OPENVIKING_RECALL_COMPRESS`           | `auto`       | Digest compression: `off`, `client` (host CLI), `server`, or `auto` (local first, server fallback) |
| `OPENVIKING_RECALL_COMPRESS_MAX_BULLETS` | `6`        | Digest bullet ceiling                                                     |
| `OPENVIKING_SCORE_THRESHOLD`           | `0.35`       | Min relevance score (0–1)                                                |
| `OPENVIKING_MIN_QUERY_LENGTH`          | `3`          | Skip recall for very short queries                                       |

Recall defaults to the broad mode: global memory, the current workspace, and other workspace memories can all be recalled, with other workspaces penalized and rendered later. Set `OPENVIKING_RECALL_PEER_SCOPE=actor` for the isolation mode, which only sees global memory plus the current workspace. In deployments where one bot serves multiple real people, such as zouk, vikingbot, or AstrBot, use the isolation mode with an explicit actor peer so one person's memories are not recalled into another person's session.
| `OPENVIKING_LOG_RANKING_DETAILS`       | `false`      | Per-candidate scoring logs (verbose)                                     |

#### Capture tuning

| Env Var                                | Default      | Description                                                              |
|----------------------------------------|--------------|--------------------------------------------------------------------------|
| `OPENVIKING_AUTO_CAPTURE`              | `true`       | Enable auto-capture; also gates write hooks (PreCompact / SessionEnd / SubagentStop) |
| `OPENVIKING_CAPTURE_MODE`              | `semantic`   | `semantic` (always capture) or `keyword` (trigger-based)                 |
| `OPENVIKING_CAPTURE_MAX_LENGTH`        | `24000`      | Max sanitized text length for the capture decision                       |
| `OPENVIKING_CAPTURE_ASSISTANT_TURNS`   | `true`       | Include assistant turns (text + tool I/O). Set to `0` for user-only.     |
| `OPENVIKING_CAPTURE_TOOL_MAX_CHARS`    | `1000000`    | Guard cap on one tool part's `tool_output`; oversized output is externalized server-side |
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

### Plugin settings in `ovcli.conf`

Client-side tuning belongs in `~/.openviking/ovcli.conf` under a `plugin` section. Shared keys apply to every harness; a per-harness object overrides them:

```json
{
  "url": "http://127.0.0.1:1933",
  "plugin": {
    "recallCompress": "auto"
  }
}
```

Resolution order: env vars → the workspace layers → `plugin.claude_code` → `plugin` → the legacy `claude_code` block in `ov.conf` → built-in defaults.
The plugin omits server-owned Context defaults such as `limit=10`, `max_tokens=1600`,
and `query_expansion="auto"` unless you explicitly override them.
An explicit legacy `recallLimit` is converted to per-category coding quotas,
not enforced as a final result cap. Values from 1 through 5 therefore produce
an effective total quota of 6, one retrieval slot for each coding domain. New
direct API integrations should configure `quotas` instead.

### Workspace configuration files

A repository can carry its own plugin settings in `<repo-root>/.openviking/config.json`, which the team commits, and `<repo-root>/.openviking/config.local.json`, which stays private and gitignored. A third layer, this machine's entry under `~/.openviking/workspaces/`, outranks both.

```json
{
  "version": 1,
  "peer": { "source": "git" },
  "recall": { "peer_scope": "actor" },
  "bypass": { "session_patterns": ["**/fixtures/**"] }
}
```

`version: 1` is required; a file declaring another version is skipped with a warning. Schema v1 is `peer.source`, `peer.id`, `recall.enabled`, `recall.peer_scope`, `recall.dedup_turns`, `recall.max_items`, `recall.score_threshold`, `capture.enabled`, `capture.commit_token_threshold`, `bypass.session_patterns`, and `labels`. Lists union across layers, and a leading `"!reset"` drops what was inherited. Unknown keys are kept and ignored.

These files are trusted without a prompt, because a hook is non-interactive and an approval gate would mean one command per workspace. What is refused is structural: connection and credential keys (`url`, `api_key`, `account`, `user`, `extra_headers`, …) are stripped with a warning and `${VAR}` is never expanded in them. What a committed file switches off is announced by `ov-memory-doctor` rather than blocked.

Keep `.gitignore` from ignoring all of `.openviking/`, or `config.json` can never be committed — narrow the rule to `.openviking/media/` and `.openviking/downloads/`. The doctor warns while the blanket rule is in place.

### Digest compression

`recallCompress` decides where the digest is produced and defaults to `auto`. `client` always compresses locally through `claude -p` (Sonnet with low effort by default — Haiku ignores the effort knob, so its latency is unbounded), keeping the token cost on your own subscription. `server` asks OpenViking for the digest. `auto` prefers local and falls back to the server when no healthy host CLI is found. Compressor execution or output-validation failures fall back to the uncompressed context block; an exact `NO_RELEVANT_MEMORY` response from either compressor is a successful empty result and injects nothing. The compressor subprocess runs with all OpenViking hooks disabled so it cannot recurse. The former `OPENVIKING_RECALL_REWRITE` environment variable and `recallRewrite` config key remain supported as lower-priority compatibility aliases.

### Legacy `claude_code` block in `ov.conf`

Earlier plugin versions configured tuning fields under a `claude_code` block in `~/.openviking/ov.conf`. That still works for backward compatibility — every env var above has a camelCase counterpart (`OPENVIKING_RECALL_LIMIT` → `claude_code.recallLimit`, `OPENVIKING_BYPASS_SESSION_PATTERNS` → `claude_code.bypassSessionPatterns` as a JSON array, etc.). Env vars take priority. New deployments should prefer env vars and shell rc — server config files shouldn't carry per-developer-machine tuning.

## Hook timeouts

Defaults in `hooks/hooks.json`:

| Hook                | Timeout | Notes                                                                                                  |
|---------------------|---------|--------------------------------------------------------------------------------------------------------|
| `SessionStart`      | `120s`  | Generous because resume/compact may pull a large archive overview                                      |
| `UserPromptSubmit`  | `60s`   | Allows the default local compressor to finish; its own timeout remains shorter so the hook can degrade safely |
| `Stop`              | `45s`   | Auto-capture parses transcript + pushes turns; async detach makes the user-perceived time near-zero    |
| `PreCompact`        | `30s`   | Synchronous commit before Claude Code mutates the transcript                                           |
| `SessionEnd`        | `30s`   | Final commit; async-detached                                                                           |
| `SubagentStart`     | `10s`   | Lightweight: just persists isolation state                                                             |
| `SubagentStop`      | `45s`   | Reads subagent transcript and commits; async-detached                                                  |

Keep `claude_code.captureTimeoutMs` below the `Stop` timeout so the script can fail gracefully and still update its incremental state.

## Statusline

The plugin renders a one-line status of OpenViking under your Claude Code input box. The installer registers it in `~/.claude/settings.json` (CC's plugin manifest doesn't accept a `statusLine` field, so this is the only way to wire it in).

Examples:

```text
OV ✓ │ Fable 5 · ctx 42% │ ↩ 6 mem · 50ms          6 memories injected; model + context usage
OV ⚠ slow                                  probe missed the 1 s budget (server may be lagging)
OV ✗ offline                               server unreachable
OV ⚡ bypass │ Fable 5 · ctx 42%            OPENVIKING_BYPASS_SESSION* matched
OV ✓ │ ✎ 573/20k · 2 arch                  pending capture, two archives produced this session
OV ✓ │ 🔗 resumed │ +3 today               session re-hydrated; 3 archives committed today
```

The `ctx` percentage reproduces Claude Code's native context indicator (a custom statusLine replaces it), with the native color thresholds: `<70%` dim, `70–89%` yellow, `≥90%` red. Hide it with `OPENVIKING_STATUSLINE_CTX=off`.

For the full segment glossary and personalization recipes (hide segments, recolor, compose with another statusline, add a custom segment), see [`STATUSLINE.md`](./STATUSLINE.md).

Data flow:

- `auto-recall.mjs` / `auto-capture.mjs` / `session-start.mjs` write small snapshots to `~/.openviking/state/{last-recall,last-capture,last-session-event,daily-stats}.json` after each turn.
- `scripts/statusline.mjs` reads those snapshots plus a 5 s shared cache of `GET /health`.
- Network calls have a hard 1 s timeout. Cache is shared across CC sessions to prevent stampedes.

Disable / customize:

- `OPENVIKING_STATUSLINE=off` — silence without removing the registration.
- `NO_COLOR=1` (or non-TTY) — strip ANSI colors automatically.
- Remove entirely: `jq 'del(.statusLine)' ~/.claude/settings.json > t && mv t ~/.claude/settings.json`.
- Already had a custom statusline? The installer prompts replace / skip / manual-compose.

## Debug logging

Set `claude_code.debug: true` in `ov.conf` or `OPENVIKING_DEBUG=1` to write hook logs to `~/.openviking/logs/cc-hooks.log`.

- `auto-recall` logs key stages plus a compact `ranking_summary` by default.
- Set `claude_code.logRankingDetails: true` only when investigating per-candidate scoring; output is verbose.
- For deep diagnosis, run the standalone scripts `scripts/debug-recall.mjs` and `scripts/debug-capture.mjs` against a sample input rather than leaving the hook log on permanently.

## Troubleshooting

Start with the bundled doctor — it checks the install (marketplace, enablement, hooks, MCP wiring), the resolved config (which file won, API key shown masked), the connection (reachability, auth, `/mcp`) and recent hook activity, and prints a fix for every finding:

```bash
node "$(jq -r '.plugins["openviking-memory@openviking"][0].installPath' ~/.claude/plugins/installed_plugins.json)/scripts/ov-memory-doctor.mjs"
```

Or just ask Claude to check the plugin: the `ov-memory-doctor` skill runs the same script and walks the report. When the server runs on the same machine (loopback url) the report adds a Server health section — whether anything listens on the port, plugin-only keys in ov.conf that stop the server from starting, and `GET /ready`; everything else server-side (config validation, live embedding probe, native engine, disk) stays with `openviking-server doctor`.

| Symptom                                    | Cause                                                        | Fix                                                                                                |
|--------------------------------------------|--------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| Plugin not activating                      | No `ov.conf` / `ovcli.conf` found                            | Create one, or set `OPENVIKING_MEMORY_ENABLED=1` plus the URL/API_KEY env vars                     |
| Hooks fire but recall is empty             | OpenViking server not running, or wrong URL                  | `curl http://localhost:1933/health` (or your remote URL)                                           |
| Auto-capture extracts 0 memories           | Wrong embedding/extraction model in `ov.conf`                | Check `embedding` / `vlm` config; review server logs                                               |
| MCP tools hit the wrong server              | stale `ovcli.conf` / env vars, or Claude Code not restarted after config change | See [Configuring MCP](#configuring-mcp), verify `~/.openviking/ovcli.conf`, then restart Claude Code |
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
| Scope        | Per-project                       | Cross-project, cross-session, peer-scoped          |
| Capacity     | ~200 lines (context limit)        | Unlimited (server-side storage)                    |
| Extraction   | Manual rules                      | LLM-powered entity / preference / event extraction |
| Subagents    | Same as parent                    | Isolated session + peer-scoped capture             |

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
     │                  │  MCP tools (stdio proxy → /mcp)            │
     │                  │ find/search/recall/remember/… │              │
     └─────────────────►│                             │              │
        OV session      └─────────────────────────────►              │
        context inject                                └──────────────┘
```

There is no TypeScript build step and no runtime npm bootstrap. Hooks are plain `.mjs` files that talk to OpenViking over HTTP; MCP uses `servers/mcp-proxy.mjs` as a zero-dependency stdio bridge to the OpenViking server's `/mcp` endpoint.

A persistent OpenViking session is created on first contact and reused for the entire Claude Code session. The OV session ID is `cc-<cc_session_id>` (the CC session_id verbatim, no hashing), so resume / compact / multi-hook events all target the same session. Archival + memory extraction is triggered client-side: the `Stop` hook commits when server-reported pending tokens cross `commitTokenThreshold` (default 20000), and `PreCompact` / `SessionEnd` / `SubagentStop` commit unconditionally.

### Hook responsibilities

| Hook                  | Trigger                                  | Action                                                                                            |
|-----------------------|------------------------------------------|---------------------------------------------------------------------------------------------------|
| `UserPromptSubmit`    | Each user turn                           | Search OV → rank → inject `<openviking-context>` block within a token budget                      |
| `Stop`                | Claude finishes a response               | Parse transcript → push new user turns to OV session → commit when pending tokens cross threshold |
| `SessionStart`        | New / resumed / post-compact session     | On `resume`/`compact`, fetch the latest archive overview and inject it as additional context      |
| `PreCompact`          | Before Claude Code rewrites the transcript | Commit pending messages so they become an archive before CC mutates the transcript                |
| `SessionEnd`          | Claude Code session closes               | Final commit so the last window is archived                                                       |
| `SubagentStart`       | Parent spawns a subagent via Task tool   | Derive an isolated OV session ID for the subagent, persist start state                            |
| `SubagentStop`        | Subagent finishes                        | Read subagent transcript → push to an isolated session with subagent peer identity → commit       |
| `PreToolUse`          | Native `Read` / `Glob` / `Grep` on a `viking://` URI | Deny the call and point Claude to the equivalent OpenViking MCP tool                  |
| `PostToolUse`         | `Read` of a `SKILL.md` file              | Optional (default off): inject an experience block when OV has relevant skill-experience memories |

### Async write path

`Stop`, `SessionEnd`, and `SubagentStop` use a detached-worker pattern: the parent hook drains stdin, prints `{decision:"approve"}` to unblock Claude Code, then spawns a detached clone to do the HTTP work. The user never waits for OV. `PreCompact` stays synchronous because Claude Code mutates the transcript right after.

Disable with `claude_code.writePathAsync: false` if you need deterministic ordering during debugging.

### Memory pollution prevention

`auto-capture` strips `<openviking-context>`, `<system-reminder>`, `<relevant-memories>`, and `[Subagent Context]` blocks from each turn before pushing to OV. Without this, the recall context the plugin injects this turn would be captured back as part of the user's "message" next turn, creating a self-referential pollution loop.

### MCP tools available from the server

The plugin's `.mcp.json` starts a local stdio proxy, which connects to the OpenViking server's native HTTP MCP endpoint at `/mcp`. Claude can call the server's retrieval, memory, resource, watch, filesystem, and code-navigation tools on demand.

See the [MCP integration guide](../../docs/en/guides/06-mcp-integration.md) for the canonical tool list and parameters.

### Plugin structure

```
claude-code-memory-plugin/
├── .claude-plugin/
│   └── plugin.json          # plugin manifest
├── hooks/
│   └── hooks.json           # 9 hook registrations
├── commands/
│   └── ov.md                # /ov status command
├── skills/
│   ├── openviking-memory/   # how to use the memory tools
│   ├── ov-experience-memory/
│   └── ov-memory-doctor/    # install / config / connection / local-server troubleshooting
├── servers/
│   └── mcp-proxy.mjs        # stdio -> OpenViking /mcp bridge
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
│   ├── ov-status.mjs        # /ov status report
│   ├── ov-memory-doctor.mjs # diagnostics script (ov-memory-doctor skill)
│   └── lib/
│       ├── ov-session.mjs   # OV HTTP client + session helpers + bypass check
│       └── async-writer.mjs # detached-worker helper for write-path hooks
├── .mcp.json                # MCP server config (local stdio proxy)
├── package.json             # type:module marker only — no runtime deps
└── README.md
```

## License

Apache-2.0 — same as [OpenViking](https://github.com/volcengine/OpenViking).
