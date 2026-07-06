# Codex 记忆插件

本插件旨在为 [Codex](https://developers.openai.com/codex) 提供持久化的跨会话（session）记忆功能。只需安装一次，即可实现：在每次用户输入前自动召回相关记忆，在每轮对话结束后进行增量捕获，并在上下文压缩（compaction）前将完整记录提交给记忆抽取器。同时，该插件将 Codex 连接至 OpenViking 的 `/mcp` 端点，使模型能够直接调用 `search`、`store` 等工具来主动管理记忆。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

Claude Code 和 Codex 共用同一个安装脚本。它会依次询问界面语言（English/中文）、要安装的 harness、下载源和 OpenViking 凭据；所有步骤幂等，可安全地重复执行。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

GitHub 访问受限的地区，从火山引擎 TOS 镜像运行同一个安装脚本（或在下载源提问时选择「TOS 镜像」）。Codex 走 TOS 时安装自 TOS 托管的 git 仓库，保留远程更新能力：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --dist tos
```

现在不再需要任何 shell wrapper——插件自带的 stdio MCP 代理会在运行时读取 `~/.openviking/ovcli.conf`（或 `OPENVIKING_*` 环境变量），与 hooks 使用同一套配置链。安装完成后：

```bash
codex              # 首次启动需进入 /hooks 完成一次审批
```

<details>
<summary><b>手动安装</b></summary>

前置条件：需安装 Node.js >= 22、Codex >= 0.130.0，并启用 `plugin_hooks` 特性。

1. **配置连接** — 手写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`），或装完后运行插件自带向导 `node <插件目录>/scripts/setup.mjs`。

2. **从远程 marketplace 安装插件**：

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   若你的 Codex 版本未默认启用 plugin hooks，在 `~/.codex/config.toml` 中加上 `[features]` → `plugin_hooks = true`。之后可用 `codex plugin marketplace upgrade openviking` 更新。

</details>

## 验证

启动 `codex` 后，插件将在每次用户输入前自动召回记忆。若设置环境变量 `OPENVIKING_DEBUG=1`，则会将相关事件日志写入 `~/.openviking/logs/codex-hooks.log`。

## 工作原理

本插件深度挂载于 Codex 的生命周期之中：在每次用户输入前，它会搜索 OpenViking 并注入相关的记忆（触发 `UserPromptSubmit`）；在每轮对话结束后，会将新的对话追加至当前会话（触发 `Stop`）；在上下文压缩前，补齐并提交（commit）完整的对话记录（触发 `PreCompact`），以确保记忆抽取器能够在完整的上下文环境中运行。此外，在启动新会话时，插件还会自动清理前次运行遗留的孤儿会话（orphan session）。

> **已知局限**：当通过 `SIGTERM`、`Ctrl+C` 或输入 `/exit` 退出 Codex 时，不会触发任何 hook（钩子）。遗留的孤儿会话将在下一次触发 `SessionStart` 时，通过闲置 TTL（生存时间，默认为 30 分钟）机制或活动窗口启发式策略进行回收清理。

<details>
<summary><b>配置</b></summary>

凭据来源：默认使用当前激活的 `ovcli.conf`（`OPENVIKING_CLI_CONFIG_FILE` 或 `~/.openviking/ovcli.conf`），因此 `ov config switch <name>` 会在下次启动时同时影响 hook、MCP 代理和 Codex 内部运行的 `ov` 命令。只有明确希望环境变量覆盖 CLI 配置时，才设置 `OPENVIKING_CREDENTIAL_SOURCE=env`。若没有 ovcli 配置，则依次回退到环境变量、`ov.conf` 和内置默认值。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整的服务器 URL |
| `OPENVIKING_API_KEY` | — | API 密钥（将通过 `Authorization: Bearer` 标头发送） |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | hook、MCP 和 Codex 内部 `ov` 命令共同使用的当前 CLI 配置 |
| `OPENVIKING_CREDENTIAL_SOURCE` | `auto` | 设置为 `env` 时强制使用环境变量凭据 |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | `SessionStart` 活动窗口阈值（毫秒） |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | `SessionStart` 闲置 TTL 清理阈值（毫秒） |
| `OPENVIKING_DEBUG` | `false` | 是否将日志写入 `~/.openviking/logs/codex-hooks.log` |

更多调参说明（如 `OPENVIKING_RECALL_LIMIT`、`OPENVIKING_CAPTURE_ASSISTANT_TURNS` 等），请参考 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin)。

</details>

## 故障排查

| 现象 | 可能原因 | 修复方法 |
|------|------|------|
| MCP 工具调用报认证错误 | 当前 ovcli 配置没有 authenticated server 所需的有效 `api_key` | 修正 `~/.openviking/ovcli.conf`（或运行 `node <插件目录>/scripts/setup.mjs`）后重启 Codex；stdio 代理会在启动时和认证失败后重新读取配置 |
| MCP 工具调用报连接错误 | 服务器不可达或 URL 配置错误 | 执行 `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` 检查服务器状态 |
| `4 hooks need review` | 首次启动需要进行安全审批 | 在 Codex 终端内输入 `/hooks` 完成审批 |
| `ov config switch` 后插件仍指向旧服务器 | 上个会话的代理进程仍在运行 | 重启 Codex；代理在启动时解析凭据 |
| Hook 与 MCP 指向不同服务器 | 某一侧设置了 `OPENVIKING_CREDENTIAL_SOURCE=env` 且环境变量过期 | 取消该设置（让 ovcli.conf 同时驱动两者），或保证环境变量一致 |

## 参见

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 为什么以及如何给你的 Coding Agent 加上长期记忆
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 完整的环境变量说明与架构图
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — 提交（commit）决策树
- [MCP 客户端](./06-mcp-clients.md) — MCP 协议、工具列表及其他客户端
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置说明
