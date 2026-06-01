# Codex 记忆插件

为 [Codex](https://developers.openai.com/codex) 提供持久化的跨 session 记忆。安装一次——每次用户输入前自动召回，每轮结束后增量捕获，compaction 前提交给记忆抽取器。插件同时把 Codex 接到 OpenViking 的 `/mcp` 端点，模型可以直接调用 search、store 等工具管理记忆。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

脚本会检查依赖、配置 OpenViking 连接并注册插件。每一步幂等，反复执行安全。

安装完成后：

```bash
source ~/.zshrc    # 或 ~/.bashrc
codex              # 首次启动进 /hooks 审批一次
```

<details>
<summary><b>手动安装</b></summary>

前置：Node.js >= 22、Codex >= 0.130.0、`codex_hooks` feature 已启用。

1. **Shell 函数包装** — 在 shell rc 中追加一个 `codex()` 函数，每次调用时从 ovcli.conf 注入 OpenViking 环境变量。完整函数见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md)。

2. **插件安装** — 注册本地 marketplace 并启用插件。具体命令见 `setup-helper/install.sh`。

3. **占位符渲染** — `.mcp.json` 和 `hooks.json` 中的占位符在拷贝到 Codex 缓存时需替换为绝对值。installer 会自动做。

</details>

## 验证

```bash
type codex         # 期望输出：codex is a shell function
```

进入 Codex 后插件会在每次输入前召回记忆。设 `OPENVIKING_DEBUG=1` 可把事件写到 `~/.openviking/logs/codex-hooks.log`。

## 工作原理

插件挂载到 Codex 的生命周期：每次用户输入前搜索 OpenViking 并注入相关记忆（`UserPromptSubmit`），每轮结束后把新的对话追加到 session（`Stop`），compaction 前补齐并 commit 完整 transcript（`PreCompact`）让记忆抽取器跑在完整上下文上。新 session 启动时还会清理前次运行的孤儿 session。

> **已知盲区**：Codex 在 `SIGTERM` / `Ctrl+C` / `/exit` 时不触发任何 hook。孤儿 session 由下一次 `SessionStart` 的闲置 TTL 清理（30 分钟）或活动窗口启发式回收。

<details>
<summary><b>配置</b></summary>

配置优先级：环境变量 > `ovcli.conf` > `ov.conf` > 内置默认值（`http://127.0.0.1:1933`，无鉴权）。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整服务器 URL |
| `OPENVIKING_API_KEY` | — | API key（通过 `Authorization: Bearer` 发送） |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | SessionStart 活动窗口阈值 |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | SessionStart 闲置 TTL 清理阈值 |
| `OPENVIKING_DEBUG` | `false` | 写日志到 `~/.openviking/logs/codex-hooks.log` |

调参（`OPENVIKING_RECALL_LIMIT`、`OPENVIKING_CAPTURE_ASSISTANT_TURNS` 等）见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin)。

</details>

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| `MCP server is not logged in` | 启动时 `OPENVIKING_API_KEY` 不在 env 里 | 确认 `codex()` 函数已 source，`ovcli.conf` 有 `api_key` |
| `4 hooks need review` | 首次启动的安全审批 | 在 Codex 输入 `/hooks` 审批 |
| 审批后仍 `hook (failed) exited with code 1` | 缓存里占位符未渲染 | 重新跑一次一行安装脚本 |
| 召回为空 | 服务器不可达或 URL 不对 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Hook 401 但 MCP 可用，或反之 | env vs ovcli.conf 不一致 | Hook 每次重读 ovcli.conf，MCP 在启动时读 env。改完重启 codex。 |

## 参见

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 为什么以及如何给你的 Coding Agent 加上长期记忆
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 完整环境变量、架构图
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — commit 决策树
- [MCP 客户端](./06-mcp-clients.md) — MCP 协议、工具列表、其他客户端
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置
