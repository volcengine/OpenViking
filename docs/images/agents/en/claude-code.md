Add cross-project, cross-session long-term memory to [Claude Code](https://docs.claude.com/en/docs/claude-code/overview). After installation, every conversation automatically recalls relevant memories and captures new content, with no tool calls required from the model.

Source: [examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [Blog: motivation and demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Step 1: Install

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/claude-code-memory-plugin/tos-install.sh)
```

The installer checks dependencies, configures the OpenViking connection, and installs the plugin. Each step is idempotent, so it is safe to rerun.

After installation, activate the `claude` wrapper in your current terminal, or simply open a new terminal window:

```bash
source ~/.openviking/openviking-repo/examples/claude-code-memory-plugin/setup-helper/wrapper.sh
```

> Launch Claude Code through a custom command? A wrapper script like `cc-custom`, or a multi-word launcher (a base command plus a sub-command) — list it at the installer's "Extra launch commands" step (or pass `OPENVIKING_CC_WRAP_EXTRA='cc-custom'`) to inject credentials there too.

After using it for a while, start a new conversation and ask about something you mentioned earlier. It should remember.

<details>
<summary><b>Manual installation</b></summary>

If you prefer to install manually:

1. **Wrap `claude`** - Add these two lines to the end of `~/.zshrc` (for zsh) or `~/.bashrc` (for bash), replacing `<repo-path>` with the absolute path to your local checkout. Each `claude` invocation then injects `OPENVIKING_URL` and `OPENVIKING_API_KEY` from `~/.openviking/ovcli.conf`, keeping the API key scoped to the `claude` process tree:

   ```bash
   _ov_wrapper="<repo-path>/examples/claude-code-memory-plugin/setup-helper/wrapper.sh"
   [ -f "$_ov_wrapper" ] && source "$_ov_wrapper"
   ```

   For the function implementation and why a global `export` is not used, see the [plugin README -> Configuring MCP](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuring-mcp).

2. **Install the plugin** from the OpenViking repository root:

   ```bash
   claude plugin marketplace add "$(pwd)/examples"
   claude plugin install claude-code-memory-plugin@openviking-plugins-local
   ```

3. **Start Claude Code** and run `/mcp` to confirm the OpenViking entry shows your server URL.

> No `ovcli.conf` yet? Create it first via Deployment Guide -> CLI.
>
> Pure local mode (`http://127.0.0.1:1933`, no auth)? Skip step 1. The plugin uses the local defaults.
>
> Claude Code < 2.0? See the [compatibility mode section](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#legacy-mode-claude-code--20) in the plugin README.

</details>

## Step 2: Verify

```bash
type claude        # Expected: claude is a shell function
```

> If the previous command printed a path instead of `shell function`, the wrapper isn't active yet. Re-source it (or open a new terminal) before launching `claude`, otherwise it silently connects to `127.0.0.1` with no auth.

Launch `claude` from the terminal where `type claude` reports a shell function, then:

- `/plugins` -> Find **openviking-memory** under Installed. Its **openviking** MCP entry should be connected.
- `/mcp` -> The OpenViking entry should show your server URL and valid authentication.
- `/openviking-memory:ov` -> Shows server status, identity, recall/injection stats, and toggle state.

If the plugin does not appear to work, set `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/cc-hooks.log`.

## How it works

The plugin hooks into the Claude Code lifecycle:

- **Before every prompt** - searches OpenViking and injects relevant memories
- **After each response** - captures new conversation turns
- **On session start** - injects your profile and memory index
- **Before compaction and on session end** - commits pending messages
- **For each subagent** - assigns an isolated memory session

All writes run asynchronously, so they never block your workflow.

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
| Plugin is not active | `ov.conf` / `ovcli.conf` cannot be found | Run [Step 1: Install](#step-1-install), or set `OPENVIKING_MEMORY_ENABLED=1` plus URL/API_KEY |
| Hooks run but recall is empty | Server is down or URL is wrong | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP tools connect to `127.0.0.1` instead of remote | Missing shell function wrapper | Confirm `type claude` returns "shell function"; see [Step 1: Install](#step-1-install) |
| `type claude` shows a path instead of a shell function (wrapper inactive) | The rc wasn't `source`d after install, or you launched from a terminal that didn't load it | Run `source ~/.zshrc` (or `~/.bashrc` on bash), or open a new terminal |
| Launching via an alias (e.g. `cc`) injects no credentials | The alias *name* was listed in `OPENVIKING_CC_WRAP_EXTRA` (alias names are skipped), or the alias's target command isn't wrapped | Wrap the command the alias points to, not the alias: `alias cc=claude` needs nothing; for `alias cc=claude-custom`, add `claude-custom` |
| Remote auth returns 401 / 403 | API key is wrong or tenant headers are missing | Check `OPENVIKING_API_KEY`; for multi-tenant deployments also verify `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |

## Reference docs

- [Blog: OpenViking for Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) - Motivation, architecture overview, and demo
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) - Full environment variable table, hook details, and architecture diagram
