# Community Plugins

Community-maintained integrations for various agent runtimes. Each differs in target platform, integration depth, and maintenance status — check the linked README before adopting.

## ZCode memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The ZCode community integration adds cross-project, cross-session memory through config-driven lifecycle hooks and an OpenViking MCP server:

- **SessionStart** injects the user profile.
- **UserPromptSubmit** recalls relevant memories.
- **PreToolUse** redirects direct `viking://` reads to MCP tools.
- **Stop** captures unseen rollout turns in a detached worker, then commits the OpenViking session.

ZCode does not expose `PreCompact`, `SessionEnd`, or subagent lifecycle hooks. The adapter therefore commits on `Stop`, uses ZCode rollout files as the authoritative incremental transcript, and falls back to hook stdin only when no rollout file is available.

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and ZCode.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer detects `~/.zcode/` or the `zcode` binary, installs the runtime under `~/.openviking/agent-integrations/zcode/`, and merges hooks and MCP configuration into `~/.zcode/cli/config.json`.

After restarting ZCode, verify that:

- `~/.zcode/cli/config.json` contains `hooks.enabled: true`, OpenViking entries under `hooks.events`, and `mcp.servers.openviking`.
- `OPENVIKING_DEBUG=1` produces diagnostics in `~/.openviking/logs/zcode-hooks.log`.

| Symptom | Cause | Fix |
|---------|-------|-----|
| Hooks not firing | Hook configuration is disabled or stale | Re-run the installer and restart ZCode |
| Recall returns nothing | OpenViking is unavailable or has not extracted memories yet | Check `curl http://127.0.0.1:1933/health` and wait for extraction |
| MCP tools not appearing | The MCP proxy failed to start | Check the absolute `mcp.servers.openviking` command in `~/.zcode/cli/config.json` |
| Duplicate captures | An older installation left duplicate hook entries | Run `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness zcode`, then reinstall |

Implementation details and currently verified ZCode assumptions are documented in the plugin's [README](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) and [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/DESIGN.md).

## MiniMax Code memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The MiniMax Code integration adds cross-project, cross-session memory through lifecycle hooks and an OpenViking MCP server:

- **SessionStart** injects the user profile.
- **UserPromptSubmit** recalls relevant memories.
- **Stop** captures the unseen turns in a detached worker, then commits the OpenViking session.
- **PreCompact** captures and commits before compaction.
- **SessionEnd** commits what is left.
- **PreToolUse** redirects direct `viking://` reads to the MCP tools.

## Kimi Code memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The Kimi Code integration is a native managed plugin. It reuses the shared
OpenViking hook runtime and adds only Kimi-specific event mapping, wire
transcript decoding, output formatting, and commit policy:

- **UserPromptSubmit** recalls memory and prints raw context text for Kimi to inject.
- **PreToolUse** blocks direct Read/Glob/Grep access to `viking://` URIs.
- **Stop**, **PreCompact**, and **SessionEnd** capture new `wire.jsonl` turns; **Interrupt** runs the same capture synchronously within one two-second OpenViking request budget.
- The plugin manifest exposes the OpenViking MCP server without editing Kimi's legacy config files.

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and Kimi Code CLI.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer assembles a self-contained runtime under
`$KIMI_CODE_HOME/plugins/managed/openviking-memory/` (default Kimi home:
`~/.kimi-code/`), then updates only the `openviking-memory` entry in
`plugins/installed.json`. Existing plugin records are preserved. Re-run the
same command to upgrade. To remove only this plugin:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness kimicode
```

The verified host contract and version are recorded in
[`hosts/kimicode/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/kimicode/DESIGN.md).

## CodeBuddy Code memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The CodeBuddy Code integration is an `agent-hook-plugin` host adapter. It reuses the shared OpenViking hook runtime and adds only CodeBuddy-specific event mapping, transcript decoding, output envelopes, and commit policy:

