# 其他插件

仓库里还附带了几个未在 Claude Code 和 OpenClaw 主集成中介绍的社区/实验性插件。它们在目标 runtime、集成深度和维护状态上各有差异，使用前请先阅读各自的 README。

## Codex 记忆插件

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)

面向 [Codex](https://github.com/openai/codex) 的生命周期 hook 集成，并把显式工具接到 OpenViking server 原生 `/mcp`：

- `UserPromptSubmit` 自动召回并注入相关记忆
- `Stop` 增量追加对话 turn 到同一个 OpenViking session
- `PreCompact` 在 Codex 压缩上下文前提交 session，触发记忆抽取
- `SessionStart(startup|clear)` 用 active-window heuristic 和 idle-TTL sweep 清理 orphan session
- 显式工具不再由插件自带 stdio server 提供，而是统一走 OpenViking server 的 `/mcp`

推荐安装：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

安装器会启用本地 `openviking-memory@openviking-plugins-local` 插件、打开 `features.plugin_hooks = true`，并可选地在 `~/.codex/config.toml` 写入 `mcp_servers.openviking`，指向 OpenViking server 的原生 `/mcp`。交互式运行时会询问是否启用 MCP；如果只想装 hooks，可设置 `OPENVIKING_CODEX_ENABLE_MCP=0`，已有的安装器管理的 `mcp_servers.openviking` 会被移除。

hook 会读取 `~/.openviking/ovcli.conf`；但 Codex 的 HTTP MCP transport 不读这个文件，所以 MCP URL 和 account/user/agent header 需要落到 Codex config 里。

原生 `/mcp` 暴露的工具包括 `search`、`read`、`list`、`store`、`add_resource`、`grep`、`glob`、`forget`、`health`（具体以 server 版本为准）。

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
