## 步骤1：安装

```bash
# TRAE
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos

# TRAE CN
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos
```

选 **火山引擎 OpenViking 云服务**，把 API Key 贴进去：

{{OPENVIKING_API_KEY_BLOCK}}

## 步骤2：验证

重启 TRAE。在设置里确认 `openviking` 已连接。

## 故障排查

| 问题 | 处理 |
|---|---|
| 没有自动召回 | 完全退出 TRAE，重启，再建会话 |
| 连接 / 鉴权失败 | 检查 `~/.openviking/ovcli.conf`，重启 TRAE |
| 需要日志 | `~/.openviking/logs/trae-hooks.log` 或 `trae-cn-hooks.log` |

## 参考

- 手动配置文档：[TRAE](https://docs.openviking.net/zh/agent-integrations/13-trae)
- 源码：[examples/trae-memory-hooks](https://github.com/volcengine/OpenViking/tree/main/examples/trae-memory-hooks)
