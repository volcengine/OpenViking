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
| **DeepSeek Harness (`dsh`) / DeepSeek Harness Desktop** | [DeepSeek Harness Memory Bundle](./17-dsh.md) — in-process Cordis plugin with pre-step recall, event capture, and the OpenViking MCP tools |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md). Memory plugin with automatic capture and recall. Older releases include a built-in provider. |
| **OpenCode** | [OpenCode Plugin](./10-opencode.md) — MCP tools plus lifecycle hooks for repo context, auto-recall, and capture |
| **pi** | [pi Coding Agent Extension](./11-pi.md) — native extension with auto-recall, turn capture, threshold commit, and the server's MCP tools registered as native pi tools |
| **LangChain / LangGraph** | [LangChain and LangGraph](./07-langchain-langgraph.md) — retriever, tools, context backend, store, and middleware |
| **VikingBot** | [VikingBot Installation and Configuration](../guides/17-vikingbot.md) — built-in Agent with the `openviking_rule_search` tool (Beta) |
| **Multiple local coding agents / a desktop UI** | [OpenViking Helper](./14-openviking-helper.md) — visual agent setup, session inspection, and memory management |
| **Any Agent Plugins 1.0 client** | [Agent Plugins 1.0 Package](./15-agent-plugins.md) — one portable package: `openviking-memory` skill plus the OpenViking MCP tools |
| **Other Claude Code plugin-compatible clients** | [Install from the OpenViking marketplace](#claude-code-plugin-compatible-clients-install-the-plugin-directly) |
| **Manus / Claude Desktop / ChatGPT / other MCP clients** | [MCP Clients](./06-mcp-clients.md) — point any MCP-compatible client at the built-in `/mcp` endpoint |
| **Chat clients / SDK and API apps / other clients without a plugin** | [OpenViking Gateway](../guides/15-gateway.md) — point any client that takes a base URL and an API key at the gateway; it recalls memory and saves conversations with nothing to install in the client |
| **ZCode / CodeBuddy Code / AstrBot / …** | [Community Plugins](./08-community-plugins.md) — community-maintained integrations for various runtimes |

Some agents, such as Claude Code, Codex, OpenCode, pi and DSH, can use either their plugin or OpenViking Gateway. To decide between them, see [Gateway or plugin?](../guides/15-gateway.md#gateway-or-plugin).

## Claude Code plugin-compatible clients: install the plugin directly

OpenViking publishes `openviking-memory@openviking` through `.claude-plugin/marketplace.json`. For clients with a Claude Code plugin installer, register `volcengine/OpenViking` as a marketplace and install the plugin using the client's commands. Follow the linked client documentation for version requirements and plugin management:

| Client documentation | Add marketplace | Install plugin |
|----------------------|-----------------|----------------|
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing) | `copilot plugin marketplace add volcengine/OpenViking` | `copilot plugin install openviking-memory@openviking` |
| [Factory Droid](https://docs.factory.com/harness/plugins) | `droid plugin marketplace add volcengine/OpenViking` | `droid plugin install openviking-memory@openviking` |
| [Qoder CLI](https://docs.qoder.com/cli/plugins-reference) | `qoder plugins marketplace add volcengine/OpenViking` | `qoder plugins install openviking-memory@openviking` |
| [CodeBuddy CLI](https://www.codebuddy.ai/docs/cli/plugins-reference) | `codebuddy plugin marketplace add volcengine/OpenViking` | `codebuddy plugin install openviking-memory@openviking` |
| [Qwen Code](https://qwenlm.github.io/qwen-code-docs/en/users/extension/introduction/) | `qwen extensions sources add volcengine/OpenViking` | `qwen extensions install volcengine/OpenViking:openviking-memory` |
| [Oh My Pi](https://github.com/can1357/oh-my-pi/blob/main/docs/marketplace.md) | `omp plugin marketplace add volcengine/OpenViking` | `omp plugin install openviking-memory@openviking` |

Qoder's marketplace commands require its marketplace feature to be enabled. For example, the complete GitHub Copilot CLI installation is:

```bash
copilot plugin marketplace add volcengine/OpenViking
copilot plugin install openviking-memory@openviking
```

Configure the server connection as described in [Claude Code manual setup](./02-claude-code.md#install), then start a new client session and check that the plugin's MCP server and lifecycle hooks are active. When the client cannot install this plugin, use its dedicated integration above, [MCP](./06-mcp-clients.md), or the [gateway](../guides/15-gateway.md).

## Compare integrations side by side

For the concrete differences between integrations — tool surface, automatic recall, session and commit behaviour, compaction takeover, degradation and fault tolerance — see the [Capability Reference](./16-capability-reference.md), a cross-integration comparison matrix.

## Developing and maintaining plugins

To add or maintain an integration, follow the [Hook + MCP Agent Plugin Development and Maintenance Standard](./18-plugin-development.md). When using VibeCoding, require your coding agent to read and follow it before making changes, using Claude Code, Codex, and other existing plugins as implementation references.

## Prerequisite for all integrations

Every integration on this page connects to a running OpenViking server. If you don't have one yet, follow the [Quickstart Guide](../getting-started/02-quickstart.md). The default endpoint is `http://localhost:1933`; remote use requires an API key (see [Authentication](../guides/04-authentication.md)).

## Low-latency recall

After verifying the integration, use [Tune recall latency](19-recall-tuning.md) to configure query expansion, result compression and timeouts.
