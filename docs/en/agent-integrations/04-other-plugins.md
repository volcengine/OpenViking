# Other Plugins

The repo also ships several community/experimental plugins beyond the headline Claude Code and OpenClaw integrations. They differ in target runtime, integration depth, and maintenance status — read each one's README before adopting.

## Codex Memory MCP Server

Source: [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

A minimal MCP-only server for [Codex](https://github.com/openai/codex). Intentionally narrow scope:

- no lifecycle hooks
- no background capture worker
- no writes to `~/.codex`
- no checked-in build output

Codex gets four explicit memory tools: `openviking_recall`, `remember`, plus a couple more.

If you only need explicit memory operations from Codex (no auto-recall or auto-capture), this is the simplest option.

## OpenCode plugins

Two OpenCode plugin variants exist with different design choices. Pick whichever matches your usage — we don't make the decision for you.

### `opencode-memory-plugin` — explicit-tool variant

Source: [examples/opencode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)

Exposes OpenViking memories as explicit OpenCode tools and syncs the conversation session into OpenViking.

- the agent sees concrete tools and decides when to call them
- OpenViking data is fetched on demand via tool execution, not pre-injected into every prompt
- the plugin keeps an OpenViking session in sync with the OpenCode conversation and triggers background extraction with `memcommit`

### `opencode/plugin` — context-injection variant

Source: [examples/opencode/plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)

Injects indexed code repos into OpenCode's context and auto-starts the OpenViking server when needed.

- prompt context is augmented with relevant code from indexed repos
- bundles a small launcher that brings up the OpenViking server on demand

## Generic MCP clients

For Cursor, Trae, Manus, Claude Desktop, ChatGPT/Codex, and any other MCP-compatible runtime, you don't need a dedicated plugin — just point the client at the built-in `/mcp` endpoint.

→ See the [MCP Integration Guide](../guides/06-mcp-integration.md).
