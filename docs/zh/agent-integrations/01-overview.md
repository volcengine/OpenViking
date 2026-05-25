# Agent 集成概览

OpenViking 可以作为多种 Agent 运行时的长期记忆与上下文后端。按你的运行时挑选合适的接入方式即可。

## 该用哪个集成？

| 你在用… | 选这个 |
|---------|---------|
| **Claude Code** | [Claude Code 记忆插件](./02-claude-code.md) — 通过 hooks 实现自动召回与自动捕获 |
| **OpenClaw** | [OpenClaw 插件](./03-openclaw.md) — 全生命周期一体化集成 |
| **Codex** | [Codex 记忆插件](./04-codex.md) — 生命周期 hooks 自动召回与增量捕获 |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md) — 内置 OpenViking 记忆提供方，无需安装插件 |
| **LangChain / LangGraph** | [LangChain 和 LangGraph](./07-langchain-langgraph.md) — retriever、tools、context backend、store 和 middleware |
| **Cursor / Trae / Manus / Claude Desktop / ChatGPT / …** | [MCP 客户端](./06-mcp-clients.md) — 任何兼容 MCP 的客户端直接对接内置 `/mcp` 端点 |
| **AstrBot / OpenCode / …** | [社区插件](./08-community-plugins.md) — 社区维护的各运行时集成 |

## 集成深度

部分集成能力超过通用 MCP 客户端：

- **通用 MCP 客户端**：模型主动调用工具时按需访问 OpenViking。配置只需一份连接片段。
- **基于 hooks / 原生插件**（Claude Code、Codex、OpenClaw、Hermes Agent、AstrBot）：在运行时生命周期事件（每次 prompt、每轮结束、session 起止、compact、subagent 派生等）中驱动召回与捕获。模型不需要"记得调用"。
- **SDK 集成**（LangChain/LangGraph）：把 OpenViking 接入框架原生抽象，例如 retriever、tools、chat history、store 和 middleware。

如果你的 Agent 运行时暴露 hooks、middleware 或 context-engine 槽位，原生集成通常是更好的默认选择。

## 所有集成的共同前置

本页所有集成都需要连接到一个正在运行的 OpenViking 服务。如果你还没有，请先按 [快速开始](../getting-started/02-quickstart.md) 部署。默认端点是 `http://localhost:1933`；远程使用需要 API Key（参见 [鉴权](../guides/04-authentication.md)）。
