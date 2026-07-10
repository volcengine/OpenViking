## 安装 Cursor 集成

运行以下命令完成安装：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

## 验证

1. 重启 Cursor 并新建 Agent 会话。
2. 在 **Cursor Settings → Hooks** 中确认 `cursor-hook.mjs` 已执行。
3. 在 **Cursor Settings → Tools & MCPs** 中确认 `openviking` 已连接。

完整说明见 [Cursor 接入文档](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/12-cursor.md)。

## 故障排查

| 问题 | 处理 |
|---|---|
| 安装后没有触发 Hook | 完全退出并重新启动 Cursor，然后新建 Agent 会话。 |
| Plugins 页面显示 `Get` | 无需操作，请以 Hooks 和 Tools & MCPs 的验证结果为准。 |
| 出现重复召回 | 重跑安装命令，然后重启 Cursor。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`，然后重启 Cursor。 |
| Hook 找不到 Node.js | 确认 Cursor 进程的 `PATH` 中存在 `node`。 |
