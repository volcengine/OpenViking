# Codex

为 [Codex](https://developers.openai.com/codex) 提供跨会话记忆。插件通过 hooks 自动召回、捕获和提交对话，通过 MCP 提供检索、读取和记忆管理工具。

源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)（Codex 专属文件在 [hosts/codex](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/codex)） | [博客：动机与效果展示](https://blog.openviking.ai/post/openviking-coding-agent/)

## 安装

安装脚本会询问要安装的 harness 和 OpenViking 凭据；重复运行是安全的。

连接托管实例时，选择 **OpenViking 托管服务**，再选择服务商：

- **Volcengine**：输入服务 API Key，安装器会提供服务地址。
- **BytePlus**：输入 API Key。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

旧版安装器连接 BytePlus 时使用**自定义 URL**。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

Codex 从随每个 OpenViking 版本发布的 git 仓库安装，保留远程更新能力。TraeCode CLI 2.0 直接接受这个 Codex 格式插件：安装脚本询问要安装的 harness 时选择 **TraeCode CLI 2.0**，然后用 `trae-cli` 启动。

### 首次启动：信任 hooks

安装插件不会自动信任它的 hooks。首次启动时 Codex 会显示 **Hooks need review** 提示。选 **Trust all and continue**，或选 **Review hooks** 先查看命令；选 *Continue without trusting* 后 hooks 保持关闭，直到你在 `/hooks` 中启用。同时在 `/plugins` 中确认 `openviking-memory` 已启用。hook 定义新增或变化后需要再次审阅。参见[官方 hook 信任文档](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)。

> **升级**：hooks 文件换了位置，更新插件后 Codex 会要求再审阅并信任一次 OpenViking hooks。

hooks 关闭时 MCP 工具仍可用；自动召回和捕获需要 hooks。

<details>
<summary><b>手动安装</b></summary>

前置条件：Node.js >= 22、Codex >= 0.130.0，并启用 `plugin_hooks` 功能。

1. **配置连接**——编写 `~/.openviking/ovcli.conf`（`url`、`api_key`，可选 `account`/`user`）。

2. **从远程 marketplace 安装插件**：

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   若你的 Codex 版本未默认启用 plugin hooks，在 `~/.codex/config.toml` 中加上 `[features]` → `plugin_hooks = true`。之后可用 `codex plugin marketplace upgrade openviking` 更新。

</details>

安装完成后，新开一个 Codex 会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 操作。它会检查连接，并带你完成首次使用；参见[一键接入](../getting-started/07-agent-onboarding.md)。

## 验证

启动 `codex`；`SessionStart` hook 会加载你的 profile，每次提问都会召回相关记忆。设置 `OPENVIKING_DEBUG=1` 可把事件写入 `~/.openviking/logs/codex-hooks.log`。TraeCode CLI 2.0 用 `trae-cli` 启动，并用 `trae-cli plugin list` 确认插件已启用。

## Codex 上的不同之处

- **会话结束需要 Codex 0.145 或更新版本。** Codex 0.145 之前没有 `SessionEnd`；0.145 及以上也只在正常退出（`/quit`、`/exit`、两次 `Ctrl-C`、EOF、`codex exec` 运行结束）时触发，`SIGTERM`、关闭终端、`kill -9` 或崩溃时不触发。没有它时，插件不会提交会话的最后一段。请在服务端开启 `memory.session_auto_commit.enabled`，由空闲提交兜底。见[退出时会发生什么](./16-capability-reference.md#what-happens-at-exit)。
- **不捕获子 agent，也没有工具调用 hook。** Codex 上不安装 `viking://` 防护。
- **Hook 信任**，见上文。
- **召回摘要**：`recallCompress` 设为 `auto` 时，Codex 在可用时使用本地压缩器。见[召回摘要](./16-capability-reference.md#recall-digest)。

## OpenViking 来源汇总

插件会报告每次回答拿到了哪些 OpenViking 来源：自动召回，以及显式的 OpenViking MCP 或 `ov` CLI 查询。统计的是提供给模型的来源，不代表模型确实依赖了它们。

| 设置 | 环境变量 | 默认值 | 作用 |
|------|----------|--------|------|
| `usageOutput` | `OPENVIKING_USAGE_OUTPUT` | `auto` | `terminal`：一条 Stop hook 提示消息。`desktop`：由模型渲染在回答末尾。`auto`：设置了 `TERM_PROGRAM` 或非 `dumb` 的 `TERM` 时用 terminal，否则用 desktop |
| `usageView` | `OPENVIKING_USAGE_VIEW` | `summary` | `expanded` 显示来源详情；`off` 关闭报告及其本地元数据 |

报告失败不会阻塞召回或捕获。

## 故障排查

| 现象 | 原因 | 解决方法 |
|------|------|----------|
| 已安装插件但没有召回或捕获 | hooks 未信任或被禁用（例如更新之后），或插件被禁用 | 在 `/hooks` 中信任并启用 OpenViking 条目，再在 `/plugins` 中确认 `openviking-memory`。 |
| MCP 工具调用报认证错误 | 当前 ovcli 配置中没有有效的 `api_key` | 修正 `~/.openviking/ovcli.conf` 并重启 Codex。 |
| hooks 和 MCP 连到不同服务器 | 残留的 `OPENVIKING_*` 凭据环境变量覆盖了 ovcli.conf | 清除这些环境变量，或设置 `OPENVIKING_CREDENTIAL_SOURCE=cli`。 |

## 参见

- [能力参考](./16-capability-reference.md)——所有宿主共用的捕获、提交、召回、离线重试和设置
- [插件 README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README_CN.md)
- [插件设置](../configuration/02-client.md#插件配置)
- [MCP 客户端](./06-mcp-clients.md)
