# Kimi Code

Give [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code) cross-session memory. As a native managed plugin, hooks handle automatic recall, capture, and session commits; MCP tools let the model search, read, and manage memories.

Source: [examples/kimi-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/kimi-code-memory-plugin) | Host contract: [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/DESIGN.md)

## Install

Prerequisites: Kimi Code CLI ≥ 2.1.1 (verified on 2.2.0), Node.js ≥ 18, and a reachable OpenViking server (`127.0.0.1:1933` locally needs no credentials). You also need a running OpenViking server — see [Prerequisite for all integrations](./01-overview.md).

In the Kimi TUI, run:

```text
/plugins install <absolute local directory | zip URL>
```

The source can be the `examples/kimi-code-memory-plugin` directory in this repository (absolute path), or a zip of that directory with the manifest at the archive root. **No curl installer needed**. After installing, `/reload` or start a new session.

Note: kimi-code's GitHub URL install only supports repositories with the manifest at the repo root (no subdirectory support). This plugin lives under `examples/` in a monorepo, so the GitHub URL form does not apply — use the local directory or a zip.

The CLI manages the plugin for you: the profile and skill catalog are injected on the first prompt, and every prompt is recalled against — there is no hook-trust review step.

## Upgrade and uninstall

The CLI always runs the managed copy at `$KIMI_CODE_HOME/plugins/managed/openviking-memory/` (default Kimi home: `~/.kimi-code/`), never your source directory — editing the source has no effect until you reinstall.

```text
/plugins install <same source>          # upgrade: replace the managed copy
/plugins remove openviking-memory       # uninstall: hooks, MCP, and skills go with it
```

You can also inspect, enable, or remove the plugin in the `/plugins` manager. Upgrading and uninstalling only touch the plugin files and their registration in Kimi; server-side memories, credentials, and the `~/.openviking/kimicode-plugin-state` state files are left alone.

## Verify

The plugin ships a diagnostic script:

```bash
node scripts/ov-memory-doctor.mjs --offline
```

It checks the installation record, configuration and connection, and recent activity. Set `OPENVIKING_DEBUG=1` to write event logs to `~/.openviking/logs/kimicode-hooks.log`.

Behavior check: start a new session, ask about something you mentioned earlier to confirm recall; after enough turns (8 by default) the conversation is committed into the `kc-<session-id>` session.

## How it works

The plugin handles memory at these Kimi Code lifecycle events:

| Kimi Code event | Plugin behavior |
| --- | --- |
| `UserPromptSubmit` | Retrieve and inject memories relevant to the current prompt. The profile and `<available-skills>` catalog are also injected on the first prompt through this event — `SessionStart` stdout is dropped by Kimi (issue #2873), and plain stdout text is the only channel Kimi reliably appends to context. |
| `Stop` | Incrementally decode the session's `wire.jsonl` and append new user/assistant/tool messages to the `kc-<session-id>` session; commits once the threshold (8 turns by default) is reached since the last commit. |
| `PreCompact` | Same incremental capture; commits before compaction by default. |
| `SessionEnd` / `Interrupt` | Same incremental capture; always commit when new messages were captured. |
| `SessionStart` | Bounded replay of previously failed pending messages (2 entries per run). |
| `PreToolUse` (`Read` / `Glob` / `Grep`) | Deny direct access to `viking://` URIs and point the model at the MCP tools. |

The MCP tools (`find`, `search`, `grep`, `read`, `remember`, `add_resource`, `add_skill`, and more) are exposed through the plugin's bundled stdio proxy; the bundled `openviking-memory`, `openviking-skills`, and `ov-experience-memory` skills teach the model when to use them. All hooks run synchronously with no detached background processes, so behavior is identical on Windows, macOS, and Linux.

> **Known limitation**: subagent conversations (`agents/<subagent>/wire.jsonl`) are not captured yet (v2).

## Configuration

Resolution chain: `OPENVIKING_*` env vars → workspace `.openviking/config*.json` → the local registry → `plugin.kimicode` in `ovcli.conf` → `plugin` → built-in defaults. Connection and credentials come from env vars or the active `ovcli.conf` (`ov config switch` flips both hooks and the MCP proxy).

| Env Var | Default | Description |
|---------|---------|-------------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | Full server URL |
| `OPENVIKING_API_KEY` | — | API key (sent as `Authorization: Bearer`) |
| `OPENVIKING_AUTO_RECALL` | on | Recall and inject profile/memories on every prompt |
| `OPENVIKING_AUTO_CAPTURE` | on | Incremental capture on `Stop`/`PreCompact`/`SessionEnd`/`Interrupt` |
| `OPENVIKING_COMMIT_TURN_THRESHOLD` | `8` | Captured turns between threshold commits |
| `OPENVIKING_AUTO_COMMIT_ON_COMPACT` | on | Commit before compaction |
| `OPENVIKING_RECALL_LIMIT` / `OPENVIKING_SCORE_THRESHOLD` | `10` / `0.35` | Recall count and score threshold |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | `actor` restricts retrieval to the current workspace peer |
| `OPENVIKING_PENDING_REPLAY_LIMIT` | `2` | Pending entries replayed per `SessionStart` |
| `OPENVIKING_DEBUG` | `false` | Write logs to `~/.openviking/logs/kimicode-hooks.log` |

Most of these knobs can also live in `ovcli.conf` under `plugin` — see [Plugin Settings](../configuration/02-client.md#plugin-settings). The full list is in the shared plugin's [`config-schema.mjs`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared). The plugin's state files live in `~/.openviking/kimicode-plugin-state`; relocate them with `OPENVIKING_KIMICODE_STATE_DIR`.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Installed plugin with no recall/capture | Plugin is disabled, or the session was not reloaded | Confirm `openviking-memory` is enabled in `/plugins`, then `/reload` or start a new session. |
| Source edits have no effect | The CLI always runs the managed copy, not your source directory | Reinstall with `/plugins install <same source>` and verify again. |
| MCP tool calls fail with an auth error | Missing or stale credentials | Fix `~/.openviking/ovcli.conf` or the `OPENVIKING_*` credential env vars, then start a new session. |
| MCP tool calls fail with a connection error | Server unreachable or the URL is wrong | Check the endpoint: `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Not sure what is wrong | — | Run `node scripts/ov-memory-doctor.mjs --offline`; set `OPENVIKING_DEBUG=1` and read `~/.openviking/logs/kimicode-hooks.log`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/README.md) — install, behavior, and configuration overview
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/DESIGN.md) — host contract and design record
- [MCP Clients](./06-mcp-clients.md) — MCP protocol, tools, and other clients
- [Community Plugins → Kimi Code](./08-community-plugins.md#kimi-code-memory-integration) — legacy curl installer path
