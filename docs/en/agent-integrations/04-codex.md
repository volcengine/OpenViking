# Codex Memory Plugin

Equip [Codex](https://developers.openai.com/codex) with persistent memory across sessions. Install it once, and memories will be automatically recalled with every prompt, captured after each turn, and committed before compaction. The plugin also connects Codex to OpenViking's `/mcp` endpoint, enabling the model to search, store, and manage memories directly.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: Motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer checks dependencies, configures the OpenViking connection, and registers the plugin. Every step is idempotent.

In regions where GitHub is hard to reach, use the equivalent command below:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/codex-memory-plugin/tos-install.sh)
```

After installation, activate the `codex` wrapper in your current terminal (or open a new terminal window):

```bash
source ~/.openviking/openviking-repo/examples/codex-memory-plugin/setup-helper/wrapper.sh
codex              # First run: approve hooks once when prompted via /hooks
```

> Launch Codex through a custom command? A wrapper script like `codex-custom`, or a multi-word launcher (a base command plus a sub-command) — list it at the installer's "Extra launch commands" step (or pass `OPENVIKING_CODEX_WRAP_EXTRA='codex-custom'`) to inject credentials there too.

<details>
<summary><b>Manual setup</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, and the `codex_hooks` feature enabled.

1. **Shell function wrapper** — Append a `codex()` function to your shell profile (e.g., `.bashrc` or `.zshrc`) to inject OpenViking environment variables from `ovcli.conf`. See the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) for the full function.

2. **Plugin installation** — Register a local marketplace and enable the plugin. See `setup-helper/install.sh` for the exact commands.

3. **Placeholder rendering** — The provided `.mcp.json` and `hooks.json` files contain placeholders that must be substituted when copied to Codex's plugin cache. The automated installer handles this for you.

</details>

## Verify

```bash
type codex         # Expectation: codex is a shell function
```

> If the previous command printed a path instead of `shell function`, the wrapper isn't active yet. Re-source it (or open a new terminal) before launching, otherwise codex starts without `OPENVIKING_API_KEY` and reports `MCP server is not logged in`.

Once inside Codex, the plugin should seamlessly recall memories on every prompt. Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`.

## How it works

The plugin integrates with Codex's lifecycle by hooking into key events. It searches OpenViking and injects relevant memories before every prompt (`UserPromptSubmit`), appends new turns to the session after each response (`Stop`), and commits the full transcript before compaction (`PreCompact`) to ensure memory extraction processes the entire conversation. Upon starting a fresh session, it also cleans up any orphaned sessions from previous runs.

> **Known limitation**: Codex does not fire a hook upon `SIGTERM`, `Ctrl+C`, or `/exit`. Orphaned sessions are recovered during the next `SessionStart` via the idle-TTL sweep (30 minutes) or the active-window heuristic.

<details>
<summary><b>Configuration</b></summary>

Configuration priority: Environment variables > `ovcli.conf` > `ov.conf` > Built-in defaults (`http://127.0.0.1:1933`, no authentication).

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL |
| `OPENVIKING_API_KEY` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart active-window threshold |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle-TTL sweep threshold |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/codex-hooks.log` |

Additional tuning options (e.g., `OPENVIKING_RECALL_LIMIT`, `OPENVIKING_CAPTURE_ASSISTANT_TURNS`) are documented in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin).

</details>

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MCP server is not logged in` | `OPENVIKING_API_KEY` not in env at launch | Ensure the `codex()` shell function is sourced and `ovcli.conf` contains a valid `api_key`. |
| `type codex` shows a path instead of a shell function (wrapper inactive) | The rc wasn't `source`d after install, or you launched from a terminal that didn't load it | Run `source ~/.zshrc` (or `~/.bashrc` on bash), or open a new terminal |
| Launching via an alias (e.g. `cx`) injects no credentials | The alias *name* was listed in `OPENVIKING_CODEX_WRAP_EXTRA` (alias names are skipped), or the alias's target command isn't wrapped | Wrap the command the alias points to, not the alias: `alias cx=codex` needs nothing; for `alias cx=codex-custom`, add `codex-custom` |
| `4 hooks need review` | Security review on first launch | Run `/hooks` within Codex and approve the hooks. |
| `hook (failed) exited with code 1` | Stale placeholders in the plugin cache | Re-run the one-line installer. |
| Recall returns nothing | Server is unreachable or the URL is incorrect | Check the endpoint: `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Hook 401 but MCP works (or vice versa) | Mismatch between environment variables and `ovcli.conf` | Hooks re-read `ovcli.conf` on every execution, whereas MCP reads environment variables only at startup. Restart Codex to sync. |

## See also

- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — Motivation, architecture overview, and demo.
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — Full environment variable list and architecture diagram.
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — Commit decision tree.
- [MCP Clients](./06-mcp-clients.md) — MCP protocol, tools, and other clients.
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup instructions.
