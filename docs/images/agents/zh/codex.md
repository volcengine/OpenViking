为 [Codex](https://developers.openai.com/codex) 提供持久化的跨会话（session）记忆。一次安装，即可实现：在用户每次输入前自动召回记忆，每轮对话结束后进行增量捕获，并在上下文压缩（compaction）前将记忆提交给抽取器。该插件还将 Codex 连接至 OpenViking 的 `/mcp` 端点，使模型能够直接调用 search、store 等工具来管理记忆。

源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 步骤 1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/codex-memory-plugin/tos-install.sh)
```

该脚本会自动检查依赖、配置 OpenViking 连接并注册插件。所有步骤均具有幂等性，可安全地重复执行。

安装完成后，在当前终端激活 `codex` 封装函数（或重新打开一个终端窗口）：

```bash
source ~/.openviking/openviking-repo/examples/codex-memory-plugin/setup-helper/wrapper.sh
codex              # 首次启动需执行 /hooks 进行一次安全审批
```

> 通过自定义命令启动 Codex？例如包装脚本 `codex-custom`，或“基础命令 + 子命令”形式的多词启动器——在安装时的“Extra launch commands”一步填入（或运行脚本时传入 `OPENVIKING_CODEX_WRAP_EXTRA='codex-custom'`），即可让它们一并注入凭据。

<details>
<summary><b>手动安装</b></summary>

前置条件：Node.js >= 22、Codex >= 0.130.0，且已启用 `codex_hooks` 特性。

1. **Shell 函数包装** — 在 shell 的配置文件（如 `.bashrc` 或 `.zshrc`）中追加一个 `codex()` 函数，以便在每次调用时从 `ovcli.conf` 注入 OpenViking 环境变量。完整函数代码请见 [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md)。

2. **插件安装** — 注册本地 marketplace 并启用该插件。具体命令请参考 `setup-helper/install.sh`。

3. **占位符渲染** — `.mcp.json` 和 `hooks.json` 中的占位符在拷贝至 Codex 缓存时需替换为绝对路径。安装程序会自动完成此操作。

</details>


## 步骤 2：验证

```bash
type codex         # 期望输出：codex is a shell function
```

> 若上一步输出的是一个路径而非 `shell function`，说明 wrapper 尚未生效，请先 `source` 那行 wrapper（或新开一个终端）再启动；否则 codex 启动时拿不到 `OPENVIKING_API_KEY`，会报 `MCP server is not logged in`。

进入 Codex 后，插件将在每次输入前自动召回记忆。将环境变量 `OPENVIKING_DEBUG` 设置为 `1`，可将事件日志输出至 `~/.openviking/logs/codex-hooks.log`。


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
| `MCP server is not logged in` | 启动时 `OPENVIKING_API_KEY` 未注入环境变量 | 确认 `codex()` 函数已被 `source` 激活，且 `ovcli.conf` 中包含 `api_key` |
| `type codex` 显示的是路径而非 shell function（wrapper 未生效） | 安装后未 `source` rc，或在未加载该 rc 的终端里启动 | 执行 `source ~/.zshrc`（bash 用 `~/.bashrc`），或新开一个终端窗口 |
| 通过别名（如 `cx`）启动，凭据未注入 | 把别名名填进了 `OPENVIKING_CODEX_WRAP_EXTRA`（别名会被跳过），或别名指向的命令未被封装 | 封装别名指向的真实命令而非别名本身：`alias cx=codex` 无需配置；`alias cx=codex-custom` 则把 `codex-custom` 填入 |
| `4 hooks need review` | 首次启动触发的安全审批 | 在 Codex 中输入 `/hooks` 完成审批 |
| 审批后仍提示 `hook (failed) exited with code 1` | 缓存中的占位符未被正确替换 | 重新运行一次单行安装脚本 |
| 召回为空 | 服务器不可达或 URL 配置错误 | 运行 `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` 检查连接状态 |
| Hook 返回 401 但 MCP 可用，或反之 | 环境变量与 `ovcli.conf` 配置不一致 | Hook 每次执行都会重新读取 `ovcli.conf`，而 MCP 仅在启动时读取环境变量。修改配置后请重启 Codex。 |


## 参考文档

- [博客：在 Claude Code / Codex 中接入 OpenViking](https://blog.openviking.ai/post/openviking-coding-agent/) — 探讨为何以及如何为您的 Coding Agent 赋予长期记忆
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md) — 包含完整的环境变量说明及架构图
- [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/DESIGN.md) — 详细介绍了 commit 的决策树
