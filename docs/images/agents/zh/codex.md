为 [Codex](https://developers.openai.com/codex) 提供持久化的跨会话（session）记忆。一次安装，即可实现：在用户每次输入前自动召回记忆，每轮对话结束后进行增量捕获，并在上下文压缩（compaction）前将记忆提交给抽取器。该插件还将 Codex 连接至 OpenViking 的 `/mcp` 端点，使模型能够直接调用 search、store 等工具来管理记忆。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 步骤 1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness codex --dist tos
```

Claude Code 和 Codex 共用这一个安装脚本。它会依次询问界面语言（English/中文）和 OpenViking 凭据；所有步骤幂等，可安全地重复执行。Codex 走 TOS 时安装自 TOS 托管的 git 仓库，之后可用 `codex plugin marketplace upgrade openviking` 远程更新。

不再需要任何 shell wrapper——插件自带的 stdio MCP 代理会在运行时读取 `~/.openviking/ovcli.conf`。安装完成后：

```bash
codex              # 首次启动需执行 /hooks 进行一次安全审批
```

<details>
<summary><b>手动安装</b></summary>

前置条件：Node.js >= 22、Codex >= 0.130.0，且已启用 `plugin_hooks` 特性。

1. **配置连接** — 手写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或装完后运行插件自带向导 `node <插件目录>/scripts/setup.mjs`。

2. **从远程 marketplace 安装插件**（需可访问 GitHub）：

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   若你的 Codex 版本未默认启用 plugin hooks，在 `~/.codex/config.toml` 中加上 `[features]` → `plugin_hooks = true`。

</details>


## 步骤 2：验证

启动 `codex` 后，插件将在每次输入前自动召回记忆。将环境变量 `OPENVIKING_DEBUG` 设置为 `1`，可将事件日志输出至 `~/.openviking/logs/codex-hooks.log`。


## 工作原理

插件深入挂载于 Codex 的生命周期中：在用户每次输入前，它会检索 OpenViking 并注入相关记忆（`UserPromptSubmit`）；每轮对话结束后，将新对话追加到当前会话中（`Stop`）；在上下文压缩前，补齐并提交（commit）完整的对话记录（`PreCompact`），以确保记忆抽取器能够在完整的上下文环境中运行。此外，在启动新会话时，它还会自动清理上一次运行遗留的孤儿会话。

> **已知盲区**：Codex 在收到 `SIGTERM` 信号、用户按下 `Ctrl+C` 或输入 `/exit` 退出时，不会触发任何 hook。这些遗留的孤儿会话将在下一次触发 `SessionStart` 时，通过闲置 TTL（30 分钟）机制或活动窗口启发式算法进行回收清理。

<details>
<summary><b>配置</b></summary>

配置读取优先级：环境变量 > `ovcli.conf` > `ov.conf` > 内置默认值（`http://127.0.0.1:1933`，无鉴权）。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整的服务器 URL |
| `OPENVIKING_API_KEY` | — | API key（通过 `Authorization: Bearer` 发送） |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | `SessionStart` 活动窗口阈值 |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | `SessionStart` 闲置 TTL 清理阈值 |
| `OPENVIKING_DEBUG` | `false` | 将日志输出至 `~/.openviking/logs/codex-hooks.log` |

关于更多参数调优（如 `OPENVIKING_RECALL_LIMIT`、`OPENVIKING_CAPTURE_ASSISTANT_TURNS` 等），请参阅 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin)。

</details>


## 故障排查

| 现象 | 原因 | 解决方案 |
|------|------|------|
| MCP 工具调用报认证错误 | `ovcli.conf` 中没有 authenticated server 所需的有效 `api_key` | 修正 `ovcli.conf`（或运行 `node <插件目录>/scripts/setup.mjs`）后重启 Codex |
| MCP 工具调用报连接错误 | 服务器不可达或 URL 配置错误 | 运行 `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` 检查连接状态 |
| `4 hooks need review` | 首次启动触发的安全审批 | 在 Codex 中输入 `/hooks` 完成审批 |
| `ov config switch` 后插件仍指向旧服务器 | 上个会话的代理进程仍在运行 | 重启 Codex；stdio 代理在启动时解析凭据 |


## 参考文档

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 探讨为何以及如何为您的 Coding Agent 赋予长期记忆
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 包含完整的环境变量说明及架构图
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — 详细介绍了 commit 的决策树
