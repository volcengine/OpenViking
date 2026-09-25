# Agent Integrations Overview

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

Query expansion and recall compression add model calls. Disable both when response time takes priority; retrieval, budgets, and cross-turn deduplication remain enabled:

```bash
export OPENVIKING_RECALL_QUERY_EXPANSION=off
export OPENVIKING_RECALL_COMPRESS=off
```

Or configure them in `~/.openviking/ovcli.conf`:

```json
{
  "plugin": {
    "recallQueryExpansion": "off",
    "recallCompress": "off"
  }
}
```

### Choose a compression mode

| Value | Behavior |
| --- | --- |
| `off` | Do not compress recall results |
| `server` | Request compression on the OpenViking server |
| `client` | Use a local compressor only; supported by Claude Code and Codex |
| `auto` | Prefer a local compressor when available; otherwise request automatic server processing |

Claude Code and Codex default to `auto`. Their local compressors are `claude -p` and `codex exec`; see [§3.2.5](./16-capability-reference.md#_3-2-5-recall-digest). Other integrations that support cloud compression retain `off` as their default and require explicit opt-in. Server compression is supported by Claude Code, Codex, OpenCode, DSH, pi, Cursor, TRAE, TRAE CN, ZCode, OpenClaw, and Hermes. It requires a server with context-search rewrite support.

These settings control automatic recall. Explicit MCP `search` calls use the arguments supplied in that call. See the [shared plugin documentation](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression) for details and older-server fallback behavior.

Shared plugins read the `plugin` section in `ovcli.conf`; `plugin.<harness>` overrides a setting for one client. Environment variables take precedence; the older `OPENVIKING_RECALL_REWRITE` still works as an alias for `OPENVIKING_RECALL_COMPRESS`. See [Plugin settings](../configuration/02-client.md#plugin-settings). Restart the agent after changing settings so its hooks load the new configuration. These are plugin-client settings; the server's `ov.conf` does not need to change.

### Request timeout

Query expansion, retrieval, and digest compression run in sequence. Query expansion defaults to a 5-second timeout (`retrieval.recall_intent_timeout_s`); digest rewriting defaults to 30 seconds (`retrieval.recall_rewrite_timeout_s`).

`OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS` or `plugin.recallContextTimeoutMs` sets the client's timeout for the entire context request. When unset, the client waits the plugin's ordinary timeout, raised to at least 15 seconds when the request carries a session (query expansion) and at least 45 seconds when it asks for a digest. An override should exceed the server timeouts the request will spend and stay below the host's hook timeout. Ending the request early discards the entire response.
