# Agent 集成

OpenViking 可以作为多种 Agent 运行时的长期记忆与上下文后端。本页汇总了目前已有的集成，按运行时挑选适合的接入方式即可。

## 本仓库内置插件

- **[Claude Code 记忆插件](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin)** — 通过 hooks 为 [Claude Code](https://docs.claude.com/zh-CN/docs/claude-code/overview) 提供自动召回（prompt 前）与自动捕获（每轮结束后）能力，模型侧无需主动调用 MCP 工具。
- **[OpenClaw 插件](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)** — 为 [OpenClaw](https://github.com/openclaw/openclaw) 提供 context-engine + hooks + tools + 运行时管理一体化集成，覆盖长期记忆检索、会话归档、摘要与记忆抽取等 OpenClaw 全生命周期能力。

## 通用 MCP 客户端

对于任何兼容 MCP 的运行时（Cursor、Trae、Manus、Claude Desktop、ChatGPT/Codex 等），可直接使用内置的 HTTP MCP 端点 `http://<server>:1933/mcp`。

→ 参见 [MCP 集成指南](./06-mcp-integration.md) 了解客户端配置、鉴权方式和已验证平台。

## 外部集成

- **[Hermes Agent — OpenViking 记忆提供方](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)** — Nous Research 的 Hermes Agent 已原生支持将 OpenViking 作为记忆提供方。

## `examples/` 下的更多插件

仓库里还附带了几个未在上文重点介绍的社区/实验性插件。它们在目标 runtime、集成深度和维护状态上各有差异，使用前请先阅读各自的 README：

- **[Codex 记忆 MCP Server](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)** — 面向 [Codex](https://github.com/openai/codex) 的最小化 MCP 服务，提供 `openviking_recall` / `openviking_store` 等几个工具，不含生命周期 hooks。
- **[OpenCode 记忆插件（`opencode-memory-plugin`）](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)** — 通过 OpenCode 的工具机制把 OpenViking 记忆暴露为显式工具，并把对话会话同步到 OpenViking。
- **[OpenCode 插件（`opencode/plugin`）](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)** — 把已索引的代码仓库注入 OpenCode 上下文，按需自动启动 OpenViking 服务器。
