# Agent Integrations Overview

OpenViking can act as the long-term memory and context backend for many agent runtimes. Pick the integration that matches your agent.

## Which integration should I use?

| If you use… | Use this |
|-------------|----------|
| **Claude Code** | [Claude Code Memory Plugin](./02-claude-code.md) — auto-recall + auto-capture via hooks |
| **OpenClaw** | [OpenClaw Plugin](./03-openclaw.md) — context-engine with full lifecycle integration |
| **Codex** | [Codex Memory Plugin](./04-codex.md) — lifecycle hooks for auto-recall and incremental capture |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md) — built-in OpenViking memory provider, no plugin install needed |
| **LangChain / LangGraph** | [LangChain and LangGraph](./07-langchain-langgraph.md) — retriever, tools, context backend, store, and middleware |
| **Cursor / Trae / Manus / Claude Desktop / ChatGPT / …** | [MCP Clients](./06-mcp-clients.md) — point any MCP-compatible client at the built-in `/mcp` endpoint |
| **AstrBot / OpenCode / …** | [Community Plugins](./08-community-plugins.md) — community-maintained integrations for various runtimes |

## Integration depths

Some integrations go beyond what a generic MCP client can do:

- **Generic MCP clients** call OpenViking on demand through tools the model decides to invoke. Setup is one config snippet.
- **Hooks-based / native plugins** (Claude Code, Codex, OpenClaw, Hermes Agent, AstrBot) drive recall and capture from runtime lifecycle events — every prompt, every turn, session start/end, compact, subagent spawn. The model doesn't need to "remember to recall."
- **SDK integrations** (LangChain/LangGraph) wire OpenViking into framework-native abstractions such as retrievers, tools, chat history, stores, and middleware.

For agents whose runtime exposes hooks, middleware, or a context-engine slot, the native integration path is usually the better default.

## Prerequisite for all integrations

Every integration on this page connects to a running OpenViking server. If you don't have one yet, follow the [Quickstart Guide](../getting-started/02-quickstart.md). The default endpoint is `http://localhost:1933`; remote use requires an API key (see [Authentication](../guides/04-authentication.md)).
