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
| **pi** | [pi Coding Agent Extension](./11-pi.md) — native extension with auto-recall, turn capture, and threshold commit |
| **LangChain / LangGraph** | [LangChain and LangGraph](./07-langchain-langgraph.md) — retriever, tools, context backend, store, and middleware |
| **Multiple local coding agents / a desktop UI** | [OpenViking Helper](./14-openviking-helper.md) — visual agent setup, session inspection, and memory management |
| **Any Agent Plugins 1.0 client** | [Agent Plugins 1.0 Package](./15-agent-plugins.md) — one portable package: `openviking-memory` skill plus the OpenViking MCP tools |
| **Manus / Claude Desktop / ChatGPT / other MCP clients** | [MCP Clients](./06-mcp-clients.md) — point any MCP-compatible client at the built-in `/mcp` endpoint |
| **ZCode / AstrBot / …** | [Community Plugins](./08-community-plugins.md) — community-maintained integrations for various runtimes |

## Compare integrations side by side

For the concrete differences between integrations — tool surface, automatic recall, session and commit behaviour, compaction takeover, degradation and fault tolerance — see the [Capability Reference](./16-capability-reference.md), a cross-integration comparison matrix.

## Prerequisite for all integrations

Every integration on this page connects to a running OpenViking server. If you don't have one yet, follow the [Quickstart Guide](../getting-started/02-quickstart.md). The default endpoint is `http://localhost:1933`; remote use requires an API key (see [Authentication](../guides/04-authentication.md)).

## Low-latency recall

Query expansion and recall-result compression are two independent, optional model calls. Disable both in the Agent plugin when response latency matters most; semantic retrieval, budgeting, tier degradation, and cross-turn dedup continue to work.

The same environment variables apply to both the Claude Code and Codex memory plugins. Query expansion is also switchable in OpenCode and pi; compression is Claude Code and Codex only.

```bash
export OPENVIKING_RECALL_QUERY_EXPANSION=off
export OPENVIKING_RECALL_COMPRESS=off
```

Both plugins have a local compression path, but expose it differently:

- Claude Code defaults to `recallCompress=auto`: it prefers local `claude -p` (Sonnet with low effort) and falls back to an OpenViking server digest when the local CLI is unavailable. `client` forces local-only compression, while `server` forces server-only compression.
- Codex calls local `codex exec` by default, trying `gpt-5.3-codex-spark` first and then `gpt-5.6-luna` with low effort. It does not enable server-side compression.

The shared default is `recallCompress=auto`. `OPENVIKING_RECALL_COMPRESS=off` disables compression in both plugins; Codex interprets `auto` or `client` as enabling its local compressor. The old Claude Code variable `OPENVIKING_RECALL_REWRITE` remains supported for compatibility, but new configurations should use the unified name.

The same settings can live in `~/.openviking/ovcli.conf`:

```json
{
  "url": "https://openviking.example.com",
  "api_key": "your-api-key",
  "plugin": {
    "recallQueryExpansion": "off",
    "recallCompress": "off"
  }
}
```

Environment variables take precedence over `ovcli.conf`. Restart the Agent after changing these settings so its hook processes reload the configuration. These are plugin-client settings; the server's `ov.conf` does not need to change.

The `plugin` section is read by the Claude Code and Codex plugins, so a `plugin` entry named after another harness is currently inert. OpenCode and pi read `OPENVIKING_RECALL_QUERY_EXPANSION` from the environment (or `recallQueryExpansion` in their own config file) but not `OPENVIKING_RECALL_COMPRESS`, since neither requests a server digest.

A context request waits longer than an ordinary request, because aborting it client-side discards the whole response rather than just the stage that ran long. The server pipeline is serial and each optional stage has its own fuse: query expansion (`retrieval.recall_intent_timeout_s`, 5s) runs first, then retrieval, body reads and budgeting, and only then the digest rewrite (`retrieval.recall_rewrite_timeout_s`, 30s). The deadline therefore follows what the request actually asks for — 15s once it carries a session and can spend the expansion fuse, 45s when it also asks for a digest, and the plugin's ordinary timeout when it asks for neither. Set `OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS` (or `plugin.recallContextTimeoutMs`) to pin it — keep it above the fuses the request will spend and below the Agent's own hook timeout.
