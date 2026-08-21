## 步骤1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness opencode --dist tos
```

选 **火山引擎 OpenViking 云服务**，把 API Key 贴进去：

## 步骤2：验证

重启 OpenCode，让它搜索 OpenViking 记忆。工具名类似 `openviking_search`、`openviking_read`、`openviking_remember`。

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件没加载 | 检查 `~/.config/opencode/opencode.json` 是否包含 `@openviking/opencode-plugin` |
| 连错服务 / 401 | 检查 `~/.openviking/ovcli.conf` 和 API Key |
| 召回为空 | 确认云端实例里已有记忆 |

## 参考

- 手动配置文档：[OpenCode](https://docs.openviking.net/zh/agent-integrations/10-opencode)
- 源码：[examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