- **SessionStart** injects the user profile and replays anything the offline queue is holding.
- **UserPromptSubmit** recalls relevant memories and injects them as an `<openviking-context>` block in `additionalContext`. Host-generated continuation turns are skipped, so a turn the host produced on its own never triggers a search.
- **PreToolUse** denies reading or writing a `viking://` URI as a local path and points the Agent at the OpenViking MCP tools. Shell commands are not checked, so `ov` invocations and literal `viking://` arguments still work.
- **Stop**, **PreCompact**, and **SessionEnd** capture new transcript turns incrementally in a detached worker. `Stop` commits the OpenViking session every 8 captured messages (`commitTurnThreshold`); `PreCompact` and `SessionEnd` commit when they capture new messages.

The plugin manifest also exposes the OpenViking MCP server as a stdio proxy onto the same resolved configuration, and ships the `openviking-memory`, `openviking-skills`, and `ov-experience-memory` skills. Hooks and MCP read the same credentials, so they cannot disagree about the server, account, or user. Subagent capture is not supported in v1, as on the other community adapters: the host fires `SubagentStart`/`SubagentStop`, but the shared dispatcher has no subagent stage yet.

### Install

Prerequisites: CodeBuddy Code, Node.js 18+, and a running OpenViking server.

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --yes --harness codebuddy
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer assembles a self-contained plugin under `~/.openviking/agent-integrations/codebuddy/marketplace/openviking-memory/`, with the shared hook runtime beside the adapters, registers it as a local marketplace named `openviking`, and installs it through the CodeBuddy CLI. To remove only this integration:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness codebuddy
```

The plugin reads its connection from `~/.openviking/ovcli.conf`; `OPENVIKING_URL` and `OPENVIKING_API_KEY` override that file, and per-plugin settings live under `plugin.codebuddy` (see [Plugin Settings](../configuration/02-client.md#plugin-settings)). Start a new CodeBuddy session after installing (`/reload-plugins` applies it to a running one), and confirm with `codebuddy plugin list` that `openviking-memory@openviking` is enabled.

### Behaviour that follows from the host

`UserPromptSubmit` and `SessionEnd` fire **only in the interactive TUI** — headless runs (`-p`, `--input-format stream-json`) never emit them, so a headless session captures on `Stop` but never sends the final commit. `SessionStart` carries no `cwd` and can be delivered twice per session, so the session-start hook is idempotent and falls back to the process working directory. A hook that overruns its timeout is killed, but its child processes are not, which is what lets commits run in a detached worker after the hook process returns.

The verified host contract, the transcript format, and the adapter's v1 scope are recorded in [`hosts/codebuddy/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/codebuddy/DESIGN.md).

## AstrBot plugin

[AstrBot](https://github.com/AstrBotDevs/AstrBot) is a multi-platform IM bot framework supporting QQ, Telegram, Discord, Lark, and 20+ other platforms.

Source: [astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

Provides auto-capture of group/DM conversations, semantic recall before each LLM request, and configurable venue memory isolation.

**Install**: In AstrBot WebUI, search **OpenViking Memory** in the Plugin Marketplace; or install from URL: `https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

The current plugin requires AstrBot 4.23.1+ and a compatible OpenViking server. Use a User key for `global`; `venue` needs an Admin key to create users. Check the linked plugin README before migrating an older configuration.

**Key features**:

- Auto-recall and auto-capture via hooks — the model doesn't need to invoke tools
- `self_scope=global` shares one bot identity; `venue` creates a separate user per group/DM. People are represented as peers. Legacy `isolation_mode` values are mapped to these scopes.
- Automatic commit by message count, token threshold, or idle timeout
- Backfills platform message history on first venue encounter

## Open WebUI tool server

[Open WebUI](https://github.com/open-webui/open-webui) is a self-hosted AI chat interface.

Source: [examples/openwebui-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openwebui-plugin)

A standalone FastAPI server that exposes a curated subset of OpenViking endpoints as OpenAPI tools, so Open WebUI can call them as native tools. Setup and endpoint details are in the README.

## More examples

The [examples/](https://github.com/volcengine/OpenViking/tree/main/examples) directory also contains deployment and integration samples beyond agent plugins — Grafana dashboards, Kubernetes Helm charts, multi-tenant setups, snapshot workflows, and SDK snippets.

## See also

- [Capability Reference](./16-capability-reference.md)
