## 安装 Cursor Plugin

OpenViking Cursor Plugin 内部包含生命周期 Hook、OpenViking MCP Server、always-on Rule 和记忆 Skill。一条命令安装完整 Plugin：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

## 验证

1. 确认 `~/.cursor/hooks.json` 中存在 `cursor-hook.mjs`。
2. 确认 `~/.cursor/mcp.json` 中存在 `openviking` server。
3. 确认 `~/.cursor/rules/openviking-memory.mdc` 与 `~/.cursor/skills/openviking-memory/SKILL.md` 存在。
4. 重启 Cursor 并新建 Agent 会话。

完整说明见 [Cursor 接入文档](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/12-cursor.md)。

## 故障排查

| 问题 | 处理 |
|---|---|
| 出现两个 OpenViking MCP 或重复召回 | 重跑安装器迁移旧 OpenViking 条目，然后重启 Cursor。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`，然后重启 Cursor。 |
| Hook 找不到 Node.js | 确认 Cursor 进程的 `PATH` 中存在 `node`。 |
