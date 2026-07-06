为 [Claude Code](https://docs.claude.com/zh-CN/docs/claude-code/overview) 添加跨项目、跨会话（session）的长期记忆功能。安装完成后，每轮对话均会自动召回相关记忆并捕获新内容，无需模型主动调用任何工具。

源码：[examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 步骤 1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness claude --dist tos
```

Claude Code 和 Codex 共用这一个安装脚本。它会依次询问界面语言（English/中文）和 OpenViking 凭据；所有步骤幂等，支持重复运行。不再需要任何 shell wrapper——插件自带的 stdio MCP 代理会在运行时读取 `~/.openviking/ovcli.conf`。

> TOS 渠道注册的是本地目录 marketplace，无法自动更新——更新请重跑上面的安装命令。

使用一段时间后，即便在全新的对话中提及过往的话题，Claude Code 也能准确回忆起来。

<details>
<summary><b>手动安装</b></summary>

如果您倾向于手动安装：

1. **配置连接** — 手写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或装完后运行插件自带向导 `node <插件目录>/scripts/setup.mjs`。

2. **安装插件** — 从远程 marketplace（需可访问 GitHub）：

   ```bash
   claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
   claude plugin install openviking-memory@openviking
   ```

3. **启动 Claude Code** — 运行后输入 `/mcp` 命令，确认 OpenViking 条目已连接。

> 尚未创建 `ovcli.conf`？请先按照部署指南 → CLI 的说明进行配置。
>
> 使用纯本地模式（`http://127.0.0.1:1933`，无鉴权）？您可以跳过第 1 步，插件将直接使用本地默认值。
>
> 使用 Claude Code < 2.0 版本？安装脚本会自动识别并回退到兼容模式；详见 [插件 README 的兼容模式章节](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README_CN.md#兼容模式claude-code--20)。

</details>


## 步骤 2：验证

启动 `claude`，随后：

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
| 插件未激活 | 未找到 `ov.conf` 或 `ovcli.conf` 配置文件 | 运行 [步骤 1：安装](#步骤-1安装)，或手动设置 `OPENVIKING_MEMORY_ENABLED=1` 配合 URL/API_KEY 使用。 |
| Hook 已触发但召回结果为空 | 服务器未启动或 URL 配置错误 | 执行命令测试连通性：`curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP 工具连接到了 `127.0.0.1` 而非远程服务器 | `~/.openviking/ovcli.conf` 中没有 `url`（代理回退到本地默认值） | 修正 `ovcli.conf`（或运行 `node <插件目录>/scripts/setup.mjs`）后重启 Claude Code |
| 远程认证失败 (401 / 403) | API Key 错误或缺少租户 Header | 检查 `OPENVIKING_API_KEY` 是否正确；多租户环境下还需核对 `OPENVIKING_ACCOUNT` 和 `OPENVIKING_USER` |


## 参考文档

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 探讨为 Coding Agent 添加长期记忆的动机与实际效果。
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — 查看完整的环境变量列表、Hook 运行细节及系统架构图。
