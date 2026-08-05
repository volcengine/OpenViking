# Codex Memory Plugin

Equip [Codex](https://developers.openai.com/codex) with persistent memory across sessions. Install it once, and your OpenViking profile and memory index are loaded at session start, relevant memories are recalled with every prompt, new turns are captured after each response, and sessions are committed before compaction. The plugin also connects Codex to OpenViking's `/mcp` endpoint, enabling the model to call tools such as `find`, `search`, `recall`, and `remember` directly.

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [Blog: Motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

Claude Code and Codex share one installer. It asks for your language (English/中文), which harnesses to install, the download source, and your OpenViking credentials; every step is idempotent.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

In regions where GitHub is hard to reach, run the same installer from the Volcengine TOS mirror (or pick "TOS mirror" at the download-source prompt). Codex installs from a TOS-hosted git repo and keeps remote update support:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

No shell wrapper is needed anymore — the plugin ships a stdio MCP proxy that reads `~/.openviking/ovcli.conf` (or `OPENVIKING_*` env vars) at runtime, same as the hooks. After installing:

```bash
codex              # First run: approve hooks once when prompted via /hooks
```

<details>
<summary><b>Manual setup</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, and the `plugin_hooks` feature enabled.

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or run the bundled wizard `node <plugin-dir>/scripts/setup.mjs` after installing.

2. **Install the plugin** from the remote marketplace:

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   Then enable plugin hooks in `~/.codex/config.toml` if your build doesn't already: `[features]` → `plugin_hooks = true`. Update later with `codex plugin marketplace upgrade openviking`.

</details>

## Verify

Launch `codex`; on the first prompt of a session, the `SessionStart` hook should load your profile, and the plugin should then recall relevant memories for every prompt. Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`.

## How it works

The plugin integrates with Codex's lifecycle by hooking into key events. On `SessionStart` (`startup`, `clear`, or `resume`), it injects `profile.md` plus URI and abstract indexes for `preferences/` and `entities/` through the same shared, CJK-aware profile builder used by the other coding-agent integrations. It then searches OpenViking and injects relevant memories before every prompt (`UserPromptSubmit`), appends new turns to the session after each response (`Stop`), and commits the full transcript before compaction (`PreCompact`) to ensure memory extraction processes the entire conversation. Upon starting a fresh session, it also cleans up any orphaned sessions from previous runs. A resumed session may combine the fixed profile block with its latest archive digest.

> **Known limitation**: Codex does not fire a hook upon `SIGTERM`, `Ctrl+C`, or `/exit`. Orphaned sessions are recovered during the next `SessionStart` via the idle-TTL sweep (30 minutes) or the active-window heuristic.

<details>
<summary><b>Configuration</b></summary>

Credential source: active `ovcli.conf` wins by default (`OPENVIKING_CLI_CONFIG_FILE` or `~/.openviking/ovcli.conf`), so `ov config switch <name>` changes hooks, MCP proxy, and child `ov` commands together on the next launch. Set `OPENVIKING_CREDENTIAL_SOURCE=env` only when you intentionally want env vars to override the CLI config. Without an ovcli config, env vars then `ov.conf` then built-in defaults are used.

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL |
| `OPENVIKING_API_KEY` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | Active CLI config to use for hooks, MCP, and child `ov` commands |
| `OPENVIKING_CREDENTIAL_SOURCE` | `auto` | Set `env` to force env-var credentials instead of active ovcli config |
| `OPENVIKING_NO_AUTO_INJECT` | `false` | Disable fixed session-start profile/background injection without disabling per-prompt recall |
| `OPENVIKING_PROFILE_TOKEN_BUDGET` | `10000` | CJK-aware token budget for `profile.md` plus `preferences/` and `entities/` indexes |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart active-window threshold |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart idle-TTL sweep threshold |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/codex-hooks.log` |

If recall latency matters most, see [Low-latency recall](./01-overview.md#low-latency-recall) for the environment-variable and `ovcli.conf` settings that disable query expansion and Codex's local result compression.

Additional tuning options (e.g., `OPENVIKING_RECALL_LIMIT`, `OPENVIKING_CAPTURE_ASSISTANT_TURNS`) are documented in the [plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin).

</details>

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| MCP tool calls fail with an auth error | The active ovcli config has no valid `api_key` for an authenticated server | Fix `~/.openviking/ovcli.conf` (or run `node <plugin-dir>/scripts/setup.mjs`) and restart Codex; the stdio proxy re-reads it on launch and after auth failures. |
| MCP tool calls fail with a connection error | Server unreachable or the URL is wrong | Check the endpoint: `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| `4 hooks need review` | Security review on first launch | Run `/hooks` within Codex and approve the hooks. |
| Plugin still targets an old server after `ov config switch` | Codex keeps the proxy process from the previous session | Restart Codex; the proxy resolves credentials at startup. |
| Hooks use one server, MCP another | `OPENVIKING_CREDENTIAL_SOURCE=env` set with stale env vars in one context | Unset it (ovcli.conf then drives both), or make the env vars consistent. |

## See also

- [Blog: OpenViking in Claude Code / Codex](https://blog.openviking.ai/post/openviking-coding-agent/) — Motivation, architecture overview, and demo.
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — Full environment variable list and architecture diagram.
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — Commit decision tree.
- [MCP Clients](./06-mcp-clients.md) — MCP protocol, tools, and other clients.
- [Deployment Guide → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` setup instructions.
