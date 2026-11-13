# Qoder CLI

为 Qoder CLI 添加跨项目、跨会话的长期记忆。安装完成后，OpenViking Hook 会在会话启动和用户提交问题时注入相关上下文，在回复结束后捕获新对话；MCP 用于主动搜索、读取和管理记忆。

## 安装

前置条件：macOS 或 Linux、Node.js 18+，以及 `qodercli`（或国内版 `qoderclicn`）在 `PATH` 中的 Qoder CLI。安装过程中会引导配置 OpenViking 连接信息；询问要接入哪些 Agent 时选择 **Qoder CLI**。

安装器询问连接方式时，火山引擎云服务用户请选择 **火山引擎 OpenViking 云服务** 并填写 API Key。服务运行在本机时选择 **自建 / 本地**（`http://127.0.0.1:1933`）；远程自建服务请选择 **自定义 URL / 保持当前** 并填写其 URL。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装后新建一个 `qodercli` 会话生效。

安装完成后，在 Qoder 中新开一个会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 执行。它会检查连接，并带你完成首次使用，详见[一键接入](../getting-started/07-agent-onboarding.md)。

如果之前从 OpenViking 的 GitHub marketplace 把 Claude Code 插件装进了 Qoder，安装器会替换这个 `openviking` marketplace，两个插件不会重复捕获同一会话。

## 安装内容

安装器在 `~/.openviking/agent-integrations/qoder/marketplace/openviking-memory/` 下组装自包含插件（共享 hook 运行时与适配器同目录），将其注册为名为 `openviking` 的本地 marketplace，并用 `qodercli plugins install` 安装。插件包含：

- 生命周期 Hook：加载画像、提问时召回、捕获对话、提交 session，以及 `viking://` URI 保护。
- 以 stdio 代理形式提供的 OpenViking MCP 服务，含 `search`、`read`、`remember`、`add_skill` 等工具。
- `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search` 四个 skill，以插件名为前缀列出，例如 `openviking-memory:openviking-skills`。

安装器使用 `~/.qoder`，设置了 `QODER_CONFIG_DIR` 时用它。`PATH` 中只有 `qoderclicn` 时，改用该 CLI 及 `~/.qoder-cn`（`QODERCN_CONFIG_DIR`）。

## 验证

1. 运行 `qodercli plugins list`，确认 `openviking-memory@openviking` 已启用。
2. 新建会话，确认 `openviking` MCP 服务已连接。
3. 告诉 Qoder 一条测试偏好并正常结束会话。记忆抽取完成后，在同一工作区新建会话询问它。

## 工作原理

- **SessionStart** 注入用户画像，并重放离线队列中暂存的写入。
- **UserPromptSubmit** 召回相关记忆，以 `additionalContext` 中的 `<openviking-context>` 块注入。
- **PreToolUse** 拒绝把 `viking://` URI 当本地路径读写，并引导 Agent 改用 OpenViking MCP 工具。Shell 命令不做检查，`ov` 调用与字面量 `viking://` 参数照常可用。
- **Stop**、**PreCompact** 和 **SessionEnd** 在分离的 worker 中增量捕获 transcript 中的新轮次。`Stop` 每捕获 8 条消息（`commitTurnThreshold`）提交一次 OpenViking session；`PreCompact` 和 `SessionEnd` 提交所有尚未提交的消息，包括 `Stop` 已经发送的。

hook 与 MCP 从 `~/.openviking/ovcli.conf` 读取同一份凭据，不会在服务端、账号或用户上各说各话。`OPENVIKING_URL` 与 `OPENVIKING_API_KEY` 可覆盖该文件，按插件的配置位于 `plugin.qoder` 下（见[插件配置](../configuration/02-client.md#插件配置)）。会话在 OpenViking 中记为 `qd-<session id>`。

### 宿主决定的行为

`UserPromptSubmit` 与 `SessionEnd` 在 TUI 和 headless `-p` 运行中都会触发，因此 headless 会话同样会召回并提交。Qoder 把注入的上下文作为独立的 attachment 记录写进 transcript；捕获只读取对话记录，召回的记忆不会被再次捕获。每次 `-p` 运行结束时还会结束一个没有 transcript 的辅助会话，它的 `SessionEnd` 会被忽略。

Qoder 通过 `mcp_call` 懒加载 MCP 工具。在默认权限模式的 headless 运行中，MCP 工具需要权限规则或更宽松的权限模式。插件相关命令请使用 `qodercli`：`qoder` 遇到某些参数会打开 Qoder IDE。

暂不支持子代理采集：宿主会触发 `SubagentStart`/`SubagentStop`，但共享 dispatcher 还没有子代理阶段。经过实测的宿主契约与 transcript 格式记录在 [`hosts/qoder/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/qoder/DESIGN.md)。

## 升级与卸载

重新运行安装命令即可升级。卸载：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness qoder
```

卸载会移除插件、其本地 marketplace、Qoder 缓存的副本以及运行时文件，其他配置保持不变。

## 故障排查

| 现象 | 原因与处理 |
|------|------------|
| `-p` 运行中 MCP 工具请求授权 | 为 `openviking` MCP 服务添加权限规则，或使用更宽松的权限模式。 |
| 插件列表中没有它 | 重新运行安装器并确认 `qodercli` 在 `PATH` 中，然后新建会话。 |
| MCP 连不上 | 检查 `~/.openviking/ovcli.conf` 中的 URL/API Key，然后新建会话。 |
| 需要详细诊断 | 以 `OPENVIKING_DEBUG=1` 启动 Qoder，查看 `~/.openviking/logs/qoder-hooks.log`。 |

## 参见

- [能力参考](./16-capability-reference.md)
- [认证](../guides/04-authentication.md)
- [源码：examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
- [Qoder CLI 插件](https://docs.qoder.com/cli/plugins)
