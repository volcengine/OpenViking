# Agent Integrations

OpenViking can act as the long-term memory and context backend for many agent runtimes. This page collects the integrations that already exist — pick the one that matches your agent.

## Plugins in this repository

- **[Claude Code Memory Plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin)** — automatic recall before every prompt and capture after every turn for [Claude Code](https://docs.claude.com/en/docs/claude-code/overview), via hooks. No MCP tool calls required from the model.
- **[OpenClaw Plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)** — context-engine + hooks + tools + runtime manager for [OpenClaw](https://github.com/openclaw/openclaw). Owns long-term memory retrieval, session archiving, summarization, and memory extraction across the OpenClaw lifecycle.

## Generic MCP clients

For any MCP-compatible runtime (Cursor, Trae, Manus, Claude Desktop, ChatGPT/Codex, etc.), use the built-in HTTP MCP endpoint at `http://<server>:1933/mcp`.

→ See the [MCP Integration Guide](./06-mcp-integration.md) for client configuration, authentication, and verified platforms.

## External integrations

- **[Hermes Agent — OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)** — Nous Research's Hermes Agent ships first-class support for OpenViking as a memory provider.

## More plugins under `examples/`

The repo also ships several community/experimental plugins that aren't covered above. They differ in target runtime, integration depth, and maintenance status — read each one's README before adopting:

- **[Codex Memory MCP Server](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)** — minimal MCP-only server for [Codex](https://github.com/openai/codex); exposes `openviking_recall` / `openviking_store` and a couple more tools. No lifecycle hooks.
- **[OpenCode Memory Plugin (`opencode-memory-plugin`)](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)** — exposes OpenViking memories as explicit OpenCode tools and syncs conversation sessions into OpenViking.
- **[OpenCode Plugin (`opencode/plugin`)](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)** — injects indexed code repos into OpenCode's context and auto-starts the OpenViking server on demand.
