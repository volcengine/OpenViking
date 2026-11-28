# Cursor

为 Cursor 添加跨项目、跨会话的长期记忆。OpenViking Hook 会在会话启动和用户提交问题时注入相关上下文，在回复结束后捕获新对话；MCP 用于主动搜索、读取和管理记忆。

源码：[examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)（Cursor 专属文件在 [hosts/cursor](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/cursor)）

## 安装

前置条件：macOS 或 Linux、Node.js 18+，建议使用最新稳定版 Cursor。

连接托管实例时，选择 **OpenViking 托管服务**，再选择服务商：

- **Volcengine**：输入服务 API Key，安装器会提供服务地址。
- **BytePlus**：输入 API Key。详见 [BytePlus 接入指南](../guides/27-byteplus-setup.md)。

旧版安装器连接 BytePlus 时使用**自定义 URL**。

服务运行在本机时选择 **自建 / 本地**（`http://127.0.0.1:1933`）。远程自建服务选择**自定义 URL**并填写其地址；已有连接时，此选项显示为**自定义 URL / 保持当前**。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agent 如不确定是否执行，可先阅读脚本头部，其中写明了它会做什么、如何核实。
```

安装后完全退出并重启 Cursor。安装脚本会添加 hooks、`openviking` MCP 服务器、一条常驻 Rule，以及 skill `openviking-memory`、`openviking-skills`、`ov-experience-memory`、`openviking-rule-search` 和 `ov-memory-doctor`。

安装完成后，新开一个 Cursor 会话，让它按 https://docs.openviking.net/agents/zh/onboarding.md 操作。它会检查连接，并带你完成首次使用；参见[一键接入](../getting-started/07-agent-onboarding.md)。

## 验证

1. 重启 Cursor 并新建 Agent 会话。
2. 打开 **Cursor Settings → Hooks**，确认 OpenViking hooks 执行 `scripts/hook.mjs`，URI 防护 hook 执行 `scripts/uri-guard.mjs`。
3. 打开 **Cursor Settings → Tools & MCPs**，确认 `openviking` 已连接。
4. 询问已有的项目或偏好，确认回答用到了已存储的记忆。需要诊断时，用 `OPENVIKING_DEBUG=1` 启动 Cursor 并查看 `~/.openviking/logs/cursor-hooks.log`。

## Cursor 上的不同之处

- **没有可用的会话结束事件。** Cursor 注册了 `sessionEnd`，但它只在关闭窗口时触发，那时 Cursor 已不再运行 hook 命令，所以实际上不会提交。插件不会提交会话的最后一段。请在服务端开启 `memory.session_auto_commit.enabled`，由空闲提交兜底。见[退出时会发生什么](./16-capability-reference.md#what-happens-at-exit)。
- **工具结果随轮次一起捕获。** Cursor 自身的工具通过 `postToolUse` / `postToolUseFailure` 上报，MCP 调用通过 `afterMCPExecution` 上报；每个结果以 tool part 捕获，带调用 ID、输入、输出和成功或失败状态。缺少调用 ID 或工具名的结果会被跳过。
- **`viking://` 防护**：`beforeReadFile` 拒绝把 `viking://` 路径当作本地文件读取，并引导 Agent 使用 OpenViking MCP 工具。
- **`session_history`**：`preToolUse` 只匹配 `MCP:session_history`，Agent 未传 `session` 时补上当前对话。
- **不捕获子 agent。**

其余内容见[能力参考](./16-capability-reference.md)。

## 升级与卸载

重新运行安装命令即可升级。卸载命令：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness cursor
```

卸载只移除 OpenViking 管理的 Cursor hooks、MCP、Rule、skill 和运行时文件，其他配置保持不变。

## 故障排查

| 现象 | 原因和处理 |
|------|------------|
| hooks 没有运行 | 完全退出并重启 Cursor，然后新建 Agent 会话。 |
| hook 输出中有召回结果，但回答没有使用 | 升级到最新稳定版 Cursor；旧版本可能不支持 `beforeSubmitPrompt.additional_context`。 |
| 同一个事件运行了多个 OpenViking hook | Cursor 可能导入了旧版 Claude Code 插件。移除安装脚本报告的旧 OpenViking 插件 id，然后重启 Cursor。 |
| MCP 无法连接 | 检查 `~/.openviking/ovcli.conf` 中的 URL/API Key，然后重启 Cursor。 |

## 参见

- [能力参考](./16-capability-reference.md)
- [认证](../guides/04-authentication.md)
- [Cursor Hooks 文档](https://cursor.com/docs/hooks)
