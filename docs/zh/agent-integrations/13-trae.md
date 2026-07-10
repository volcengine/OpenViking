# TRAE 与 TRAE CN 记忆集成

为 TRAE 和 TRAE CN 添加跨项目、跨会话的长期记忆。安装后会自动召回相关记忆、保存新的对话内容，并提供 OpenViking 记忆工具。

## 安装

```bash
# TRAE
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae

# TRAE CN
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae-cn

# 同时安装
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) --harness trae,trae-cn
```

GitHub 访问受限时，可使用火山引擎 TOS 镜像：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos
```

安装完成后，重启 TRAE。

## 功能

- 新会话启动时加载用户画像和项目记忆。
- 根据当前问题自动召回相关内容。
- 对话结束后自动保存新的用户与助手消息。
- 支持通过 OpenViking 工具主动搜索和管理记忆。

## 验证

1. 重启 TRAE 并新建 Agent 会话。
2. 提问一个与过往项目或个人偏好相关的问题，确认 TRAE 能使用已有记忆回答。
3. 在 TRAE 的 MCP 设置中确认 `openviking` 已连接。

## 升级与卸载

重复运行对应安装命令即可升级。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes
```

将 `trae-cn` 替换为 `trae` 可卸载 TRAE 集成。

## 故障排查

| 现象 | 处理 |
|------|------|
| 安装后没有自动召回 | 完全退出并重新启动 TRAE，然后新建 Agent 会话。 |
| 出现重复召回 | 重跑安装命令，然后重启 TRAE。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf` 中的服务地址和 API Key。 |
