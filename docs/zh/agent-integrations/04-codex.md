# Codex 记忆插件

本插件旨在为 [Codex](https://developers.openai.com/codex) 提供持久化的跨会话（session）记忆功能。只需安装一次，即可实现：在每次用户输入前自动召回相关记忆，在每轮对话结束后进行增量捕获，并在上下文压缩（compaction）前将完整记录提交给记忆抽取器。同时，该插件将 Codex 连接至 OpenViking 的 `/mcp` 端点，使模型能够直接调用 `search`、`store` 等工具来主动管理记忆。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)
```

脚本将自动检查依赖项、配置 OpenViking 连接并注册插件。安装过程的每一步均支持幂等操作，可安全地重复执行。

GitHub 访问受限的地区，可改用以下等价命令：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/codex-memory-plugin/tos-install.sh)
```

安装完成后，请在当前终端激活 `codex` 的封装函数（或新开一个终端窗口）：

```bash
source ~/.openviking/openviking-repo/examples/codex-memory-plugin/setup-helper/wrapper.sh
codex              # 首次启动需进入 /hooks 完成一次审批
```

> 通过自定义命令启动 Codex？例如包装脚本 `codex-custom`，或“基础命令 + 子命令”形式的多词启动器——在安装时的“Extra launch commands”一步填入（或运行脚本时传入 `OPENVIKING_CODEX_WRAP_EXTRA='codex-custom'`），即可让它们一并注入凭据。

<details>
<summary><b>手动安装</b></summary>

前置条件：需安装 Node.js >= 22、Codex >= 0.130.0，并启用 `codex_hooks` 特性。

1. **Shell 函数封装** — 在 shell 的配置文件（如 rc 文件）中追加一个 `codex()` 函数，确保每次调用时都能从 `ovcli.conf` 注入 OpenViking 相关的环境变量。完整的函数代码请参考 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md)。

2. **插件安装** — 注册本地 marketplace 并启用插件。具体执行命令请参见 `setup-helper/install.sh`。

3. **占位符渲染** — 在将 `.mcp.json` 和 `hooks.json` 复制到 Codex 缓存目录时，需将其中的占位符替换为绝对路径或具体数值。自动化安装脚本会自动完成此操作。

</details>

## 验证

```bash
type codex         # 期望输出：codex is a shell function
```

> 若上一步输出的是一个路径而非 `shell function`，说明 wrapper 尚未生效，请先 `source` 那行 wrapper（或新开一个终端）再启动；否则 codex 启动时拿不到 `OPENVIKING_API_KEY`，会报 `MCP server is not logged in`。

进入 Codex 后，插件将在每次用户输入前自动召回记忆。若设置环境变量 `OPENVIKING_DEBUG=1`，则会将相关事件日志写入 `~/.openviking/logs/codex-hooks.log`。

## 工作原理

本插件深度挂载于 Codex 的生命周期之中：在每次用户输入前，它会搜索 OpenViking 并注入相关的记忆（触发 `UserPromptSubmit`）；在每轮对话结束后，会将新的对话追加至当前会话（触发 `Stop`）；在上下文压缩前，补齐并提交（commit）完整的对话记录（触发 `PreCompact`），以确保记忆抽取器能够在完整的上下文环境中运行。此外，在启动新会话时，插件还会自动清理前次运行遗留的孤儿会话（orphan session）。

> **已知局限**：当通过 `SIGTERM`、`Ctrl+C` 或输入 `/exit` 退出 Codex 时，不会触发任何 hook（钩子）。遗留的孤儿会话将在下一次触发 `SessionStart` 时，通过闲置 TTL（生存时间，默认为 30 分钟）机制或活动窗口启发式策略进行回收清理。

<details>
<summary><b>配置</b></summary>

配置优先级为：环境变量 > `ovcli.conf` > `ov.conf` > 内置默认值（默认 URL 为 `http://127.0.0.1:1933`，无鉴权）。

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `OPENVIKING_URL` / `OPENVIKING_BASE_URL` | — | 完整的服务器 URL |
| `OPENVIKING_API_KEY` | — | API 密钥（将通过 `Authorization: Bearer` 标头发送） |
| `OPENVIKING_CODEX_ACTIVE_WINDOW_MS` | `120000` | `SessionStart` 活动窗口阈值（毫秒） |
| `OPENVIKING_CODEX_IDLE_TTL_MS` | `1800000` | `SessionStart` 闲置 TTL 清理阈值（毫秒） |
| `OPENVIKING_DEBUG` | `false` | 是否将日志写入 `~/.openviking/logs/codex-hooks.log` |

更多调参说明（如 `OPENVIKING_RECALL_LIMIT`、`OPENVIKING_CAPTURE_ASSISTANT_TURNS` 等），请参考 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md#tuning-the-plugin)。

</details>

## 故障排查

| 现象 | 可能原因 | 修复方法 |
|------|------|------|
| `MCP server is not logged in` | 启动时环境变量中缺失 `OPENVIKING_API_KEY` | 确认已 source `codex()` 函数，且 `ovcli.conf` 中配置了 `api_key` |
| `type codex` 显示的是路径而非 shell function（wrapper 未生效） | 安装后未 `source` rc，或在未加载该 rc 的终端里启动 | 执行 `source ~/.zshrc`（bash 用 `~/.bashrc`），或新开一个终端窗口 |
| 通过别名（如 `cx`）启动，凭据未注入 | 把别名名填进了 `OPENVIKING_CODEX_WRAP_EXTRA`（别名会被跳过），或别名指向的命令未被封装 | 封装别名指向的真实命令而非别名本身：`alias cx=codex` 无需配置；`alias cx=codex-custom` 则把 `codex-custom` 填入 |
| `4 hooks need review` | 首次启动需要进行安全审批 | 在 Codex 终端内输入 `/hooks` 完成审批 |
| 审批后仍提示 `hook (failed) exited with code 1` | 缓存文件中的占位符未被正确渲染 | 重新执行一次一键安装脚本 |
| 召回结果为空 | 服务器不可达或 URL 配置错误 | 执行 `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` 检查服务器状态 |
| Hook 报 401 但 MCP 正常可用，或反之 | 环境变量与 `ovcli.conf` 的配置不一致 | Hook 每次触发均会重新读取 `ovcli.conf`，而 MCP 仅在启动时读取环境变量。请修改配置并重启 Codex。 |

## 参见

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 为什么以及如何给你的 Coding Agent 加上长期记忆
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 完整的环境变量说明与架构图
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — 提交（commit）决策树
- [MCP 客户端](./06-mcp-clients.md) — MCP 协议、工具列表及其他客户端
- [部署指南 → CLI](../guides/03-deployment.md#cli) — `ovcli.conf` 配置说明
