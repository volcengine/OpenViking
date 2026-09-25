# Claude Code Memory Plugin

Give [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) cross-project and cross-session long-term memory. Once installed, every conversation automatically recalls relevant memories and captures new content without requiring the model to make any tool calls.

Source: [examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [Blog: motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

Claude Code and Codex share one installer. It asks for your language (English/中文), which harnesses to install, the download source, and your OpenViking credentials; the installation supports repeated runs.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

In regions where GitHub is hard to reach, run the same installer from the Volcengine TOS mirror (or pick "TOS mirror" at the download-source prompt):

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

> **TOS caveat for Claude Code**: the TOS channel registers a local directory marketplace, which cannot auto-update — re-run the installer to update. (Codex on TOS installs from a TOS-hosted git repo and keeps remote updates.)

No shell wrapper is needed anymore: the plugin ships a stdio MCP proxy that reads `~/.openviking/ovcli.conf` (or `OPENVIKING_*` env vars) at runtime, same as the hooks.

After a session has been captured and processed, start a new conversation and ask about a specific fact from it to check cross-session recall.

<details>
<summary><b>Manual setup</b></summary>

If you prefer to set it up manually:

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or run the bundled wizard `node <plugin-dir>/scripts/setup.mjs` after installing.

2. **Install the plugin** from the remote marketplace (no clone needed):

   ```bash
   claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
   claude plugin install openviking-memory@openviking
   ```

   Or, for development, register a local checkout: `claude plugin marketplace add "<repo>/examples"` then install the same plugin id.

3. **Start Claude Code** and run `/mcp` to verify that the OpenViking entry is connected.

> Don't have `ovcli.conf` yet? See the [Deployment Guide → CLI](../guides/03-deployment.md#cli).
>
> Using pure local mode (`http://127.0.0.1:1933`, no authentication)? Skip step 1—the plugin automatically defaults to the local setup.
>
> Running Claude Code < 2.0? The installer detects it and falls back to `claude mcp add` + a hooks merge automatically; see the [Legacy mode section in the plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#legacy-mode-claude-code--20).

</details>

## Verify

Launch `claude`, then:

- `/plugins` → Verify that **openviking-memory** is listed under "Installed", with the **openviking** MCP connected below it.
- `/mcp` → Ensure the OpenViking entry displays your server URL along with valid authentication.
- `/openviking-memory:ov` → View server health, identity, recall/injection statistics, and toggle states.

If the plugin does not seem to activate, set `OPENVIKING_DEBUG=1` and check the logs at `~/.openviking/logs/cc-hooks.log`.

## How it works

The plugin hooks into the Claude Code lifecycle:

- **Before every prompt** — searches OpenViking and injects relevant memories
- **After each response** — captures new conversation turns
- **On session start** — injects your profile, memory index, and skill catalog
- **Before compaction and on session end** — commits pending messages
- **For each subagent** — assigns an isolated memory session
- **Before a native file tool touches a `viking://` path** — blocks the call and names the OpenViking MCP tool to use instead; a `Write` or `Edit` on a skill path is pointed to `add_skill`

Capture normally runs in a detached worker. Lifecycle hooks still have timeouts and may wait for capture or commit work; background execution does not guarantee immediate persistence or zero delay.

The skill catalog is an `<available-skills>` block that lists the skills stored in OpenViking: your own under `viking://~/skills` first, then the ones shared with your account under `viking://agent/skills`, each with a short description. Before following a listed skill, Claude reads its `SKILL.md` with the OpenViking `read` tool. The catalog has its own token budget: when the descriptions do not fit, it lists names only, and when not even one name fits, it shrinks to a one-line count. The bundled `openviking-skills` skill tells Claude how to find and use OpenViking skills, create, install, and share them with the `add_skill` MCP tool, delete them, and move local skills such as `~/.claude/skills` into OpenViking when you ask.

Tool calls and results are captured as dedicated `tool` parts, and `tool_output` is reported verbatim. Truncation is the server's job: output larger than `tool_output_externalization.threshold_chars` (default `20000`) is written to the session's tool-result store, and the part keeps a synopsis stub plus `tool_output_ref`, so the original stays readable through [`/api/v1/sessions/{id}/tool-results`](../api/05-sessions.md#read-tool-result).

<details>
<summary><b>Configuration</b></summary>

Configuration priority: Environment variables > `ovcli.conf` > `ov.conf` > Built-in defaults (`http://127.0.0.1:1933`, no authentication).

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_AUTO_RECALL` | `true` | Auto-recall on every user prompt |
| `OPENVIKING_RECALL_LIMIT` | `10` | Legacy width override converted to per-category coding quotas |
| `OPENVIKING_RECALL_TOKEN_BUDGET` | `2000` | Inline token budget for the final raw-find fallback |
| `OPENVIKING_AUTO_CAPTURE` | `true` | Auto-capture after each turn |
| `OPENVIKING_SKILL_CATALOG` | `true` | Inject the `<available-skills>` catalog at session start |
| `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET` | `1200` | Token budget for `<available-skills>`, separate from the profile budget; `0` turns the catalog off |
| `OPENVIKING_SESSION_START_MAX_BYTES` | `9500` | Byte cap on the whole SessionStart context, kept under Claude Code's 10,000-character limit so the block stays inline instead of being saved to a file; on resume or compact the session archive takes up to half. `0` removes the cap |
| `OPENVIKING_BYPASS_SESSION` | `false` | Skip all hooks for this session |
| `OPENVIKING_BYPASS_SESSION_PATTERNS` | `""` | CSV glob patterns to auto-bypass |
| `OPENVIKING_RECALL_QUERY_FILTERS` | `""` | CSV of sed-style regex rules applied to the prompt before it becomes a query ([grammar and examples](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#input-filters)) |
| `OPENVIKING_CAPTURE_FILTERS` | `""` | CSV of sed-style regex rules applied to every captured turn (same grammar) |
| `OPENVIKING_MEMORY_ENABLED` | (auto) | Force on/off |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/cc-hooks.log` |

Most of these knobs can also live in `ovcli.conf` under `plugin` — see [Plugin Settings](../configuration/02-client.md#plugin-settings). The two filter knobs are better written there, as JSON arrays, because the environment form is split on commas.

If recall latency matters most, see [Low-latency recall](./01-overview.md#low-latency-recall) for the environment-variable and `ovcli.conf` settings that disable query expansion and result compression.

With a user/admin key, the server derives identity from the key. Set `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` only for trusted mode, using values supplied by your administrator. The complete list of environment variables is available in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration).

</details>

## Workspace peer

Memories are filed under a peer derived from the repository you are working in, so one project keeps one memory across clones, worktrees, and subdirectories. The default `peer.source: "git"` uses the repository's normalized `origin` URL — with `origin git@github.com:volcengine/OpenViking.git`, the peer is `github.com-volcengine-openviking` — falling back to the repository root path; outside a repository no peer is sent at all, and what is remembered there goes to your user-level space at `viking://user/<you>/memories`. A fork has its own `origin`, so it stays a separate peer.

Change it with `OPENVIKING_PEER_SOURCE`, with `plugin.peerSource` in `ovcli.conf`, or with `peer.source` in the workspace's `.openviking/config.json` (a `"version": 1` file the team can commit): `"cwd"` restores the previous behavior — the working directory with every non-alphanumeric character replaced by `-` — `"none"` sends no peer, and a template such as `"team-{dir}"` builds your own. To [give a directory that is not a repository its own memory](../configuration/02-client.md#give-a-directory-its-own-peer), create `.openviking/config.json` in it containing `{"version": 1, "peer": {"id": "my-project"}}`. Memories written under the earlier cwd-derived peer are still recalled, so nothing needs migrating. The layer precedence and the full workspace-file schema are in [Client Configuration → Workspace Configuration](../configuration/02-client.md#workspace-configuration).

## Statusline

The plugin renders an OpenViking status indicator beneath your Claude Code input box, allowing you to check connection health, recall count, capture progress, and session state at a glance. See [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/STATUSLINE.md) for a complete glossary of segments and personalization recipes.

## Troubleshooting

| Issue | Cause | Solution |
|---------|-------|-----|
| Plugin is not activating | Missing `ov.conf` or `ovcli.conf` | Run the [installer](#install), or set `OPENVIKING_MEMORY_ENABLED=1` along with the URL/API_KEY environment variables |
| Hooks fire but recall is empty | Server is not running or the URL is incorrect | Check server health: `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP tools hit `127.0.0.1` instead of the remote server | `~/.openviking/ovcli.conf` has no `url` (the proxy falls back to the local default) | Fix `ovcli.conf` (or run `node <plugin-dir>/scripts/setup.mjs`), then restart Claude Code |
| MCP tool calls fail with an auth error | The active ovcli config has no valid `api_key` for an authenticated server | Update the `api_key` in `ovcli.conf`; the stdio proxy re-reads it after auth failures |
| Remote auth 401 / 403 | Invalid credentials or insufficient permission | Check the active user/admin key and resource access. Only trusted mode requires administrator-supplied account/user headers; these headers cannot change a user key’s identity. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — Motivation, architecture overview, and demo
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — Full environment variable tables, hook details, and architecture diagrams
- [MCP Clients](./06-mcp-clients.md) — Information on MCP tool parameters and other clients
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup instructions
