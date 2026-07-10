## 安装 Cursor Plugin

OpenViking Cursor Plugin 内部已经包含生命周期 Hook、OpenViking MCP Server、always-on Rule 和记忆 Skill。安装 Plugin 即完成全部接入，不要再手动添加 MCP Server。

`openviking-memory` 上架 Cursor Marketplace 后，可在 Plugins/Customize 页面安装，或执行：

```text
/add-plugin openviking-memory
```

Marketplace 发布前可使用兼容安装器，它会自动同时配置 Hook 与 MCP：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

## 验证

Plugin 安装方式：

1. 在 Cursor Plugins/Customize 页面确认 `openviking-memory` 已启用。
2. 确认 Plugin 提供的 OpenViking Hook 与 MCP Server 处于活动状态。
3. 新建 Agent 会话并执行一次会使用工具的请求。

兼容安装器方式：

1. 确认 `~/.cursor/hooks.json` 中存在 `cursor-hook.mjs`。
2. 确认 `~/.cursor/mcp.json` 中存在 `openviking` server。
3. 重启 Cursor 并新建 Agent 会话。

完整说明见 [Cursor 接入文档](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/12-cursor.md)。

## 故障排查

| 问题 | 处理 |
|---|---|
| 出现两个 OpenViking MCP 或重复召回 | Plugin 与兼容安装器只保留一种，不要同时启用。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`，然后重启 Cursor。 |
| Hook 找不到 Node.js | 确认 Cursor 进程的 `PATH` 中存在 `node`。 |
