# Claude Code Memory Plugin

Long-term semantic memory for [Claude Code](https://docs.claude.com/en/docs/claude-code/overview). Recall happens automatically before every prompt and capture happens automatically after every turn — no MCP tool calls required from the model.

Source: [examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin)

## Quick Start

### One-line installer (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

The script runs on macOS and Linux. It checks dependencies, asks whether you'll connect to a **self-hosted** server or to **Volcengine OpenViking Cloud** (`https://api.vikingdb.cn-beijing.volces.com/openviking`), sets up `~/.openviking/ovcli.conf` (prompting only if missing), clones the OpenViking repo to `~/.openviking/openviking-repo`, adds the `claude` function wrapper to your shell rc, and installs the plugin via `claude plugin install`. Every step is idempotent — re-running is safe.

If you'd rather do it by hand, follow the three steps below.

### Manual setup

#### 1. Wrap `claude` to inject env from `ovcli.conf`

This is the recommended path. The plugin's hooks **and** the bundled MCP server both read env vars, so we set them once — but scoped to the `claude` invocation only, not exported globally. Append to `~/.zshrc` or `~/.bashrc`:

```bash
claude() {
  if [ -f ~/.openviking/ovcli.conf ]; then
    OPENVIKING_URL=$(jq -r '.url' ~/.openviking/ovcli.conf) \
    OPENVIKING_API_KEY=$(jq -r '.api_key' ~/.openviking/ovcli.conf) \
    command claude "$@"
  else
    command claude "$@"
  fi
}
```

Re-source your rc and verify (use `~/.bashrc` if you're on bash):

```bash
source ~/.zshrc    # or: source ~/.bashrc
type claude        # expect: claude is a shell function
```

Inside Claude Code, run `/mcp` after the next start — the OpenViking entry should show your remote URL with valid auth.

> **Don't have `ovcli.conf` yet?** See the [CLI section of the Deployment Guide](../guides/03-deployment.md#cli) to set one up.
>
> **Pure local mode** (`http://127.0.0.1:1933`, no auth)? Skip this step — the plugin uses the local default silently.
>
> **Why a function instead of `export`?** Globally exported env vars leak into every child process spawned from your shell — npm scripts, build tools, crash dumps, `/proc/<pid>/environ`. The function-wrapper limits the secret to the `claude` process tree only.

#### 2. Install the plugin

From the OpenViking repo root:

```bash
claude plugin marketplace add "$(pwd)/examples"
claude plugin install claude-code-memory-plugin@openviking-plugins-local
```

> The plugin installs at user scope by default — active from any directory. We don't pass `--scope` because older Claude Code 2.0.x builds (e.g. 2.0.76) reject the flag.
>
> The marketplace entry points Claude Code at the source directory. Edits to `scripts/`, `hooks/`, and config files take effect on the next hook invocation — no reinstall. But moving / renaming / deleting the source dir, or `git checkout`-ing to a branch without these files, breaks the plugin.
>
> **Claude Code < 2.0?** `claude plugin` shipped in 2.0 (Oct 2025). Older builds still expose `claude mcp add` + the hooks system — the [plugin README's Legacy mode section](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#legacy-mode-claude-code--20) shows how to wire the same functionality by hand, and the one-line installer detects the version and offers it automatically.

#### 3. Start Claude Code

```bash
claude
```

Inside Claude Code, run `/mcp` to confirm the OpenViking MCP entry shows your remote URL. If the plugin doesn't seem to fire, set `OPENVIKING_DEBUG=1` and check `~/.openviking/logs/cc-hooks.log`.

## Why a function wrapper?

The plugin's hooks read `ovcli.conf` directly — but the bundled `.mcp.json` entry **cannot**. Claude Code parses `.mcp.json` itself and only supports `${VAR}` substitution, so config-file values can't transparently reach the MCP server URL or auth headers.

Injecting env vars at `claude` invocation is the single path that covers both hooks and MCP. Wrapping in a shell function (rather than a global `export`) keeps the API key out of every other shell child process — see the security note in [the manual setup step 1](#1-wrap-claude-to-inject-env-from-ovcli-conf).

**Symptom of misconfiguration**: hooks (auto-recall, auto-capture) work fine because they read `ovcli.conf` via Node, but the on-demand MCP tools (`search`, `read`, `store`, …) silently connect to `http://127.0.0.1:1933` with empty auth headers, and `/mcp` shows the wrong URL.

## Configuration

### Resolution priority

Every plugin field follows this chain (highest → lowest):

1. **Environment variables** (`OPENVIKING_*`)
2. **`ovcli.conf`** — connection fields only (`url`, `api_key`, `account`, `user`, `agent_id`)
3. **`ov.conf`** — server config; the plugin reads `server.url`, `server.root_api_key`, and a legacy `claude_code` block if present
4. **Built-in defaults** (`http://127.0.0.1:1933`, no auth)

> ⚠️ **Hooks only.** This chain is implemented in `scripts/config.mjs` and consumed by hook scripts. It does **not** apply to MCP server registration — see [Why a function wrapper?](#why-a-function-wrapper) above.

### Key environment variables

| Env Var                                          | Default       | Description                                                           |
|--------------------------------------------------|---------------|-----------------------------------------------------------------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL`         | —             | Full server URL                                                       |
| `OPENVIKING_API_KEY` / `OPENVIKING_BEARER_TOKEN` | —             | API key; sent as `Authorization: Bearer <key>`                        |
| `OPENVIKING_AUTO_RECALL`                         | `true`        | Enable auto-recall on every user prompt                               |
| `OPENVIKING_RECALL_LIMIT`                        | `6`           | Max memories to inject per turn                                       |
| `OPENVIKING_RECALL_TOKEN_BUDGET`                 | `2000`        | Token budget for inline content; over-budget items degrade to URI hints |
| `OPENVIKING_AUTO_CAPTURE`                        | `true`        | Enable auto-capture; also gates write hooks                           |
| `OPENVIKING_BYPASS_SESSION`                      | `false`       | One-shot: `1`/`true` skips every hook in the current process          |
| `OPENVIKING_BYPASS_SESSION_PATTERNS`             | `""`          | CSV of glob patterns matched against `session_id` or `cwd`            |
| `OPENVIKING_MEMORY_ENABLED`                      | (auto)        | `0`/`false`=force off; `1`/`true`=force on                            |
| `OPENVIKING_DEBUG`                               | `false`       | Write hook logs to `~/.openviking/logs/cc-hooks.log`                  |

For multi-tenant deployments, `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_AGENT_ID` set the corresponding `X-OpenViking-*` headers. The full env-var list (recall tuning, capture tuning, lifecycle, debug) is in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration).

### Bypass a session

Use Claude Code in a `/tmp` PoC directory without polluting your long-term memory:

```bash
# Persistent: any session whose session_id or cwd matches a pattern
export OPENVIKING_BYPASS_SESSION_PATTERNS='/tmp/**,**/scratch/**,/Users/me/Dev/throwaway/*'

# Or one-shot:
OPENVIKING_BYPASS_SESSION=1 claude
```

When bypass is active, every hook approves immediately without contacting OpenViking.

## Compared to Claude Code's built-in `MEMORY.md`

This plugin **complements** Claude Code's native memory system, it doesn't replace it:

| Feature      | Built-in `MEMORY.md`              | OpenViking plugin                                  |
|--------------|-----------------------------------|----------------------------------------------------|
| Storage      | Flat markdown                     | Vector DB + structured extraction                  |
| Search       | Loaded into context wholesale     | Semantic similarity + ranking + token budget       |
| Scope        | Per-project                       | Cross-project, cross-session, cross-agent          |
| Capacity     | ~200 lines (context limit)        | Unlimited (server-side storage)                    |
| Extraction   | Manual rules                      | LLM-powered entity / preference / event extraction |
| Subagents    | Same as parent                    | Isolated session + typed agent namespace           |

## Hook behavior

| Hook                  | Trigger                                    | Action                                                                                            |
|-----------------------|--------------------------------------------|---------------------------------------------------------------------------------------------------|
| `UserPromptSubmit`    | Each user turn                             | Search OV → rank → inject `<openviking-context>` block within a token budget                      |
| `Stop`                | Claude finishes a response                 | Parse transcript → push new user turns to OV session → commit when pending tokens cross threshold |
| `SessionStart`        | New / resumed / post-compact session       | On `resume`/`compact`, fetch the latest archive overview and inject it as additional context      |
| `PreCompact`          | Before Claude Code rewrites the transcript | Commit pending messages so they become an archive before CC mutates the transcript                |
| `SessionEnd`          | Claude Code session closes                 | Final commit so the last window is archived                                                       |
| `SubagentStart`       | Parent spawns a subagent via Task tool     | Derive an isolated OV session ID for the subagent, persist start state                            |
| `SubagentStop`        | Subagent finishes                          | Read subagent transcript → push to isolated session with subagent-typed agent header → commit     |

`Stop`, `SessionEnd`, and `SubagentStop` use a detached-worker pattern so the user never waits for OpenViking. Disable with `OPENVIKING_WRITE_PATH_ASYNC=false` if you need deterministic ordering.

`auto-capture` strips `<openviking-context>`, `<system-reminder>`, `<relevant-memories>`, and `[Subagent Context]` blocks before pushing to OV — without this, the recall context the plugin injects this turn would be captured back as part of the user's message next turn.

## Troubleshooting

| Symptom                                    | Cause                                                          | Fix                                                                                          |
|--------------------------------------------|----------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Plugin not activating                      | No `ov.conf` / `ovcli.conf` found                              | Run the [one-line installer](#one-line-installer-recommended), or set `OPENVIKING_MEMORY_ENABLED=1` plus URL/API_KEY env vars |
| Hooks fire but recall is empty             | OpenViking server not running, or wrong URL                    | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"`                                     |
| MCP tools hit `127.0.0.1` instead of remote | `.mcp.json` only resolves `${VAR}`, no `ovcli.conf` integration | See [Why a function wrapper?](#why-a-function-wrapper)                                       |
| Remote auth 401 / 403                      | Wrong API key or missing tenant headers                        | Verify `OPENVIKING_API_KEY`; for multi-tenant, also check `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |
| `Stop` hook times out                      | Server slow + sync write path                                  | Leave `OPENVIKING_WRITE_PATH_ASYNC=true` (default), or raise the `Stop` timeout in `hooks/hooks.json` |

## See also

- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — exhaustive env-var tables, hook timeouts, debug logging, architecture diagram
- [Migration notes](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/MIGRATION.md) — upgrading from earlier plugin versions
- [MCP Integration Guide](../guides/06-mcp-integration.md) — for MCP tool parameters and other clients
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup
