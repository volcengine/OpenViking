## 安装

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness codex --dist tos
```

选 **火山引擎 OpenViking 云服务**，把本页 API Key 贴进去。

## 验证

启动 `codex`，用 `/hooks` 审批一次。第一次提交 prompt 时应加载 profile。

## 故障排查

| 问题 | 处理 |
|---|---|
| 鉴权失败 | 检查 `~/.openviking/ovcli.conf` 的 `api_key`，重启 Codex |
| 连接失败 | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| `4 hooks need review` | `/hooks` 里批准 |
| 需要日志 | `OPENVIKING_DEBUG=1`，看 `~/.openviking/logs/codex-hooks.log` |
