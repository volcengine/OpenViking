# 其他插件

仓库里还附带了几个未在 Claude Code 和 OpenClaw 主集成中介绍的社区/实验性插件。它们在目标 runtime、集成深度和维护状态上各有差异，使用前请先阅读各自的 README。

## Codex 记忆 MCP Server

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

面向 [Codex](https://github.com/openai/codex) 的最小化 MCP-only 服务，刻意保持窄边界：

- 不挂生命周期 hook
- 不跑后台捕获 worker
- 不写 `~/.codex`
- 不留 build 产物

Codex 拿到的只是几个显式记忆工具：`find`、`remember`，外加几个辅助。

如果你只需要 Codex 显式调用记忆（不需要自动召回/捕获），这是最简方案。

## OpenCode 插件

OpenCode 有两个设计路径不同的插件变体。请按你的使用方式自行选择，我们不替你决定。

### `opencode-memory-plugin` — 显式工具版本

源码：[examples/opencode-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-memory-plugin)

通过 OpenCode 的工具机制把 OpenViking 记忆暴露为显式工具，并把对话会话同步到 OpenViking。

- 模型看到的是具体工具，由它决定何时调用
- OpenViking 数据按需通过工具调用获取，而不是预注入到每次 prompt
- 插件还会把 OpenViking session 与 OpenCode 对话保持同步，并通过 `memcommit` 触发后台抽取

### `opencode/plugin` — 上下文注入版本

源码：[examples/opencode/plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode/plugin)

把已索引的代码仓库注入 OpenCode 上下文，并按需自动启动 OpenViking 服务器。

- prompt 上下文中加入索引代码库的相关片段
- 自带一个轻量启动器，按需拉起 OpenViking 服务

## 通用 MCP 客户端

Cursor、Trae、Manus、Claude Desktop、ChatGPT/Codex 以及任何其他兼容 MCP 的 runtime，无需专属插件——直接把客户端指向内置 `/mcp` 端点即可。

→ 参见 [MCP 集成指南](../guides/06-mcp-integration.md)。
