Add persistent, cross-session memory to [Codex](https://developers.openai.com/codex). Install it once, and the plugin will automatically recall memories before every user prompt, capture updates after each turn, and commit changes before compaction. It also connects Codex to OpenViking's `/mcp` endpoint, allowing the model to directly invoke tools like search and store.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: motivation and demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Step 1: Install

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness codex --dist tos
```

Claude Code and Codex share this one installer. It asks for your language (English/中文) and OpenViking credentials; each step is idempotent, so it is safe to rerun. On the TOS channel Codex installs from a TOS-hosted git repo and can update later with `codex plugin marketplace upgrade openviking`.

No shell wrapper is needed anymore — the plugin ships a stdio MCP proxy that reads `~/.openviking/ovcli.conf` at runtime. After installing:

```bash
codex              # approve hooks once via /hooks on first launch
```

<details>
<summary><b>Manual installation</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, and the `plugin_hooks` feature enabled.

1. **Configure the connection** - write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or run the bundled wizard `node <plugin-dir>/scripts/setup.mjs` after installing.

2. **Install the plugin** from the remote marketplace (needs GitHub access):

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   Then enable plugin hooks in `~/.codex/config.toml` if your build doesn't already: `[features]` → `plugin_hooks = true`.

</details>

## Step 2: Verify

Launch `codex`; the plugin will now recall memory before every prompt. You can set `OPENVIKING_DEBUG=1` to log events to `~/.openviking/logs/codex-hooks.log`.

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
| MCP tool calls fail with an auth error | `ovcli.conf` has no valid `api_key` for an authenticated server | Fix `ovcli.conf` (or run `node <plugin-dir>/scripts/setup.mjs`) and restart Codex |
| MCP tool calls fail with a connection error | Server is unreachable or the URL is incorrect | Run `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` to test the connection |
| `4 hooks need review` | First-launch security approval is required | Type `/hooks` in Codex and approve them |
| Plugin still targets an old server after `ov config switch` | Codex keeps the proxy process from the previous session | Restart Codex; the stdio proxy resolves credentials at startup |

## Reference docs

- [Blog: OpenViking for Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) - Why and how to add long-term memory to your coding agent.
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) - Comprehensive list of environment variables and the architecture diagram.
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) - Details on the commit decision tree.
