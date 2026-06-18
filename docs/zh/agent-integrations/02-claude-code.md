# Claude Code 记忆插件

为 [Claude Code](https://docs.claude.com/zh-CN/docs/claude-code/overview) 添加跨项目、跨会话（session）的长期记忆功能。安装完成后，每轮对话均会自动召回相关记忆并捕获新内容，无需模型主动调用任何工具。

源码：[examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

该脚本将自动检查依赖项、配置 OpenViking 连接并完成插件安装，支持重复运行（幂等操作）。

GitHub 访问受限的地区，可改用以下等价命令：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/claude-code-memory-plugin/tos-install.sh)
```

安装完成后，需在当前终端激活 `claude` 的封装函数（wrapper），或者直接打开一个新的终端窗口：

```bash
source ~/.openviking/openviking-repo/examples/claude-code-memory-plugin/setup-helper/wrapper.sh
```

> 通过自定义命令启动 Claude Code？例如包装脚本 `cc-custom`，或“基础命令 + 子命令”形式的多词启动器——在安装时的“Extra launch commands”一步填入（或运行脚本时传入 `OPENVIKING_CC_WRAP_EXTRA='cc-custom'`），即可让它们一并注入凭据。

使用一段时间后，即便在全新的对话中提及过往的话题，Claude Code 也能准确回忆起来。

<details>
<summary><b>手动安装</b></summary>

如果您倾向于手动安装：

1. **封装 `claude` 命令** — 在 `~/.zshrc`（zsh）或 `~/.bashrc`（bash）文件末尾追加以下代码，并将 `<仓库路径>` 替换为您本地克隆仓库的绝对路径。此操作可确保每次调用 `claude` 时，系统都会从 `~/.openviking/ovcli.conf` 动态注入 `OPENVIKING_URL` 和 `OPENVIKING_API_KEY`，且 API Key 仅在 `claude` 的进程树内有效：

   ```bash
   _ov_wrapper="<仓库路径>/examples/claude-code-memory-plugin/setup-helper/wrapper.sh"
   [ -f "$_ov_wrapper" ] && source "$_ov_wrapper"
   ```

   关于该函数的具体实现以及“为何不使用全局 `export`”的详细说明，请参阅 [插件 README → Configuring MCP](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuring-mcp)。

2. **安装插件** — 在 OpenViking 仓库根目录下执行：

   ```bash
   claude plugin marketplace add "$(pwd)/examples"
   claude plugin install claude-code-memory-plugin@openviking-plugins-local
   ```

3. **启动 Claude Code** — 运行后输入 `/mcp` 命令，确认 OpenViking 对应的服务器 URL 配置正确。

> 尚未创建 `ovcli.conf`？请先按照 [部署指南 → CLI](../guides/03-deployment.md#cli) 的说明进行配置。
>
> 使用纯本地模式（`http://127.0.0.1:1933`，无鉴权）？您可以跳过第 1 步，插件将直接使用本地默认值。
>
> 使用 Claude Code < 2.0 版本？请参考 [插件 README 的兼容模式章节](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README_CN.md#兼容模式claude-code--20)。

</details>

## 验证

```bash
type claude        # 期望输出：claude is a shell function
```

> 若上一步输出的是一个路径而非 `shell function`，说明 wrapper 尚未生效，请先 `source` 那行 wrapper（或新开一个终端）再启动；否则 `claude` 会静默连到本地 `127.0.0.1` 且不带鉴权。

在 `type claude` 显示为 shell function 的终端中运行 `claude` 启动，随后：

- 输入 `/plugins` → 在 Installed 列表中应能找到 **openviking-memory**（其子项 **openviking** MCP 应显示为已连接状态）。
- 输入 `/mcp` → OpenViking 对应的条目应显示您的服务器 URL 及有效的认证信息。
- 输入 `/openviking-memory:ov` → 查看服务器状态、身份信息、召回/注入的统计数据以及功能开关状态。

若插件未正常工作，可设置环境变量 `OPENVIKING_DEBUG=1`，并查看日志文件 `~/.openviking/logs/cc-hooks.log` 以排查问题。

## 工作原理

插件通过挂载到 Claude Code 的不同生命周期节点来发挥作用：

- **每次用户输入前** — 搜索 OpenViking 数据库并注入相关记忆。
- **每轮回复后** — 自动捕获并存储新的对话内容。
- **会话（session）启动时** — 注入用户画像与记忆索引。
- **上下文压缩（compact）前及会话结束时** — 提交所有待处理的消息记录。
- **启动子代理（subagent）时** — 为其分配相互隔离的记忆会话。

所有数据写入操作均为异步执行，不会阻塞当前的对话进程。

<details>
<summary><b>配置</b></summary>

配置项的读取优先级为：环境变量 > `ovcli.conf` > `ov.conf` > 内置默认值（`http://127.0.0.1:1933`，无鉴权）。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_AUTO_RECALL` | `true` | 每次用户输入前自动触发记忆召回 |
| `OPENVIKING_RECALL_LIMIT` | `6` | 单轮对话最多注入的记忆条数 |
| `OPENVIKING_RECALL_TOKEN_BUDGET` | `2000` | 内联记忆内容的 Token 预算上限 |
| `OPENVIKING_AUTO_CAPTURE` | `true` | 每轮对话结束后自动捕获新记忆 |
| `OPENVIKING_BYPASS_SESSION` | `false` | 禁用当前会话的所有 Hook |
| `OPENVIKING_BYPASS_SESSION_PATTERNS` | `""` | 通过 CSV 格式的 glob 模式匹配并自动跳过特定会话 |
| `OPENVIKING_MEMORY_ENABLED` | (auto) | 强制开启或关闭插件 |
| `OPENVIKING_DEBUG` | `false` | 将调试日志输出至 `~/.openviking/logs/cc-hooks.log` |

在多租户场景下，请额外配置 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER`。完整的环境变量列表请参阅 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration)。

</details>

## 状态行

插件会在 Claude Code 的输入框下方显示一行 OpenViking 状态栏，用于指示：连接状态、召回条数、捕获进度以及当前会话状态。关于状态栏各部分的详细含义与自定义配置方法，请参阅 [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/STATUSLINE.md)。

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| 插件未激活 | 未找到 `ov.conf` 或 `ovcli.conf` 配置文件 | 运行 [安装脚本](#安装)，或手动设置 `OPENVIKING_MEMORY_ENABLED=1` 配合 URL/API_KEY 使用。 |
| Hook 已触发但召回结果为空 | 服务器未启动或 URL 配置错误 | 执行命令测试连通性：`curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP 工具连接到了 `127.0.0.1` 而非远程服务器 | 缺少 `claude` 函数封装 | 检查 `type claude` 的输出是否包含 "shell function"；详情见 [手动安装](#安装) |
| `type claude` 显示的是路径而非 shell function（wrapper 未生效） | 安装后未 `source` rc，或在未加载该 rc 的终端里启动 | 执行 `source ~/.zshrc`（bash 用 `~/.bashrc`），或新开一个终端窗口 |
| 通过别名（如 `cc`）启动，凭据未注入 | 把别名名填进了 `OPENVIKING_CC_WRAP_EXTRA`（别名会被跳过），或别名指向的命令未被封装 | 封装别名指向的真实命令而非别名本身：`alias cc=claude` 无需配置；`alias cc=claude-custom` 则把 `claude-custom` 填入 |
| 远程认证失败 (401 / 403) | API Key 错误或缺少租户 Header | 检查 `OPENVIKING_API_KEY` 是否正确；多租户环境下还需核对 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |

## 参见

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 探讨为 Coding Agent 添加长期记忆的动机与实际效果。
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — 查看完整的环境变量列表、Hook 运行细节及系统架构图。
- [MCP 客户端](./06-mcp-clients.md) — 了解 MCP 工具参数及其他客户端集成指南。
- [部署指南 → CLI](../guides/03-deployment.md#cli) — 学习 `ovcli.conf` 的具体配置方法。
