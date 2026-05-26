# Codex Memory Plugin

Give [Codex](https://developers.openai.com/codex) persistent memory across sessions. Install once — memories are recalled on every prompt, captured after each turn, and committed before compaction. The plugin also wires Codex up to OpenViking's `/mcp` endpoint so the model can search, store, and manage memories directly.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

The installer checks dependencies, configures the OpenViking connection, and registers the plugin. Every step is idempotent.

After install:

```bash
source ~/.zshrc    # or ~/.bashrc
codex              # first run: approve hooks once when prompted via /hooks
```

<details>
<summary><b>Manual setup</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, `codex_hooks` feature enabled.

1. **Shell function wrapper** — append a `codex()` function to your shell rc that injects OpenViking env vars from `ovcli.conf`. See the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) for the full function.

2. **Plugin install** — register a local marketplace and enable the plugin. See `setup-helper/install.sh` for the exact commands.

3. **Placeholder rendering** — the checked-in `.mcp.json` and `hooks.json` contain placeholders that must be substituted when copied to Codex's plugin cache. The installer does this automatically.

</details>

## Verify

```bash
type codex         # expect: codex is a shell function
```

Inside Codex, the plugin should recall memories on each prompt. Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`.

## How it works

The plugin hooks into Codex's lifecycle: it searches OpenViking and injects relevant memories before every prompt (`UserPromptSubmit`), appends new turns to the session after each response (`Stop`), and commits the full transcript before compaction (`PreCompact`) so memory extraction runs against the complete conversation. On fresh session start, it also cleans up orphaned sessions from prior runs.

> **Known gap**: Codex fires no hook on `SIGTERM` / `Ctrl+C` / `/exit`. Orphaned sessions are recovered by the next `SessionStart`'s idle-TTL sweep (30 min) or active-window heuristic.

<details>
<summary><b>Configuration</b></summary>

Config priority: env vars > `ovcli.conf` > `ov.conf` > built-in defaults (`http://127.0.0.1:1933`, no auth).

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL |
| `OPENVIKING_API_KEY` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart active-window threshold |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle-TTL sweep threshold |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/codex-hooks.log` |

Tuning knobs (`OPENVIKING_RECALL_LIMIT`, `OPENVIKING_CAPTURE_ASSISTANT_TURNS`, etc.) are documented in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin).

</details>

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `MCP server is not logged in` | `OPENVIKING_API_KEY` not in env at launch | Ensure `codex()` shell function is sourced and `ovcli.conf` has `api_key` |
| `4 hooks need review` | First-launch security review | Run `/hooks` in Codex and approve |
| `hook (failed) exited with code 1` | Stale placeholder in plugin cache | Re-run the one-line installer |
| Recall returns nothing | Server unreachable or wrong URL | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Hook 401 but MCP works (or vice versa) | env vs ovcli.conf mismatch | Hooks re-read ovcli.conf every fire; MCP reads env at startup. Restart codex. |

## See also

- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — motivation, architecture overview, and demo
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — full env-var list, architecture diagram
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — commit decision tree
- [MCP Clients](./06-mcp-clients.md) — MCP protocol, tools, and other clients
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup
