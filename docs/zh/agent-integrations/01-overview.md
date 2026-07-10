# Agent 集成概览

OpenViking 可以作为多种 Agent 运行时的长期记忆与上下文后端。按你的运行时挑选合适的接入方式即可。

## 该用哪个集成？

| 你在用… | 选这个 |
|---------|---------|
| **Claude Code** | [Claude Code 记忆插件](./02-claude-code.md) — 通过 hooks 实现自动召回与自动捕获 |
| **OpenClaw** | [OpenClaw 插件](./03-openclaw.md) — 全生命周期一体化集成 |
| **Codex** | [Codex 记忆插件](./04-codex.md) — 生命周期 hooks 自动召回与增量捕获 |
| **Cursor** | [Cursor 记忆集成](./12-cursor.md) — 一条命令安装生命周期 Hook、MCP 工具、Rules 与 Skills |
| **TRAE / TRAE CN** | [TRAE 记忆集成](./13-trae.md) — 一个安装器完成 prompt 召回、回合捕获与 OpenViking 工具接入 |
| **Hermes Agent** | [Hermes Agent](./05-hermes.md) — 内置 OpenViking 记忆提供方，无需安装插件 |
| **OpenCode** | [OpenCode 插件](./10-opencode.md) — MCP 工具 + 生命周期 hooks，覆盖仓库上下文、自动召回与捕获 |
| **pi** | [pi Coding Agent 扩展](./11-pi.md) — 原生扩展，自动召回、逐轮捕获与阈值 commit |
| **LangChain / LangGraph** | [LangChain 和 LangGraph](./07-langchain-langgraph.md) — retriever、tools、context backend、store 和 middleware |
| **Manus / Claude Desktop / ChatGPT / 其他 MCP 客户端** | [MCP 客户端](./06-mcp-clients.md) — 任何兼容 MCP 的客户端直接对接内置 `/mcp` 端点 |
| **AstrBot / …** | [社区插件](./08-community-plugins.md) — 社区维护的各运行时集成 |

## 所有集成的共同前置

本页所有集成都需要连接到一个正在运行的 OpenViking 服务。如果你还没有，请先按 [快速开始](../getting-started/02-quickstart.md) 部署。默认端点是 `http://localhost:1933`；远程使用需要 API Key（参见 [鉴权](../guides/04-authentication.md)）。
