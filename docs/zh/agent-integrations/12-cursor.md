# Cursor 记忆集成

为 Cursor 添加跨项目、跨会话的长期记忆。安装后，Cursor 会自动召回相关记忆、记录新的对话内容，并提供 OpenViking 记忆工具。

## 安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

GitHub 访问受限时，可使用火山引擎 TOS 镜像：

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

安装完成后，重启 Cursor。

## 功能

- 新会话启动时加载用户画像和项目记忆。
- 根据当前问题自动召回相关内容。
- 对话结束后自动保存新的用户与助手消息。
- 支持通过 OpenViking 工具主动搜索和管理记忆。

## 验证

1. 重启 Cursor 并新建 Agent 会话。
2. 打开 **Cursor Settings → Hooks**，确认 Execution Log 中出现 `cursor-hook.mjs`。
3. 打开 **Cursor Settings → Tools & MCPs**，确认 `openviking` 已连接。
4. 提问一个与过往项目或个人偏好相关的问题，确认 Cursor 能使用已有记忆回答。

## 升级与卸载

重复运行安装命令即可升级。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

## 故障排查

| 现象 | 处理 |
|------|------|
| 安装后没有触发 Hook | 完全退出并重新启动 Cursor，然后新建 Agent 会话。 |
| Plugins 页面显示 `openviking-memory` 的 `Get` 按钮 | 无需操作，请以 Hooks 和 Tools & MCPs 的验证结果为准。 |
| 出现重复召回 | 重跑安装命令，然后重启 Cursor。 |
| 连接或鉴权失败 | 检查 `~/.openviking/ovcli.conf` 中的服务地址和 API Key。 |

## 参见

- [鉴权](../guides/04-authentication.md)
- [Cursor Hooks 文档](https://cursor.com/docs/hooks)
