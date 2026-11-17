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

Once installed, start a new ZCode session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

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

Once installed, start a new Kimi Code session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Antigravity memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The Antigravity integration serves the Antigravity desktop app and the `agy` CLI, which load the same plugins from `~/.gemini/config/plugins/`:

- **SessionStart** injects the user profile.
- **PreInvocation** recalls relevant memories on the first model call of each turn. Antigravity runs this hook before every model call, so later calls in a tool loop skip recall.
- **Stop** captures the finished steps of the conversation transcript in a detached worker, then commits the OpenViking session.

Antigravity hooks use their own `hooks.json` format: each top-level key names a hook, and context is returned as `injectSteps` system messages. A Claude Code `hooks.json` therefore cannot be reused.

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and the Antigravity app or `agy`.

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --yes --harness antigravity
```

The installer writes the plugin to `~/.gemini/config/plugins/openviking-memory` and its runtime to `~/.openviking/agent-integrations/antigravity`. If a directory of that name exists and OpenViking did not write it, the installer leaves it in place and stops. Connection settings come from `~/.openviking/ovcli.conf` as for the other integrations. The installer does not add an MCP server; add the OpenViking MCP server in `~/.gemini/config/mcp_config.json` if you want the agent to search memory itself.

Restart the Antigravity app or start a new `agy` session, then verify that:

- the `agy` log under `~/.gemini/antigravity-cli/log/` reports `loaded 1 named hooks`;
- after a turn, `~/.openviking/hook-state/antigravity/<conversation-id>.json` records `lastStep`.

To uninstall:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness antigravity
```

## Kimi Code memory plugin, standalone distribution (2.x, community)

Source: [examples/kimi-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/kimi-code-memory-plugin)

This is a community-maintained implementation for **Kimi Code 2.6.2**, in the
same category as the `agent-hook-plugin` Kimi integration above. It does not
modify or replace that adapter; the adapter remains the legacy path for Kimi
Code CLI 0.43.x and keeps its own code and documentation.

Both are Kimi native plugins loaded from the host's managed plugin directory.
What differs is distribution: the adapter above is assembled by OpenViking's
installer, while this directory is self-contained and can be installed directly.

- Six hooks: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`, `PreCompact`, `SessionEnd`. `Interrupt` is not registered.
- `PreToolUse` denies `viking://` paths on Read, Glob, Grep, Edit, Write and ReadMediaFile. Shell commands are not intercepted.
- Capture reads each agent's `wire.jsonl`, projects the active branch, and gives every subagent its own OpenViking session.
- Install with the host's own plugin manager; Node.js 22.19+ must be on the host's `PATH`:

```text
/plugins install <path to examples/kimi-code-memory-plugin>
```

After a release on the main repository, its ZIP installs with one command (tags
follow the `<package>@<version>` convention):

```text
/plugins install https://github.com/volcengine/OpenViking/releases/download/kimi-code-memory-plugin@<version>/kimi-code-memory-plugin-<version>.zip
```

`pack.mjs` can also build a ZIP archive whose root is the plugin root, and
`/plugins install <zip-url>` accepts it from any static host.
- Sessions that predate installation are imported explicitly with `node ./scripts/backfill.mjs`; see [Kimi Code](./22-kimi-code.md).
- Both distributions use the plugin name `openviking-memory`. The host registry keeps one entry per name, so install only one on a host.

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
