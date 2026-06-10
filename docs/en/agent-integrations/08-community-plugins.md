# Community Plugins

Community-maintained integrations for various agent runtimes. Each differs in target platform, integration depth, and maintenance status — check the linked README before adopting.

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

## OpenCode plugin

OpenViking provides one unified OpenCode plugin for repository context and long-term memory workflows.

Source: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

The plugin combines indexed repository context, OpenViking memory tools, session synchronization, lifecycle commit, and automatic recall through OpenCode plugin hooks. Use this plugin for both the former explicit-tool and context-injection use cases.
