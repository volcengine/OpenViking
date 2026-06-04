Add persistent cross-session memory to [Codex](https://developers.openai.com/codex). Install once: the plugin automatically recalls memory before every user prompt, captures updates after each turn, and commits before compaction. It also connects Codex to OpenViking's `/mcp` endpoint so the model can directly call tools such as search and store.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: motivation and demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Step 1: Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer checks dependencies, configures the OpenViking connection, and registers the plugin. Each step is idempotent, so it is safe to rerun.

After installation:

```bash
source ~/.zshrc    # or ~/.bashrc
codex              # approve hooks once via /hooks on first launch
```

<details>
<summary><b>Manual installation</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, and the `codex_hooks` feature enabled.

1. **Shell function wrapper** - Append a `codex()` function to your shell rc file. Each invocation injects OpenViking environment variables from `ovcli.conf`. See the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) for the full function.

2. **Install the plugin** - Register the local marketplace and enable the plugin. See `setup-helper/install.sh` for the exact commands.

3. **Render placeholders** - Placeholders in `.mcp.json` and `hooks.json` must be replaced with absolute values when copied into the Codex cache. The installer handles this automatically.

</details>

## Step 2: Verify

```bash
type codex         # Expected: codex is a shell function
```

Inside Codex, the plugin recalls memory before every prompt. Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`.

## How it works

The plugin hooks into the Codex lifecycle: it searches OpenViking and injects relevant memory before every user prompt (`UserPromptSubmit`), appends new conversation turns to the session after each response (`Stop`), and completes plus commits the full transcript before compaction (`PreCompact`) so the memory extractor sees the complete context. On new session start, it also cleans up orphan sessions from previous runs.

> **Known gap**: Codex does not trigger hooks on `SIGTERM`, `Ctrl+C`, or `/exit`. Orphan sessions are reclaimed by the next `SessionStart` using the idle TTL cleanup window (30 minutes) or the active-window heuristic.

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
|------|------|------|
| `MCP server is not logged in` | `OPENVIKING_API_KEY` is not in the startup environment | Confirm the `codex()` function has been sourced and `ovcli.conf` has `api_key` |
| `4 hooks need review` | First-launch security approval | Type `/hooks` in Codex and approve |
| `hook (failed) exited with code 1` after approval | Placeholders in the cache were not rendered | Rerun the one-line installer |
| Recall is empty | Server is unreachable or URL is wrong | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Hooks get 401 while MCP works, or vice versa | Environment and `ovcli.conf` differ | Hooks reread `ovcli.conf` each time; MCP reads env at startup. Restart Codex after changes. |

## Reference docs

- [Blog: OpenViking for Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) - Why and how to add long-term memory to your coding agent
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) - Full environment variables and architecture diagram
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) - Commit decision tree
