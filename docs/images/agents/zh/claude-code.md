## 步骤1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness claude --dist tos
```

选 **火山引擎 OpenViking 云服务**，把 API Key 贴进去：

## 步骤2：验证

重启 Claude Code，然后：

- `/plugins` → **openviking-memory** 已安装，**openviking** MCP 已连接
- `/mcp` → 显示云端 URL 且鉴权有效
- `/openviking-memory:ov` → 服务健康

## 故障排查

| 问题 | 处理 |
|---|---|
| 插件未激活 | 重跑安装，或检查 `~/.openviking/ovcli.conf` |
| 召回为空 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| 401 / 403 | 重新粘贴 API Key |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/cc-hooks.log` |

## 参考

- 手动配置文档：[Claude Code](https://docs.openviking.net/zh/agent-integrations/02-claude-code)
- 原理博客：[OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- 源码：[examples/claude-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin)
