# Cursor 记忆集成

为 Cursor 添加跨项目、跨会话的长期记忆。安装完成后，OpenViking Hook 会在会话启动和用户提交问题时注入相关上下文，在回复结束后捕获新对话；MCP 仅用于主动搜索、读取和管理记忆。

## 安装

前置条件：macOS 或 Linux、Node.js 18+，并建议使用最新稳定版 Cursor。安装过程中会引导配置 OpenViking 连接信息。

安装器询问连接方式时，火山引擎云服务用户请选择 **火山引擎 OpenViking 云服务** 并填写 API Key。只有本机已运行 OpenViking 服务时才选择 **自建 / 本地**。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

GitHub 访问受限时使用 TOS 镜像：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

安装完成后完全退出并重新启动 Cursor。

## 安装内容

- 生命周期 Hook：自动加载画像、按问题召回、捕获对话、提交会话并保护 `viking://` URI。
- OpenViking MCP Server：提供 `search`、`recall`、`read`、`remember` 等工具。
- always-on Rule 和记忆 Skill：告诉 Agent 如何使用已注入的上下文和记忆工具。

## 验证

1. 重启 Cursor 并新建 Agent 会话。
2. 打开 **Cursor Settings → Hooks**，确认 OpenViking 生命周期 Hook 执行了 `cursor-hook.mjs`，URI 保护 Hook 执行了 `uri-guard.mjs`。
3. 查看 `beforeSubmitPrompt` 输出，确认存在 `additional_context`；这表示当前问题的召回结果已直接交给 Agent，无需先调用 MCP。
4. 打开 **Cursor Settings → Tools & MCPs**，确认 `openviking` 已连接。
5. 告诉 Cursor 一个临时偏好，等待本轮回复完成；新建会话后询问该偏好，确认捕获和跨会话召回均生效。

## 工作原理

- `sessionStart`：加载用户画像和当前项目的记忆索引。
- `beforeSubmitPrompt`：根据当前问题召回记忆并通过 `additional_context` 注入。
- `beforeReadFile` 和 `beforeShellExecution`：阻止把 `viking://` 虚拟路径当作本地文件访问，并提示改用 OpenViking MCP 工具。
- `stop`：增量捕获本轮新增的用户与助手消息。
- `preCompact` / `sessionEnd`：提交尚未处理的消息，触发记忆抽取。

项目身份优先使用 Cursor 提供的 `workspace_roots`，因此不同项目会使用不同的 workspace peer。连接信息统一读取 `~/.openviking/ovcli.conf`。

## 升级与卸载

重复运行对应渠道的安装命令即可升级。卸载时也应使用原安装渠道：

```bash
# GitHub
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes

# TOS
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

卸载仅移除 OpenViking 管理的 Cursor Hook、MCP、Rule、Skill 和运行文件，保留其他配置。

## 故障排查

| 现象 | 原因与处理 |
|------|-----------|
| Hook 没有触发 | 完全退出 Cursor 后重新启动，并新建 Agent 会话。 |
| Hook 返回召回内容，但回答未使用 | 更新到最新稳定版 Cursor；旧版本可能不支持 `beforeSubmitPrompt.additional_context`。 |
| 同一事件出现多个 OpenViking Hook | Cursor 可能导入了旧 Claude Code 插件。升级或移除安装器列出的旧 OpenViking plugin id，然后重启 Cursor。 |
| MCP 未连接 | 检查 `~/.openviking/ovcli.conf` 中的 URL/API Key，并重启 Cursor。 |
| 需要详细日志 | 设置 `OPENVIKING_DEBUG=1` 后启动 Cursor，查看 `~/.openviking/logs/cursor-hooks.log`。 |

## 参见

- [鉴权](../guides/04-authentication.md)
- [Cursor Hooks 文档](https://cursor.com/docs/hooks)
