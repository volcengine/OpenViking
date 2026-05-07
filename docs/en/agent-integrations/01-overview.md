# Agent Integrations Overview

OpenViking can act as the long-term memory and context backend for many agent runtimes. This section collects the integrations that already exist — pick the one that matches your agent.

## Which integration should I use?

| If you use… | Use this |
|-------------|----------|
| **Claude Code** | [Claude Code Memory Plugin](./02-claude-code.md) — auto-recall + auto-capture via hooks, no MCP tool calls required from the model |
| **OpenClaw** | [OpenClaw Plugin](./03-openclaw.md) — context-engine + hooks + tools + runtime manager, deep lifecycle integration |
| **Codex / OpenCode** | [Other community plugins](./04-other-plugins.md) — MCP-only and tool-mechanism variants |
| **Cursor / Trae / Manus / Claude Desktop / ChatGPT / …** | [MCP Integration Guide](../guides/06-mcp-integration.md) — point any MCP-compatible client at the built-in `/mcp` endpoint |
| **Hermes Agent (Nous Research)** | [Hermes — OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — first-class OpenViking memory provider, no plugin install needed |

## Two integration depths

The plugins listed here go beyond what a generic MCP client can do:

- **Generic MCP clients** call OpenViking on demand through tools the model decides to invoke. Setup is one config snippet.
- **Hooks-based plugins** (Claude Code, OpenClaw) drive recall and capture from runtime lifecycle events — every prompt, every turn, session start/end, compact, subagent spawn. The model doesn't need to "remember to recall."

For agents whose runtime exposes hooks or a context-engine slot, the hooks-based path is usually the better default.

## Prerequisite for all integrations

Every integration on this page connects to a running OpenViking server. If you don't have one yet, follow the [Quickstart Guide](../getting-started/02-quickstart.md). The default endpoint is `http://localhost:1933`; remote use requires an API key (see [Authentication](../guides/04-authentication.md)).
