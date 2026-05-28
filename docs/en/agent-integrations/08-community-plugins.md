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

## OpenCode plugins

Two OpenCode plugin variants exist with different design choices. Pick whichever matches your usage.

### `opencode-memory-plugin` — explicit-tool variant

Source: [examples/opencode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)

Exposes OpenViking memories as explicit OpenCode tools. The agent decides when to call them, and data is fetched on demand rather than pre-injected.

### `opencode/plugin` — context-injection variant

Source: [examples/opencode/plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)

Injects indexed code repos into OpenCode's context and auto-starts the OpenViking server when needed.