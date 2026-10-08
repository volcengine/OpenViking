# Qoder CLI

为 Qoder CLI 添加跨项目、跨会话的长期记忆。OpenViking Hook 会在会话启动时加载画像，在每次提交 prompt 前召回相关记忆，并在 `Stop` 时捕获本轮对话。OpenViking MCP 工具继续用于主动搜索、读取和管理记忆。

## 安装

前置条件：macOS 或 Linux、Node.js 18+、Qoder CLI，以及正在运行的 OpenViking 服务。

```bash
curl -fsSL https://openviking.ai/install | bash
# 安装器询问要配置的工具时，选择 Qoder CLI。
```

如需跳过选择菜单并仅安装此集成，请在安装命令中添加 `--harness qoder`。安装完成后重启 Qoder CLI。

## 安装内容

安装器会保留无关设置，并在 `${QODER_CONFIG_DIR:-~/.qoder}` 下写入以下内容：

- `settings.json`：`SessionStart`、`UserPromptSubmit`、`Stop` Hook，以及 `openviking` MCP server。
- `skills/`：`openviking-memory`、`openviking-skills` 与 `ov-experience-memory`。
- `~/.openviking/agent-integrations/qoder/`：共享 Hook 运行时与 MCP proxy。

如果 `mcpServers.openviking` 已存在且不由本安装器管理，安装会停止，不会覆盖该配置。

## 验证

1. 重启 Qoder CLI 并新建会话。
2. 确认 `settings.json` 包含三个 OpenViking Hook 条目和 `mcpServers.openviking`。
3. 确认 OpenViking MCP 工具可用。
4. 告诉 Qoder 一个测试偏好并完成本轮。设置 `OPENVIKING_DEBUG=1` 后，在 `~/.openviking/logs/qoder-hooks.log` 中确认捕获和提交。
5. 等待记忆提取完成，在同一项目中新建会话并询问该偏好。

## 工作原理

- `SessionStart` 注入用户画像、记忆索引和可用的 OpenViking skill。
- `UserPromptSubmit` 根据 prompt 召回上下文，并通过 `hookSpecificOutput.additionalContext` 返回。
- `Stop` 从 Qoder 的 JSONL transcript 中读取新增的用户和助手消息，写入以 `qd-` 开头的 OpenViking session，并在捕获到新消息时 commit。

Hook 与 MCP 共用 `~/.openviking/ovcli.conf` 中的连接和身份配置。Qoder 使用非默认配置目录时，请设置 `QODER_CONFIG_DIR`。此集成不添加 `PreToolUse` Hook。

## 升级与卸载

重复运行安装器即可升级。只移除 OpenViking 管理的 Qoder 条目和文件：

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness qoder
```

其他 Qoder 设置、Hook、MCP server 与 Skill 会保留。

## 故障排查

| 现象 | 原因与处理 |
|------|-----------|
| Hook 未执行 | 重启 Qoder CLI 并新建会话，然后检查 `settings.json` 中的 Hook 命令。 |
| Hook 返回召回内容，但回答未使用 | 确认 Hook 响应包含 `hookSpecificOutput.additionalContext`，并升级 Qoder CLI。 |
| MCP 未连接 | 检查 `~/.openviking/ovcli.conf` 中的 URL 与 API Key，然后重启 Qoder CLI。 |
| 安装器报告 `openviking` server 已存在 | 重命名或移除外部 `mcpServers.openviking` 条目，或保留它并跳过本集成的 MCP proxy。 |

## 参见

- [集成能力参考](./16-capability-reference.md)
- [鉴权](../guides/04-authentication.md)
- [Qoder CLI Hooks](https://docs.qoder.com/cli/hooks)
- [Qoder CLI MCP 参考](https://docs.qoder.com/cli/mcp-reference.md)
