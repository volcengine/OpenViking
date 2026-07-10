## 安装 TRAE 集成

运行与客户端对应的安装命令：

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
2. 在 TRAE 设置中确认 `openviking` 已连接。
3. 新建会话并提问一个与过往项目或个人偏好相关的问题。

完整说明见 [TRAE 接入文档](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/13-trae.md)。

## 故障排查

| 问题 | 处理 |
|---|---|
| 安装后没有自动召回 | 完全退出并重新启动 TRAE，然后新建 Agent 会话。 |
| 出现重复召回 | 重跑安装命令，然后重启 TRAE。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`，然后重启 TRAE。 |
