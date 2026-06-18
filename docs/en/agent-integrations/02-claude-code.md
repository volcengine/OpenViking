# Claude Code Memory Plugin

Give [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) cross-project and cross-session long-term memory. Once installed, every conversation automatically recalls relevant memories and captures new content without requiring the model to make any tool calls.

Source: [examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [Blog: motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

The installer checks dependencies, configures your OpenViking connection, and installs the plugin. Every step is idempotent—re-running it is entirely safe.

In regions where GitHub is hard to reach, use the equivalent command below:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/claude-code-memory-plugin/tos-install.sh)
```

After installation, activate the `claude` wrapper in your current terminal (or simply open a new terminal window):

```bash
source ~/.openviking/openviking-repo/examples/claude-code-memory-plugin/setup-helper/wrapper.sh
```

> Launch Claude Code through a custom command? A wrapper script like `cc-custom`, or a multi-word launcher (a base command plus a sub-command) — list it at the installer's "Extra launch commands" step (or pass `OPENVIKING_CC_WRAP_EXTRA='cc-custom'`) to inject credentials there too.

After using it for a while, try starting a new conversation and asking about something you mentioned earlier—it will remember.

<details>
<summary><b>Manual setup</b></summary>

If you prefer to set it up manually:

1. **Wrap `claude`**: Add these two lines to the end of your `~/.zshrc` (for zsh) or `~/.bashrc` (for bash), replacing `<repo-path>` with the absolute path to your local repository. This ensures each `claude` invocation injects `OPENVIKING_URL` and `OPENVIKING_API_KEY` from `~/.openviking/ovcli.conf`, keeping the API key scoped to the `claude` process tree:

   ```bash
   _ov_wrapper="<repo-path>/examples/claude-code-memory-plugin/setup-helper/wrapper.sh"
   [ -f "$_ov_wrapper" ] && source "$_ov_wrapper"
   ```

   For details on the function implementation and why a global `export` is not used, see the [plugin README → Configuring MCP](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuring-mcp).

2. **Install the plugin** from the OpenViking repository root:

   ```bash
   claude plugin marketplace add "$(pwd)/examples"
   claude plugin install claude-code-memory-plugin@openviking-plugins-local
   ```

3. **Start Claude Code** and run `/mcp` to verify that the OpenViking entry displays your server URL.

> Don't have `ovcli.conf` yet? See the [Deployment Guide → CLI](../guides/03-deployment.md#cli).
>
> Using pure local mode (`http://127.0.0.1:1933`, no authentication)? Skip step 1—the plugin automatically defaults to the local setup.
>
> Running Claude Code < 2.0? See the [Legacy mode section in the plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#legacy-mode-claude-code--20).

</details>

## Verify

```bash
type claude        # expect: claude is a shell function
```

> If the previous command printed a path instead of `shell function`, the wrapper isn't active yet. Re-source it (or open a new terminal) before launching `claude`, otherwise it silently connects to `127.0.0.1` with no auth.

Launch `claude` from the terminal where `type claude` reports a shell function, then:

- `/plugins` → Verify that **openviking-memory** is listed under "Installed", with the **openviking** MCP connected below it.
- `/mcp` → Ensure the OpenViking entry displays your server URL along with valid authentication.
- `/openviking-memory:ov` → View server health, identity, recall/injection statistics, and toggle states.

If the plugin does not seem to activate, set `OPENVIKING_DEBUG=1` and check the logs at `~/.openviking/logs/cc-hooks.log`.

## How it works

The plugin hooks into the Claude Code lifecycle:

- **Before every prompt** — searches OpenViking and injects relevant memories
- **After each response** — captures new conversation turns
- **On session start** — injects your profile and memory index
- **Before compaction and on session end** — commits pending messages
- **For each subagent** — assigns an isolated memory session

All write operations run asynchronously, ensuring they never block your conversation.

<details>
<summary><b>Configuration</b></summary>

Configuration priority: Environment variables > `ovcli.conf` > `ov.conf` > Built-in defaults (`http://127.0.0.1:1933`, no authentication).

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

For multi-tenant deployments, configure `OPENVIKING_ACCOUNT` and `OPENVIKING_USER`. The complete list of environment variables is available in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration).

</details>

## Statusline

The plugin renders an OpenViking status indicator beneath your Claude Code input box, allowing you to check connection health, recall count, capture progress, and session state at a glance. See [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/STATUSLINE.md) for a complete glossary of segments and personalization recipes.

## Troubleshooting

| Issue | Cause | Solution |
|---------|-------|-----|
| Plugin is not activating | Missing `ov.conf` or `ovcli.conf` | Run the [installer](#install), or set `OPENVIKING_MEMORY_ENABLED=1` along with the URL/API_KEY environment variables |
| Hooks fire but recall is empty | Server is not running or the URL is incorrect | Check server health: `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP tools hit `127.0.0.1` instead of the remote server | Missing function wrapper | Ensure `type claude` outputs "shell function"; see [Manual setup](#install) |
| `type claude` shows a path instead of a shell function (wrapper inactive) | The rc wasn't `source`d after install, or you launched from a terminal that didn't load it | Run `source ~/.zshrc` (or `~/.bashrc` on bash), or open a new terminal |
| Launching via an alias (e.g. `cc`) injects no credentials | The alias *name* was listed in `OPENVIKING_CC_WRAP_EXTRA` (alias names are skipped), or the alias's target command isn't wrapped | Wrap the command the alias points to, not the alias: `alias cc=claude` needs nothing; for `alias cc=claude-custom`, add `claude-custom` |
| Remote auth 401 / 403 | Incorrect API key or missing tenant headers | Verify `OPENVIKING_API_KEY`; for multi-tenant setups, also check `OPENVIKING_ACCOUNT` and `OPENVIKING_USER` |

## See also

- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — Motivation, architecture overview, and demo
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — Full environment variable tables, hook details, and architecture diagrams
- [MCP Clients](./06-mcp-clients.md) — Information on MCP tool parameters and other clients
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup instructions
