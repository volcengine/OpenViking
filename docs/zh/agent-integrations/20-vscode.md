# VS Code

通过 VS Code 的 [Local hooks](https://code.visualstudio.com/docs/agent-customization/hooks) 能力，让 VS Code 的 Agent 获得跨项目、跨会话的长期记忆。OpenViking 的 hook 会在会话开始时加载你的记忆画像，捕获你提交的每条 prompt 并立即 commit 进行记忆抽取——因为 VS Code 没有会话结束 hook。

## 首版范围

VS Code 的 hooks 处于 Preview 阶段，若干能力刻意比其他集成更窄。本集成只覆盖有文档的 Local hook 契约：

- `SessionStart` 注入你的记忆画像——这是 [hooks 参考](https://code.visualstudio.com/docs/agents/reference/hooks-reference) 中唯一文档化支持 `additionalContext` 的事件。
- `UserPromptSubmit` 无法注入上下文（只接受 common output 格式），因此该宿主暂时没有自动召回通道。hook 仍会记录 prompt 以供捕获。
- `Stop` 与 `PreCompact` 捕获已提交的 prompt 并 commit。VS Code 在这些事件上不提供 assistant 响应，且其 transcript 文件格式被明示为不稳定，因此 assistant 侧暂不捕获。
- `PreToolUse` 拒绝对 `viking://` 虚拟路径的本地文件工具调用，并把 Agent 指回 OpenViking MCP 工具；携带 `viking://` 的终端命令仍会执行，但会附加指向同样工具的提示。
- 安装器不配置 MCP。如需 MCP 工具，请自行将 OpenViking 服务加入工作区的 `.vscode/mcp.json`。

VS Code 内 Provider 托管的 Copilot、Claude 与 Codex 会话有各自独立的 hook 契约，不在本集成范围内。

## 安装

前置条件：macOS 或 Linux、Node.js 18+，以及一个支持 agent hooks 的 VS Code 版本（`chat.useHooks` 设置，默认开启）。安装器会引导你完成 OpenViking 连接配置。

连接配置时，火山引擎云用户请选择 **Volcengine OpenViking Cloud** 并输入 API Key。本机自建服务选择 **Self-hosted / local**（`http://127.0.0.1:1933`）；远程自建服务选择 **Custom URL / keep current** 并输入地址。

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: 不确定能否直接运行？脚本头部说明了它做什么、如何验证。
```

安装器询问要安装哪些 harness 时，选择 **VS Code**，或直接指定：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --harness vscode --url <服务地址> --api-key <api-key>
```

## 安装了什么

- `~/.copilot/hooks/openviking.json` — VS Code 跨 Local 会话读取的用户级 Local hook 文件，含 `SessionStart`、`UserPromptSubmit`、`PreToolUse`、`Stop`、`PreCompact` 条目。VS Code 在下次窗口重载时加载它；用户级 Local hook 文件无需 hook 审批。
- `~/.openviking/agent-integrations/vscode/` — hook 文件指向的运行时。OpenViking 连接配置在 `~/.openviking/ovcli.conf`，与其他集成共用。

## 验证

1. 重载 VS Code 窗口并新建 Agent 会话。
2. 查看 OpenViking hook 日志（见下），确认首条 prompt 之后出现 `SessionStart` 记录。
3. 告诉 Agent 一条临时偏好，确认 `Stop` hook 日志中出现捕获与 commit。
4. 等记忆抽取完成后，在同一工作区新开会话并问起该偏好；`SessionStart` 注入的画像应已包含它。
5. 让 Agent 用文件工具读取 `viking://resources/project/file.md`，确认调用被拒绝并指向 OpenViking MCP 工具。

Hook 诊断：以 `OPENVIKING_DEBUG=1` 启动 VS Code（例如 `OPENVIKING_DEBUG=1 code .`），查看 `~/.openviking/logs/vscode-hooks.log`。

## 升级与卸载

重新运行安装命令即可升级。卸载：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness vscode
```

## 故障排查

| 现象 | 原因与处理 |
|------|------------|
| 没有 hook 日志 | 安装后 VS Code 尚未重载，或 `chat.useHooks` 被关闭。重载窗口并检查该设置。 |
| 聊天中没有召回内容 | 目前属预期：VS Code 的 `UserPromptSubmit` 无法携带注入上下文，召回只能通过 `SessionStart` 画像进入会话。 |
| 回答没有用到已存记忆 | 确认上一会话的 `Stop` hook 已 commit（看 hook 日志），并等记忆抽取完成后再在新会话中提问。 |
| MCP 工具缺失 | 安装器不为 VS Code 配置 MCP；按 [MCP 客户端](./06-mcp-clients.md) 自行把 OpenViking 服务加入 `.vscode/mcp.json`。 |

## 另见

- [集成能力参考](./16-capability-reference.md)
- [鉴权](../guides/04-authentication.md)
