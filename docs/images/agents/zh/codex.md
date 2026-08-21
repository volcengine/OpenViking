## 步骤1：安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness codex --dist tos
```

选 **火山引擎 OpenViking 云服务**，把 API Key 贴进去：

## 步骤2：验证

启动 `codex`，用 `/hooks` 审批一次。第一次提交 prompt 时应加载 profile。

## 故障排查

| 问题 | 处理 |
|---|---|
| 鉴权失败 | 检查 `~/.openviking/ovcli.conf` 的 `api_key`，重启 Codex |
| 连接失败 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| `4 hooks need review` | `/hooks` 里批准 |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/codex-hooks.log` |

## 参考

- 手动配置文档：[Codex](https://docs.openviking.net/zh/agent-integrations/04-codex)
- 原理博客：[OpenViking for coding agents](https://blog.openviking.ai/post/openviking-coding-agent/)
- 源码：[examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin)
