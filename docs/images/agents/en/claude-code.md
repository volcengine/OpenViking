Add cross-project, cross-session long-term memory to [Claude Code](https://docs.claude.com/en/docs/claude-code/overview). Install once; every conversation automatically recalls and captures memory, and the model does not need to call any tools manually.

Source: [examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [Blog: motivation and demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Step 1: Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

The installer checks dependencies, configures the OpenViking connection, and installs the plugin. Each step is idempotent, so it is safe to rerun.

After installation, start Claude Code and ask what you discussed in the previous session. It should remember.

<details>
<summary><b>Manual installation</b></summary>

If you prefer to install manually:

1. **Shell function wrapper** - Append a `claude()` function to `~/.zshrc` or `~/.bashrc`. Every invocation reads `OPENVIKING_URL` and `OPENVIKING_API_KEY` from `~/.openviking/ovcli.conf`, so the API key only exists inside the `claude` process tree. See the full function and security notes in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#1-wrap-claude-to-inject-env-from-ovcliconf).

2. **Install the plugin** from the OpenViking repository root:

   ```bash
   claude plugin marketplace add "$(pwd)/examples"
   claude plugin install claude-code-memory-plugin@openviking-plugins-local
   ```

3. **Start Claude Code** and run `/mcp` to confirm the OpenViking entry shows your server URL.

> No `ovcli.conf` yet? Create it first via Deployment Guide -> CLI.
>
> Pure local mode (`http://127.0.0.1:1933`, no auth)? Skip step 1. The plugin silently uses the local defaults.
>
> Claude Code < 2.0? See the [compatibility mode section](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README_CN.md#兼容模式claude-code--20) in the plugin README.

</details>

## Step 2: Verify

```bash
type claude        # Expected: claude is a shell function
```

Inside Claude Code:

- `/plugins` -> Find **openviking-memory** under Installed. Its **openviking** MCP entry should be connected.
- `/mcp` -> The OpenViking entry should show your server URL and valid authentication.
- `/openviking-memory:ov` -> Shows server status, identity, recall/injection stats, and toggle state.

If the plugin does not appear to work, set `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/cc-hooks.log`.

## How it works

The plugin hooks into the Claude Code lifecycle: before every user prompt it searches OpenViking and injects relevant memory; after each assistant response it captures new conversation content; on session start it injects the user profile and memory index; before compact and at session end it commits pending messages; and each subagent gets an isolated memory session. All writes run asynchronously, so they do not block your workflow.

<details>
<summary><b>Configuration</b></summary>

Configuration priority: environment variables > `ovcli.conf` > `ov.conf` > built-in defaults (`http://127.0.0.1:1933`, no auth).

| Environment variable | Default | Description |
|---------|--------|------|
| `OPENVIKING_AUTO_RECALL` | `true` | Automatically recall before each user prompt |
| `OPENVIKING_RECALL_LIMIT` | `6` | Maximum memories injected per turn |
| `OPENVIKING_RECALL_TOKEN_BUDGET` | `2000` | Token budget for inline memory content |
| `OPENVIKING_AUTO_CAPTURE` | `true` | Automatically capture after each turn |
| `OPENVIKING_BYPASS_SESSION` | `false` | Skip all hooks for the current session |
| `OPENVIKING_BYPASS_SESSION_PATTERNS` | `""` | CSV glob patterns for automatic bypass |
| `OPENVIKING_MEMORY_ENABLED` | (auto) | Force enable or disable |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/cc-hooks.log` |

For multi-tenant deployments, set `OPENVIKING_ACCOUNT` and `OPENVIKING_USER`. See the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration) for the full environment variable list.

</details>

## Status line

The plugin renders OpenViking status below the Claude Code input box: connection health, recall count, capture progress, and session state are visible at a glance. See [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/STATUSLINE.md) for the full status levels and customization recipes.

## Troubleshooting

| Symptom | Cause | Fix |
|------|------|------|
| Plugin is not active | `ov.conf` / `ovcli.conf` cannot be found | Run the [installer](#install), or set `OPENVIKING_MEMORY_ENABLED=1` plus URL/API_KEY |
| Hooks run but recall is empty | Server is down or URL is wrong | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP tools connect to `127.0.0.1` instead of remote | Missing shell function wrapper | Confirm `type claude` returns "shell function"; see [Manual installation](#install) |
| Remote auth returns 401 / 403 | API key is wrong or tenant headers are missing | Check `OPENVIKING_API_KEY`; for multi-tenant deployments also verify `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |

## Reference docs

- [Blog: OpenViking for Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) - Why and how to add long-term memory to your coding agent
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) - Full environment variable table, hook details, and architecture diagram
