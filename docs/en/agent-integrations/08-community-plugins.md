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

## Open WebUI tool server

[Open WebUI](https://github.com/open-webui/open-webui) is a self-hosted AI chat interface.

Source: [examples/openwebui-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openwebui-plugin)

A standalone FastAPI server that exposes a curated subset of OpenViking endpoints as OpenAPI tools, so Open WebUI can call them as native tools. Setup and endpoint details are in the README.

## More examples

The [examples/](https://github.com/volcengine/OpenViking/tree/main/examples) directory also contains deployment and integration samples beyond agent plugins — Grafana dashboards, Kubernetes Helm charts, multi-tenant setups, snapshot workflows, and SDK snippets.

