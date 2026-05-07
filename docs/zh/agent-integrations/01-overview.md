# Agent 集成概览

OpenViking 可以作为多种 Agent 运行时的长期记忆与上下文后端。本节汇总了目前已有的集成，按运行时挑选适合的接入方式即可。

## 该用哪个集成？

| 你在用… | 选这个 |
|---------|---------|
| **Claude Code** | [Claude Code 记忆插件](./02-claude-code.md) — 通过 hooks 实现自动召回与自动捕获，模型侧无需主动调用 MCP 工具 |
| **OpenClaw** | [OpenClaw 插件](./03-openclaw.md) — context-engine + hooks + tools + 运行时管理一体化集成，覆盖完整生命周期 |
| **Codex / OpenCode** | [其他社区插件](./04-other-plugins.md) — 包括 MCP-only 版本和 tool 机制版本 |
| **Cursor / Trae / Manus / Claude Desktop / ChatGPT / …** | [MCP 集成指南](../guides/06-mcp-integration.md) — 任何兼容 MCP 的客户端都可直接对接内置 `/mcp` 端点 |
| **Hermes Agent (Nous Research)** | [Hermes — OpenViking 记忆提供方](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 一等公民支持，无需额外安装插件 |

## 两种集成深度

本页列出的插件能力超过通用 MCP 客户端：

- **通用 MCP 客户端**：模型主动调用工具时按需访问 OpenViking。配置只需一份连接片段。
- **基于 hooks 的插件**（Claude Code、OpenClaw）：在运行时生命周期事件（每次 prompt、每轮结束、session 起止、compact、subagent 派生等）中驱动召回与捕获。模型不需要"记得调用"。

如果你的 Agent runtime 暴露 hooks 或 context-engine 槽位，基于 hooks 的方式通常是更好的默认选择。

## 所有集成的共同前置

本页所有集成都需要连接到一个正在运行的 OpenViking 服务。如果你还没有，请先按 [快速开始](../getting-started/02-quickstart.md) 部署。默认端点是 `http://localhost:1933`；远程使用需要 API Key（参见 [鉴权](../guides/04-authentication.md)）。
