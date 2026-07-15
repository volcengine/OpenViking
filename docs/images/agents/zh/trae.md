## 安装 TRAE 集成

需要 macOS/Linux 和 Node.js 18+。运行与客户端对应的安装命令，Hook 和 MCP 会同时配置：

```bash
# TRAE
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos

# TRAE CN
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos

# 同时安装
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae,trae-cn --dist tos
```

安装器询问连接方式时，请选择 **火山引擎 OpenViking 云服务** 并填写 API Key。只有本机已运行 OpenViking 服务时才选择 **自建 / 本地**。

## 验证

1. 安装后重启 TRAE。
2. 在 TRAE 设置中确认 `openviking` 已连接。
3. 新建会话并提问一个与过往项目或个人偏好相关的问题。
4. 告诉 Agent 一个临时偏好；下一会话再次询问，验证捕获和提交。

完整说明见 [TRAE 接入文档](https://github.com/volcengine/OpenViking/blob/main/docs/zh/agent-integrations/13-trae.md)。

## 故障排查

| 问题 | 处理 |
|---|---|
| 安装后没有自动召回 | 完全退出并重新启动 TRAE，然后新建 Agent 会话。 |
| 新会话无法回忆上一轮 | 查看 `~/.openviking/logs/trae-hooks.log` 或 `trae-cn-hooks.log`，确认 Stop 提交成功。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf`，然后重启 TRAE。 |
