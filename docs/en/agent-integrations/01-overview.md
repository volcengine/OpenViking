# Agent Integrations Overview

OpenViking can act as the long-term memory and context backend for many agent runtimes. Pick the integration that matches your agent.

## Which integration should I use?

| If you use… | Use this |
|-------------|----------|
| **Claude Code** | [Claude Code Memory Plugin](./02-claude-code.md) — auto-recall + auto-capture via hooks |
| **OpenClaw** | [OpenClaw Plugin](./03-openclaw.md) — context-engine with full lifecycle integration |
| **Codex** | [Codex Memory Plugin](./04-codex.md) — lifecycle hooks for auto-recall and incremental capture |
| **Cursor** | [Cursor Memory Integration](./12-cursor.md) — one command installs lifecycle hooks, MCP tools, rules, and skills |
| **TRAE / TRAE CN** | [TRAE Memory Integration](./13-trae.md) — one installer configures prompt-time recall, turn capture, and OpenViking tools |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md) — built-in OpenViking memory provider, no plugin install needed |
| **OpenCode** | [OpenCode Plugin](./10-opencode.md) — MCP tools plus lifecycle hooks for repo context, auto-recall, and capture |
| **pi** | [pi Coding Agent Extension](./11-pi.md) — native extension with auto-recall, turn capture, and threshold commit |
| **LangChain / LangGraph** | [LangChain and LangGraph](./07-langchain-langgraph.md) — retriever, tools, context backend, store, and middleware |
| **Manus / Claude Desktop / ChatGPT / other MCP clients** | [MCP Clients](./06-mcp-clients.md) — point any MCP-compatible client at the built-in `/mcp` endpoint |
| **AstrBot / …** | [Community Plugins](./08-community-plugins.md) — community-maintained integrations for various runtimes |

## Prerequisite for all integrations

Every integration on this page connects to a running OpenViking server. If you don't have one yet, follow the [Quickstart Guide](../getting-started/02-quickstart.md). The default endpoint is `http://localhost:1933`; remote use requires an API key (see [Authentication](../guides/04-authentication.md)).
