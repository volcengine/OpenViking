# Codex Memory Plugin

Equip [Codex](https://developers.openai.com/codex) with persistent memory across sessions. Install it once, and memories will be automatically recalled with every prompt, captured after each turn, and committed before compaction. The plugin also connects Codex to OpenViking's `/mcp` endpoint, enabling the model to search, store, and manage memories directly.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: Motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

### Codex marketplace

Use Codex's native marketplace commands for local or self-hosted OpenViking:

```bash
codex plugin marketplace add volcengine/OpenViking
codex plugin add openviking-memory@openviking
```

When adding from the Codex App UI, leave the sparse path empty. The marketplace
catalog lives at the repository root in `.agents/plugins/marketplace.json`.

### One-line installer

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

1. **Shell function wrapper** — Source the plugin wrapper from your shell profile (e.g., `.bashrc` or `.zshrc`) so Codex receives the active `ovcli.conf` credentials, MCP is re-rendered, and stale inherited credential env vars are stripped. See the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) for the snippet.

2. **Plugin installation** — Register a local marketplace and enable the plugin. See `setup-helper/install.sh` for the exact commands.

3. **Placeholder rendering** — The provided `.mcp.json` and `hooks.json` files contain placeholders that must be substituted when copied to Codex's plugin cache. The automated installer handles this for you.

   Codex App keeps the installed copy in its plugin cache:

   - macOS/Linux: `~/.codex/plugins/cache/openviking/openviking-memory/<version>/`
   - Windows: `%USERPROFILE%\.codex\plugins\cache\openviking\openviking-memory\<version>\`

   If you installed from the Codex marketplace and later changed
   `~/.openviking/ovcli.conf`, re-render the cached MCP config. On Windows:

   ```powershell
   $PluginCache = Get-ChildItem "$env:USERPROFILE\.codex\plugins\cache\openviking\openviking-memory" -Directory |
     Sort-Object LastWriteTime -Descending |
     Select-Object -First 1
   node "$($PluginCache.FullName)\scripts\ov-credentials.mjs" sync-mcp "$($PluginCache.FullName)\.mcp.json"
   Get-Content -Raw "$($PluginCache.FullName)\.mcp.json"
   ```

   The rendered `.mcp.json` should point at `/mcp` and use
   `bearer_token_env_var` instead of storing the secret:

   ```json
   {
     "mcpServers": {
       "openviking-memory": {
         "url": "http://localhost:1933/mcp",
         "bearer_token_env_var": "OPENVIKING_API_KEY",
         "env_http_headers": {
           "X-OpenViking-Account": "OPENVIKING_ACCOUNT",
           "X-OpenViking-User": "OPENVIKING_USER"
         }
       }
     }
   }
   ```

   Restart Codex after editing `.mcp.json` or changing environment variables.

   Copy this prompt into Codex if you want the agent to repair the installed
   cache for you:

   ```text
   Fix my installed OpenViking Memory Codex plugin MCP config.
   Locate the newest openviking-memory plugin cache under CODEX_HOME, or ~/.codex if unset
   (Windows: %CODEX_HOME%, or %USERPROFILE%\.codex if unset). Use ~/.openviking/ovcli.conf
   to run scripts/ov-credentials.mjs sync-mcp against the cached .mcp.json. Do not write
   the API key value into .mcp.json; show the redacted result and remind me to restart Codex.
   ```

</details>

## Verify

```bash
type codex         # Expectation: codex is a shell function
```

> If the previous command printed a path instead of `shell function`, the wrapper isn't active yet. Re-source it (or open a new terminal) before launching, otherwise Codex may start without the credentials needed by MCP.

Once inside Codex, the plugin should seamlessly recall memories on every prompt. Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`.

## How it works

The plugin integrates with Codex's lifecycle by hooking into key events. It searches OpenViking and injects relevant memories before every prompt (`UserPromptSubmit`), appends new turns to the session after each response (`Stop`), and commits the full transcript before compaction (`PreCompact`) to ensure memory extraction processes the entire conversation. Upon starting a fresh session, it also cleans up any orphaned sessions from previous runs.

> **Known limitation**: Codex does not fire a hook upon `SIGTERM`, `Ctrl+C`, or `/exit`. Orphaned sessions are recovered during the next `SessionStart` via the idle-TTL sweep (30 minutes) or the active-window heuristic.

<details>
<summary><b>Configuration</b></summary>

Credential source: active `ovcli.conf` wins by default (`OPENVIKING_CLI_CONFIG_FILE` or `~/.openviking/ovcli.conf`), so `ov config switch <name>` changes hooks, MCP, and child `ov` commands together on the next launch. Set `OPENVIKING_CREDENTIAL_SOURCE=env` only when you intentionally want env vars to override the CLI config; the wrapper then writes a mode-0600 runtime ovcli config under `~/.openviking/codex-plugin-state/` so child `ov` commands still match. Without an ovcli config, env vars then `ov.conf` then built-in defaults are used.

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL |
| `OPENVIKING_API_KEY` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | Active CLI config to use for hooks, MCP, and child `ov` commands |
| `OPENVIKING_CREDENTIAL_SOURCE` | `auto` | Set `env` to force env-var credentials instead of active ovcli config |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart active-window threshold |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle-TTL sweep threshold |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/codex-hooks.log` |

Additional tuning options (e.g., `OPENVIKING_RECALL_LIMIT`, `OPENVIKING_CAPTURE_ASSISTANT_TURNS`) are documented in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin).

</details>

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MCP server is not logged in` | Credentials were not injected at Codex launch, or the active ovcli config has no `api_key` for an authenticated server | Ensure the `codex()` shell function is sourced and `ovcli.conf` contains a valid `api_key`. |
| `type codex` shows a path instead of a shell function (wrapper inactive) | The rc wasn't `source`d after install, or you launched from a terminal that didn't load it | Run `source ~/.zshrc` (or `~/.bashrc` on bash), or open a new terminal |
| Launching via an alias (e.g. `cx`) injects no credentials | The alias *name* was listed in `OPENVIKING_CODEX_WRAP_EXTRA` (alias names are skipped), or the alias's target command isn't wrapped | Wrap the command the alias points to, not the alias: `alias cx=codex` needs nothing; for `alias cx=codex-custom`, add `codex-custom` |
| `4 hooks need review` | Security review on first launch | Run `/hooks` within Codex and approve the hooks. |
| `hook (failed) exited with code 1` | Stale placeholders in the plugin cache | Re-run the one-line installer. |
| Recall returns nothing | Server is unreachable or the URL is incorrect | Check the endpoint: `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Hook 401 but MCP works (or vice versa) | Cached MCP config or launch env is stale | Restart Codex through the wrapper. If you intentionally use env credentials, set `OPENVIKING_CREDENTIAL_SOURCE=env`; otherwise use `ov config switch <name>` or `OPENVIKING_CLI_CONFIG_FILE`. |

## See also

- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — Motivation, architecture overview, and demo.
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — Full environment variable list and architecture diagram.
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — Commit decision tree.
- [MCP Clients](./06-mcp-clients.md) — MCP protocol, tools, and other clients.
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup instructions.
