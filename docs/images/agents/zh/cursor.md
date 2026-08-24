## 步骤1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

选 **火山引擎 OpenViking 云服务**，把 API Key 贴进去：

{{OPENVIKING_API_KEY_BLOCK}}

## 步骤2：验证

1. 重启 Cursor，新建 Agent 会话。
2. **Cursor Settings → Hooks**：生命周期 Hook 执行 `cursor-hook.mjs`。
3. **Cursor Settings → Tools & MCPs**：`openviking` 已连接。

## 故障排查

| 问题 | 处理 |
|---|---|
| Hook 没跑 | 完全退出 Cursor，重启，再建会话 |
| 连接 / 鉴权失败 | 检查 `~/.openviking/ovcli.conf`，重启 Cursor |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/cursor-hooks.log` |

## 参考

- 手动配置文档：[Cursor](https://docs.openviking.net/zh/agent-integrations/12-cursor)
- 源码：[examples/cursor-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/cursor-memory-plugin)
