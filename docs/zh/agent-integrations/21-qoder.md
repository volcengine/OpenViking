# Qoder CLI

为 Qoder CLI 添加跨项目、跨会话的长期记忆。安装完成后，OpenViking Hook 会在会话启动和用户提交问题时注入相关上下文，在回复结束后捕获新对话；MCP 用于主动搜索、读取和管理记忆。

## 安装

前置条件：macOS 或 Linux、Node.js 18+，以及 `qodercli`（或国内版 `qoderclicn`）在 `PATH` 中的 Qoder CLI。安装过程中会引导配置 OpenViking 连接信息；询问要接入哪些 Agent 时选择 **Qoder CLI**。

连接托管实例时，选择 **OpenViking 托管服务**，再选择服务商：

- **Volcengine**：输入服务 API Key，安装器会提供服务地址。
- **BytePlus**：输入 API Key。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

旧版安装器连接 BytePlus 时使用**自定义 URL**。

服务运行在本机时选择 **自建 / 本地**（`http://127.0.0.1:1933`）。远程自建服务选择**自定义 URL**并填写其地址；已有连接时，此选项显示为**自定义 URL / 保持当前**。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装后新建一个 `qodercli` 会话生效。

安装完成后，在 Qoder 中新开一个会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 执行。它会检查连接，并带你完成首次使用，详见[一键接入](../getting-started/07-agent-onboarding.md)。

如果之前从 OpenViking 的 GitHub marketplace 把 Claude Code 插件装进了 Qoder，安装器会替换这个 `openviking` marketplace，两个插件不会重复捕获同一会话。

## 安装内容

安装器在 `~/.openviking/agent-integrations/qoder/marketplace/openviking-memory/` 下组装自包含插件，将其注册为名为 `openviking` 的本地 marketplace，并用 `qodercli plugins install` 安装。插件包含生命周期 Hook、以 stdio 代理形式提供的 OpenViking MCP 服务（含 `search`、`read`、`remember`、`add_skill` 等工具），以及 `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search`、`ov-memory-doctor` 五个 skill，以插件名为前缀列出，例如 `openviking-memory:openviking-skills`。

`PATH` 中的每个 Qoder CLI 版本都会装上插件：`qodercli` 装在 `~/.qoder`（`QODER_CONFIG_DIR`），国内版 `qoderclicn` 装在 `~/.qoder-cn`（`QODERCN_CONFIG_DIR`）。卸载时两处都会移除。

hook 与 MCP 从 `~/.openviking/ovcli.conf` 读取同一份凭据。`OPENVIKING_URL` 与 `OPENVIKING_API_KEY` 可覆盖该文件，按插件的配置位于 `plugin.qoder` 下（见[插件配置](../configuration/02-client.md#插件配置)）。会话在 OpenViking 中记为 `qd-<session id>`。

## 验证

1. 运行 `qodercli plugins list`，确认 `openviking-memory@openviking` 已启用。
2. 新建会话，确认 `openviking` MCP 服务已连接。
3. 告诉 Qoder 一条测试偏好并正常结束会话。记忆抽取完成后，在同一工作区新建会话询问它。

## 宿主特有的行为

Qoder 注册 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`Stop`、`PreCompact`、`SessionEnd`。捕获、提交、召回、离线队列等共享行为见[能力参考](./16-capability-reference.md)。

- `UserPromptSubmit` 与 `SessionEnd` 在 TUI 和 headless `-p` 运行中都会触发。每次 `-p` 运行还会结束一个没有 transcript 的辅助会话，不会从中捕获任何内容。
- Qoder 懒加载 MCP 工具：模型通过 `mcp_call` 调用它们，因此 URI 保护的拒绝信息给出的是 `mcp_call` 写法。在默认权限模式的 headless 运行中，MCP 工具需要权限规则或更宽松的权限模式。
- URI 保护覆盖 `Read|Write|Edit|Glob|Grep|Bash`：文件工具访问 `viking://` URI 会被拒绝，包含这种 URI 的 Bash 命令照常执行并附加一条提示。`mcp_call` 上的第二个 `PreToolUse` 条目在 `session_history` 调用没传 session 时补上当前会话。
- Qoder 把注入的上下文作为独立的 attachment 记录写进 transcript；捕获只读取对话记录，召回的记忆不会被再次捕获。
- 插件相关命令请使用 `qodercli`：`qoder` 遇到某些参数会打开 Qoder IDE。
- 不采集子代理。

经过实测的宿主契约与 transcript 格式记录在 [`hosts/qoder/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/qoder/DESIGN.md)。

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
