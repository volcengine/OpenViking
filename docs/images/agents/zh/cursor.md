## 安装 Cursor 集成

需要 macOS/Linux 和 Node.js 18+。以下命令会一次完成 Hook、MCP、Rule 和 Skill 安装：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

安装器询问连接方式时，请选择 **火山引擎 OpenViking 云服务** 并填写 API Key。只有本机已运行 OpenViking 服务时才选择 **自建 / 本地**。

## 验证

1. 重启 Cursor 并新建 Agent 会话。
2. 在 **Cursor Settings → Hooks** 中确认生命周期 Hook 执行了 `cursor-hook.mjs`、URI 保护 Hook 执行了 `uri-guard.mjs`，且 prompt Hook 返回 `additional_context`。
3. 在 **Cursor Settings → Tools & MCPs** 中确认 `openviking` 已连接。

完整说明见 [Cursor 接入文档](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/12-cursor.md)。

## 故障排查

| 问题 | 处理 |
|---|---|
| 安装后没有触发 Hook | 完全退出并重新启动 Cursor，然后新建 Agent 会话。 |
| 出现重复召回 | 检查 Execution Log 是否导入了旧 Claude OpenViking Hook，并按安装器提示升级或移除旧插件。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`，然后重启 Cursor。 |
