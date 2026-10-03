# Choose an agent integration

OpenViking can act as the long-term memory and context backend for many agent runtimes. Pick the integration that matches your agent.

## Which integration should I use?

| If you use… | Use this |
|-------------|----------|
| **Claude Code** | [Claude Code Memory Plugin](./02-claude-code.md) — auto-recall + auto-capture via hooks |
| **OpenClaw** | [OpenClaw Plugin](./03-openclaw.md) — context-engine with full lifecycle integration |
| **Codex / TraeCode CLI 2.0** | [Codex Memory Plugin](./04-codex.md) — lifecycle hooks for auto-recall and incremental capture |
| **Cursor** | [Cursor Memory Integration](./12-cursor.md) — one command installs lifecycle hooks, MCP tools, rules, and skills |
| **TRAE / TRAE CN** | [TRAE Memory Integration](./13-trae.md) — one installer configures prompt-time recall, turn capture, and OpenViking tools |
| **DeepSeek Harness (`dsh`)** | [DeepSeek Harness Memory Bundle](./17-dsh.md) — in-process Cordis plugin with pre-step recall, event capture, and the OpenViking MCP tools |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md) — built-in OpenViking memory provider, no plugin install needed |
| **OpenCode** | [OpenCode Plugin](./10-opencode.md) — MCP tools plus lifecycle hooks for repo context, auto-recall, and capture |
| **pi** | [pi Coding Agent Extension](./11-pi.md) — native extension with auto-recall, turn capture, threshold commit, and the server's MCP tools registered as native pi tools |
| **LangChain / LangGraph** | [LangChain and LangGraph](./07-langchain-langgraph.md) — retriever, tools, context backend, store, and middleware |
| **Multiple local coding agents / a desktop UI** | [OpenViking Helper](./14-openviking-helper.md) — visual agent setup, session inspection, and memory management |
| **Any Agent Plugins 1.0 client** | [Agent Plugins 1.0 Package](./15-agent-plugins.md) — one portable package: `openviking-memory` skill plus the OpenViking MCP tools |
| **Manus / Claude Desktop / ChatGPT / other MCP clients** | [MCP Clients](./06-mcp-clients.md) — point any MCP-compatible client at the built-in `/mcp` endpoint |
| **ZCode / AstrBot / …** | [Community Plugins](./08-community-plugins.md) — community-maintained integrations for various runtimes |

## Compare integrations side by side

For the concrete differences between integrations — tool surface, automatic recall, session and commit behaviour, compaction takeover, degradation and fault tolerance — see the [Capability Reference](./16-capability-reference.md), a cross-integration comparison matrix.

## Developing and maintaining plugins

To add or maintain an integration, follow the [Hook + MCP Agent Plugin Development and Maintenance Standard](./18-plugin-development.md). When using VibeCoding, require your coding agent to read and follow it before making changes, using Claude Code, Codex, and other existing plugins as implementation references.

## Prerequisite for all integrations

Every integration on this page connects to a running OpenViking server. If you don't have one yet, follow the [Quickstart Guide](../getting-started/02-quickstart.md). The default endpoint is `http://localhost:1933`; remote use requires an API key (see [Authentication](../guides/04-authentication.md)).

## Low-latency recall

After verifying the integration, use [Tune recall latency](19-recall-tuning.md) to configure query expansion, result compression and timeouts.
