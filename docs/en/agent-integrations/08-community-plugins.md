# Community Plugins

Community-maintained integrations for various agent runtimes. Each differs in target platform, integration depth, and maintenance status — check the linked README before adopting.

## ZCode memory integration

Source: [examples/zcode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin)

The ZCode community integration adds cross-project, cross-session memory through config-driven lifecycle hooks and an OpenViking MCP server:

- **SessionStart** injects the user profile.
- **UserPromptSubmit** recalls relevant memories.
- **PreToolUse** redirects direct `viking://` reads to MCP tools.
- **Stop** captures unseen rollout turns in a detached worker, then commits the OpenViking session.

ZCode does not expose `PreCompact`, `SessionEnd`, or subagent lifecycle hooks. The adapter therefore commits on `Stop`, uses ZCode rollout files as the authoritative incremental transcript, and falls back to hook stdin only when no rollout file is available.

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and ZCode.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness zcode
```

Use the TOS mirror where GitHub is unavailable:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness zcode --dist tos
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
| Duplicate captures | An older installation left duplicate hook entries | Run `install.sh --harness zcode --uninstall`, then reinstall |

Implementation details and currently verified ZCode assumptions are documented in the plugin's [README](https://github.com/volcengine/OpenViking/tree/main/examples/zcode-memory-plugin) and [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/zcode-memory-plugin/DESIGN.md).

## AstrBot plugin

[AstrBot](https://github.com/AstrBotDevs/AstrBot) is a multi-platform IM bot framework supporting QQ, Telegram, Discord, Lark, and 20+ other platforms.

Source: [astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

Provides auto-capture of group/DM conversations, semantic recall before each LLM request, and configurable venue memory isolation.

**Install**: In AstrBot WebUI, search **OpenViking Memory** in the Plugin Marketplace; or install from URL: `https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

**Key features**:

- Auto-recall and auto-capture via hooks — the model doesn't need to invoke tools
- Three isolation modes: `venue_user` (per-group/DM), `venue_user_fanout` (cross-venue sharing), `global_user` (single user)
- Four auto-commit triggers: message count, token threshold, idle timeout, and process-exit flush
- Backfills platform message history on first venue encounter

## Open WebUI tool server

[Open WebUI](https://github.com/open-webui/open-webui) is a self-hosted AI chat interface.

Source: [examples/openwebui-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openwebui-plugin)

A standalone FastAPI server that exposes a curated subset of OpenViking endpoints as OpenAPI tools, so Open WebUI can call them as native tools. Setup and endpoint details are in the README.

## More examples

The [examples/](https://github.com/volcengine/OpenViking/tree/main/examples) directory also contains deployment and integration samples beyond agent plugins — Grafana dashboards, Kubernetes Helm charts, multi-tenant setups, snapshot workflows, and SDK snippets.

## See also

- [Capability Reference](./16-capability-reference.md)
