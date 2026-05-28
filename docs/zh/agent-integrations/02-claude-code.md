# Claude Code 记忆插件

为 [Claude Code](https://docs.claude.com/zh-CN/docs/claude-code/overview) 提供跨项目、跨 session 的长期记忆，越用越聪明。安装一次，每次对话自动召回和捕获，模型不需要主动调用任何工具。

源码：[examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

脚本会检查依赖、配置 OpenViking 连接并安装插件。每一步都是幂等的——重复执行安全。

安装完成后启动 Claude Code，问它上次 session 聊过什么——它记得。

<details>
<summary><b>手动安装</b></summary>

如果你更喜欢手动操作：

1. **Shell 函数包装** — 在 `~/.zshrc` 或 `~/.bashrc` 末尾追加一个 `claude()` 函数，每次调用时从 `~/.openviking/ovcli.conf` 注入 `OPENVIKING_URL` 和 `OPENVIKING_API_KEY`。这样 API Key 只在 `claude` 进程树内有效。完整函数和安全说明见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#1-wrap-claude-to-inject-env-from-ovcliconf)。

2. **安装插件**，在 OpenViking 仓库根目录：

   ```bash
   claude plugin marketplace add "$(pwd)/examples"
   claude plugin install claude-code-memory-plugin@openviking-plugins-local
   ```

3. **启动 Claude Code**，执行 `/mcp` 确认 OpenViking 这一项显示的是你的服务器 URL。

> 还没有 `ovcli.conf`？先按 [部署指南 → CLI](../guides/03-deployment.md#cli) 创建。
>
> 纯本地模式（`http://127.0.0.1:1933`，无鉴权）？跳过第 1 步——插件会静默使用本地默认值。
>
> Claude Code < 2.0？见 [插件 README 的兼容模式章节](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README_CN.md#兼容模式claude-code--20)。

</details>

## 验证

```bash
type claude        # 期望输出：claude is a shell function
```

进入 Claude Code 后：

- `/plugins` → 在 Installed 中找到 **openviking-memory**（下属 **openviking** MCP 应显示已连接）
- `/mcp` → OpenViking 这一项应显示你的服务器 URL 和有效认证
- `/openviking-memory:ov` → 展示服务器状态、身份、召回/注入统计和开关状态

如果插件似乎没在工作，设 `OPENVIKING_DEBUG=1` 看 `~/.openviking/logs/cc-hooks.log`。

## 工作原理

插件挂载到 Claude Code 的生命周期：每次用户输入前搜索 OpenViking 并注入相关记忆，每轮回复后捕获新的对话内容，session 启动时注入用户画像和记忆索引，compact 前和 session 结束时提交待处理的消息，并为每个 subagent 分配隔离的记忆 session。所有写入操作异步执行，不会让你等待。

<details>
<summary><b>配置</b></summary>

配置优先级：环境变量 > `ovcli.conf` > `ov.conf` > 内置默认值（`http://127.0.0.1:1933`，无鉴权）。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_AUTO_RECALL` | `true` | 每次用户输入前自动召回 |
| `OPENVIKING_RECALL_LIMIT` | `6` | 单轮最多注入的记忆条数 |
| `OPENVIKING_RECALL_TOKEN_BUDGET` | `2000` | 内联内容的 token 预算 |
| `OPENVIKING_AUTO_CAPTURE` | `true` | 每轮结束后自动捕获 |
| `OPENVIKING_BYPASS_SESSION` | `false` | 跳过当前 session 的所有 hook |
| `OPENVIKING_BYPASS_SESSION_PATTERNS` | `""` | CSV glob 模式自动跳过 |
| `OPENVIKING_MEMORY_ENABLED` | (auto) | 强制开启/关闭 |
| `OPENVIKING_DEBUG` | `false` | 写日志到 `~/.openviking/logs/cc-hooks.log` |

多租户场景设置 `OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_AGENT_ID`。完整环境变量列表见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md#configuration)。

</details>

## 状态行

插件在 Claude Code 输入框下方渲染 OpenViking 状态：连接健康度、召回数量、捕获进度和 session 状态一目了然。完整段位说明和个性化 recipe 见 [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/STATUSLINE.md)。

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| 插件未激活 | 找不到 `ov.conf` / `ovcli.conf` | 跑 [安装脚本](#安装)，或设 `OPENVIKING_MEMORY_ENABLED=1` + URL/API_KEY |
| Hook 触发但召回为空 | 服务器没起来或 URL 不对 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| MCP 工具连到 `127.0.0.1` 而不是远程 | 缺少函数包装 | 确认 `type claude` 返回 "shell function"；见 [手动安装](#安装) |
| 远程认证 401 / 403 | API Key 错误或缺少租户头 | 检查 `OPENVIKING_API_KEY`；多租户还要核对 `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` |

## 参见

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 为什么以及如何给你的 Coding Agent 加上长期记忆
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md) — 完整环境变量表、hook 细节、架构图
- [迁移说明](https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/MIGRATION.md) — 从旧版插件升级
- [MCP 客户端](./06-mcp-clients.md) — MCP 工具参数与其他客户端
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置
