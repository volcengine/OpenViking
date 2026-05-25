# 其他插件

仓库里还附带了几个未在 Claude Code、Codex 和 OpenClaw 主集成中介绍的社区/实验性插件。它们在目标 runtime、集成深度和维护状态上各有差异，使用前请先阅读各自的 README。

## AstrBot 插件

[AstrBot](https://github.com/AstrBotDevs/AstrBot) 是一个多平台 IM Bot 框架，支持 QQ（AIOCQHTTP）、Telegram、Discord、飞书等 20+ 平台。

源码：[astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

为 AstrBot 提供 OpenViking 长期记忆能力：自动捕获群聊/私聊对话，LLM 请求前语义召回相关记忆，支持三档 venue 记忆隔离。

**安装**：在 AstrBot WebUI → 插件市场搜索 **OpenViking Memory** 并安装；或从链接安装：`https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

**主要特性**：

- 基于 hooks 的自动召回与捕获，模型不需要主动调用工具
- 三档隔离模式：`venue_user`（群/私聊各自独立）、`venue_user_fanout`（跨群共享）、`global_user`（全局共享）
- 四触发器自动 commit：消息计数、token 阈值、空闲超时、进程退出 flush
- 首次接入群聊时自动拉取平台历史消息入库

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
