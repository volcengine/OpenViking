Add persistent, cross-session memory to [Codex](https://developers.openai.com/codex). Install it once, and the plugin will automatically recall memories before every user prompt, capture updates after each turn, and commit changes before compaction. It also connects Codex to OpenViking's `/mcp` endpoint, allowing the model to directly invoke tools like search and store.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: motivation and demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Step 1: Install

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
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/codex-memory-plugin/tos-install.sh)
```

The installer checks dependencies, configures the OpenViking connection, and registers the plugin. Each step is idempotent, meaning it is safe to rerun the script at any time.

After installation, activate the `codex` wrapper in your current terminal (or simply open a new terminal window):

```bash
source ~/.openviking/openviking-repo/examples/codex-memory-plugin/setup-helper/wrapper.sh
codex              # approve hooks once via /hooks on first launch
```

> Launch Codex through a custom command? A wrapper script like `codex-custom`, or a multi-word launcher (a base command plus a sub-command) — list it at the installer's "Extra launch commands" step (or pass `OPENVIKING_CODEX_WRAP_EXTRA='codex-custom'`) to inject credentials there too.

<details>
<summary><b>Manual installation</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, and the `codex_hooks` feature enabled.

1. **Shell function wrapper** - Append a `codex()` function to your shell configuration file (e.g., `.bashrc` or `.zshrc`). Each invocation injects OpenViking environment variables from `ovcli.conf`. Refer to the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) for the complete function.

2. **Install the plugin** - Register the local marketplace and enable the plugin. See `setup-helper/install.sh` for the exact commands required.

3. **Render placeholders** - Placeholders in `.mcp.json` and `hooks.json` must be replaced with absolute paths or values when copied into the Codex cache. The automated installer handles this for you.

   Codex App stores the installed plugin under:

   - macOS/Linux: `~/.codex/plugins/cache/openviking/openviking-memory/<version>/`
   - Windows: `%USERPROFILE%\.codex\plugins\cache\openviking\openviking-memory\<version>\`

   After changing `~/.openviking/ovcli.conf`, re-render the cached MCP config.
   On Windows:

   ```powershell
   $PluginCache = Get-ChildItem "$env:USERPROFILE\.codex\plugins\cache\openviking\openviking-memory" -Directory |
     Sort-Object LastWriteTime -Descending |
     Select-Object -First 1
   node "$($PluginCache.FullName)\scripts\ov-credentials.mjs" sync-mcp "$($PluginCache.FullName)\.mcp.json"
   Get-Content -Raw "$($PluginCache.FullName)\.mcp.json"
   ```

   You can ask Codex to do it with this prompt:

   ```text
   Fix my installed OpenViking Memory Codex plugin MCP config.
   Locate the newest openviking-memory plugin cache under CODEX_HOME, or ~/.codex if unset
   (Windows: %CODEX_HOME%, or %USERPROFILE%\.codex if unset). Use ~/.openviking/ovcli.conf
   to run scripts/ov-credentials.mjs sync-mcp against the cached .mcp.json. Do not write
   the API key value into .mcp.json; show the redacted result and remind me to restart Codex.
   ```

</details>

## Step 2: Verify

```bash
type codex         # Expected: codex is a shell function
```

> If the previous command printed a path instead of `shell function`, the wrapper isn't active yet. Re-source it (or open a new terminal) before launching, otherwise codex starts without `OPENVIKING_API_KEY` and reports `MCP server is not logged in`.

Inside Codex, the plugin will now recall memory before every prompt. You can set `OPENVIKING_DEBUG=1` to log events to `~/.openviking/logs/codex-hooks.log`.

## How it works

The plugin hooks into the Codex lifecycle. It searches OpenViking and injects relevant memories before every user prompt (`UserPromptSubmit`), appends new conversation turns to the session after each response (`Stop`), and completes and commits the full transcript before compaction (`PreCompact`) to ensure the memory extractor has complete context. When a new session starts, it also cleans up orphaned sessions from previous runs.

> **Known limitation**: Codex does not trigger hooks on `SIGTERM`, `Ctrl+C`, or `/exit`. Orphaned sessions are reclaimed during the next `SessionStart` using either the idle TTL cleanup window (30 minutes) or the active-window heuristic.

<details>
<summary><b>Configuration</b></summary>

Configuration priority: environment variables > `ovcli.conf` > `ov.conf` > built-in defaults (`http://127.0.0.1:1933`, no auth).

| Environment variable | Default | Description |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | - | Full server URL |
| `OPENVIKING_API_KEY` | - | API key sent as `Authorization: Bearer` |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart active-window threshold |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle TTL cleanup threshold |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/codex-hooks.log` |

For tuning options such as `OPENVIKING_RECALL_LIMIT` and `OPENVIKING_CAPTURE_ASSISTANT_TURNS`, see the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin).

</details>

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MCP server is not logged in` | `OPENVIKING_API_KEY` is missing from the startup environment | Confirm the `codex()` function has been sourced and `ovcli.conf` contains an `api_key` |
| `type codex` shows a path instead of a shell function (wrapper inactive) | The rc wasn't `source`d after install, or you launched from a terminal that didn't load it | Run `source ~/.zshrc` (or `~/.bashrc` on bash), or open a new terminal |
| Launching via an alias (e.g. `cx`) injects no credentials | The alias *name* was listed in `OPENVIKING_CODEX_WRAP_EXTRA` (alias names are skipped), or the alias's target command isn't wrapped | Wrap the command the alias points to, not the alias: `alias cx=codex` needs nothing; for `alias cx=codex-custom`, add `codex-custom` |
| `4 hooks need review` | First-launch security approval is required | Type `/hooks` in Codex and approve them |
| `hook (failed) exited with code 1` after approval | Placeholders in the cache were not properly rendered | Rerun the one-line install script |
| Recall is empty | Server is unreachable or the URL is incorrect | Run `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` to test the connection |
| Hooks get 401 while MCP works, or vice versa | Environment variables and `ovcli.conf` are out of sync | Hooks re-read `ovcli.conf` each time; MCP reads the environment at startup. Restart Codex after making changes. |

## Reference docs

- [Blog: OpenViking for Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) - Why and how to add long-term memory to your coding agent.
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) - Comprehensive list of environment variables and the architecture diagram.
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) - Details on the commit decision tree.
