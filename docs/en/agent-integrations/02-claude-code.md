# Claude Code Memory Plugin

Give [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) a cross-project, cross-session memory that grows smarter over time. Install once — recall and capture happen automatically on every conversation, no MCP tool calls required from the model.

Source: [examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [Blog: motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

The installer checks dependencies, configures your OpenViking connection, and installs the plugin. Every step is idempotent — re-running is safe.

After install, start Claude Code and ask it something from a previous session. It remembers.

<details>
<summary><b>Manual setup</b></summary>

If you prefer to set things up by hand:

1. **Shell function wrapper** — append a `claude()` function to `~/.zshrc` or `~/.bashrc` that injects `OPENVIKING_URL` and `OPENVIKING_API_KEY` from `~/.openviking/ovcli.conf` at each invocation. This keeps the API key scoped to the `claude` process tree. See the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#1-wrap-claude-to-inject-env-from-ovcliconf) for the full function and security rationale.

2. **Install the plugin** from the OpenViking repo root:

   ```bash
   claude plugin marketplace add "$(pwd)/examples"
   claude plugin install claude-code-memory-plugin@openviking-plugins-local
   ```

3. **Start Claude Code** and run `/mcp` to confirm the OpenViking entry shows your server URL.

> Don't have `ovcli.conf` yet? See [Deployment Guide → CLI](../guides/03-deployment.md#cli).
>
> Pure local mode (`http://127.0.0.1:1933`, no auth)? Skip step 1 — the plugin uses the local default.
>
> Claude Code < 2.0? See the [plugin README's Legacy mode section](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#legacy-mode-claude-code--20).

</details>

## Verify

```bash
type claude        # expect: claude is a shell function
```

Inside Claude Code:

- `/plugins` → find **openviking-memory** in Installed (with **openviking** MCP connected underneath)
- `/mcp` → the OpenViking entry should show your server URL with valid auth
- `/openviking-memory:ov` → shows server health, identity, recall/injection stats, and toggle states

If the plugin doesn't seem to fire, set `OPENVIKING_DEBUG=1` and check `~/.openviking/logs/cc-hooks.log`.

## How it works

The plugin hooks into Claude Code's lifecycle: it searches OpenViking and injects relevant memories before every prompt, captures new conversation turns after each response, injects your profile and memory index on session start, commits pending messages before compaction and on session end, and gives each subagent an isolated memory session. All write operations run asynchronously so you never wait for OpenViking.

<details>
<summary><b>Configuration</b></summary>

Config priority: env vars > `ovcli.conf` > `ov.conf` > built-in defaults (`http://127.0.0.1:1933`, no auth).

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_AUTO_RECALL` | `true` | Auto-recall on every user prompt |
| `OPENVIKING_RECALL_LIMIT` | `6` | Max memories to inject per turn |
| `OPENVIKING_RECALL_TOKEN_BUDGET` | `2000` | Token budget for inline content |
| `OPENVIKING_AUTO_CAPTURE` | `true` | Auto-capture after each turn |
| `OPENVIKING_BYPASS_SESSION` | `false` | Skip all hooks for this session |
| `OPENVIKING_BYPASS_SESSION_PATTERNS` | `""` | CSV glob patterns to auto-bypass |
| `OPENVIKING_MEMORY_ENABLED` | (auto) | Force on/off |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/cc-hooks.log` |

For multi-tenant deployments, set `OPENVIKING_ACCOUNT` and `OPENVIKING_USER`. Full env-var list in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration).

</details>

## Statusline

The plugin renders an OpenViking status indicator under your Claude Code input box — connection health, recall count, capture progress, and session state at a glance. See [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/STATUSLINE.md) for the full segment glossary and personalization recipes.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Plugin not activating | No `ov.conf` / `ovcli.conf` found | Run the [installer](#install), or set `OPENVIKING_MEMORY_ENABLED=1` plus URL/API_KEY env vars |
| Hooks fire but recall is empty | Server not running or wrong URL | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP tools hit `127.0.0.1` instead of remote | Missing function wrapper | Ensure `type claude` says "shell function"; see [Manual setup](#install) |
| Remote auth 401 / 403 | Wrong API key or missing tenant headers | Check `OPENVIKING_API_KEY`; for multi-tenant also check `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |

## See also

- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — motivation, architecture overview, and demo
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — full env-var tables, hook details, architecture diagram
- [MCP Clients](./06-mcp-clients.md) — for MCP tool parameters and other clients
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup
