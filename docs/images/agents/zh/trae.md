## 安装 TRAE 集成

共享安装器会同时配置原生生命周期 Hook 与 OpenViking MCP Server，一条命令即可完成接入。

```bash
# TRAE
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos

# TRAE CN
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos

# 同时安装
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae,trae-cn --dist tos
```

## 验证

1. 安装后重启 TRAE。
2. 在 `~/.trae/hooks.json` 或 `~/.trae-cn/hooks.json` 中确认 `SessionStart`、`UserPromptSubmit`、`Stop`。
3. 在 TRAE 设置中确认 `openviking` MCP Server 已启用。
4. 新建会话并提交一个需要召回历史上下文的 prompt。

完整说明见 [TRAE 接入文档](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/13-trae.md)。

## 故障排查

| 问题 | 处理 |
|---|---|
| Hook 或 MCP 缺失 | 使用正确的 `trae` 或 `trae-cn` harness 重跑共享安装器。 |
| 召回或捕获执行两次 | 清理旧 OpenViking Hook 后重跑安装器。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`，然后重启 TRAE。 |
