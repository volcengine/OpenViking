# Community Plugins

Community-maintained integrations for various agent runtimes. Each differs in target platform, integration depth, and maintenance status — check the linked README before adopting.

## ZCode memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

Lifecycle hooks, the OpenViking MCP server, and 5 skills give ZCode cross-project, cross-session memory. Shared behaviour (capture, commits, recall, offline queue) is described in the [Capability Reference](./16-capability-reference.md).

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and ZCode.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

Older installers use **Custom URL** for BytePlus.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer detects `~/.zcode/` or the `zcode` binary, installs the runtime under `~/.openviking/agent-integrations/zcode/`, merges hooks and MCP configuration into `~/.zcode/cli/config.json`, and installs `openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, and `ov-memory-doctor` into `~/.zcode/skills/<name>`. A skill you edited, or an existing unmanaged directory of the same name, is preserved on upgrade and uninstall.

### Verify

After restarting ZCode, verify that:

- `~/.zcode/cli/config.json` contains `hooks.enabled: true`, OpenViking entries under `hooks.events`, and `mcp.servers.openviking`.
- `~/.zcode/skills/` holds the 5 skill directories, each with a `SKILL.md`.
- `OPENVIKING_DEBUG=1` produces diagnostics in `~/.openviking/logs/zcode-hooks.log`.

### Host-specific behaviour

- ZCode registers `SessionStart`, `UserPromptSubmit`, `Stop`, and a `PreToolUse` URI guard on `Read|Glob|Grep`. It has no compaction, session-end, or subagent hooks; subagents are not captured.
- ZCode has no session-end event, so the plugin does not commit the last stretch of a session. Enable `memory.session_auto_commit.enabled` on the server so the idle commit covers it; the plugin already sends the idle time with the session policy.
- Capture reads the rollout file `~/.zcode/cli/rollout/model-io-<session id>.jsonl`. When that file is missing, it falls back to the prompt and reply carried by the hook payloads.

| Symptom | Cause | Fix |
|---------|-------|-----|
| Hooks not firing | Hook configuration is disabled or stale | Re-run the installer and restart ZCode |
| MCP tools not appearing | The MCP proxy failed to start | Check the absolute `mcp.servers.openviking` command in `~/.zcode/cli/config.json` |
| Duplicate captures | An older installation left duplicate hook entries | Run `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness zcode`, then reinstall |

Once installed, start a new ZCode session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Kimi Code memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The Kimi Code integration is a native managed plugin: lifecycle hooks, the OpenViking MCP server, and the 5 skills (`openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, `ov-memory-doctor`) under the plugin root's `skills/`, without editing Kimi's legacy config files. Shared behaviour is described in the [Capability Reference](./16-capability-reference.md).

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and Kimi Code CLI.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

Older installers use **Custom URL** for BytePlus.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer assembles a self-contained runtime under `$KIMI_CODE_HOME/plugins/managed/openviking-memory/` (default Kimi home: `~/.kimi-code/`), then updates only the `openviking-memory` entry in `plugins/installed.json`. Existing plugin records are preserved. Re-run the same command to upgrade. To remove only this plugin:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness kimicode
```

### Verify

Check that `plugins/installed.json` under the Kimi home has an `openviking-memory` entry, then start a new Kimi Code session, tell it a test preference, and ask about it in a later session after memory extraction completes.

### Host-specific behaviour

- Kimi registers `SessionStart`, `UserPromptSubmit`, `Stop`, `PreCompact`, `SessionEnd`, `Interrupt`, and a `PreToolUse` URI guard on Read/Glob/Grep. Capture reads the session's `wire.jsonl`.
- Only `UserPromptSubmit` output reaches the model, so the profile is injected at the first prompt rather than at session start. Context is printed as plain text on stdout.
- Kimi fires `Interrupt`, not `Stop`, after a cancelled turn. `Interrupt` captures and commits like a session end, synchronously, within a 2-second budget for all its OpenViking requests.
- Session start replays at most 2 offline-queue entries.
- The MCP server cannot tell which session calls it, so `session_history` needs the session passed explicitly.
- Subagents are not captured.

The verified host contract and version are recorded in [`hosts/kimicode/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/kimicode/DESIGN.md).

Once installed, start a new Kimi Code session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Antigravity memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The Antigravity integration serves the Antigravity desktop app and the `agy` CLI, which load the same plugins from `~/.gemini/config/plugins/`. The plugin carries lifecycle hooks, the OpenViking MCP server, and 5 skills (`openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, `ov-memory-doctor`), each also invocable as `/<name>`. Shared behaviour is described in the [Capability Reference](./16-capability-reference.md).

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and the Antigravity app or `agy`.

This noninteractive command uses the connection saved in `~/.openviking/ovcli.conf`. Configure it first with `ov config`: select **OpenViking Managed Service**, then **Volcengine** (service API key) or **BytePlus** (enter your API key). See [BytePlus setup](../guides/27-byteplus-setup.md#connect-the-cli).

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --yes --harness antigravity
```

The installer writes the plugin to `~/.gemini/config/plugins/openviking-memory` and its runtime to `~/.openviking/agent-integrations/antigravity`. If a directory of that name exists and OpenViking did not write it, the installer leaves it in place and stops. The plugin directory holds `plugin.json`, `hooks.json`, `mcp_config.json`, and `skills/`. If `~/.gemini/config/mcp_config.json` already defines an MCP server named `openviking`, the installer keeps yours and does not write the plugin's `mcp_config.json`, so no tool appears twice.

To uninstall:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness antigravity
```

### Verify

Restart the Antigravity app or start a new `agy` session, then verify that:

- the `agy` log under `~/.gemini/antigravity-cli/log/` reports `loaded 1 named hooks`;
- the plugin directory contains `skills/openviking-memory/SKILL.md`, and `mcp_config.json` unless your own configuration already has an `openviking` server.

### Host-specific behaviour

- Antigravity registers `SessionStart`, `PreInvocation`, `Stop`, and a `PreToolUse` URI guard. Its `hooks.json` has its own format (each top-level key names a hook, context goes back as `injectSteps` system messages), so a Claude Code `hooks.json` cannot be reused.
- Antigravity runs `PreInvocation` before every model call, including each call in a tool loop and each background wakeup. Recall runs once per turn, on the first model call.
- The URI guard denies file tool calls whose location argument is a `viking://` URI. Shell commands (`run_command`) are not checked.
- Antigravity has no session-end or compaction hook, so the plugin does not commit the last stretch of a session. Enable `memory.session_auto_commit.enabled` on the server so the idle commit covers it; the plugin already sends the idle time with the session policy.
- Subagents are not captured.

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
