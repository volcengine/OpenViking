# OpenViking SuperAgent

**Bring Your Own Machine：用你自己的机器，运行你自己的 Agent。**

准备一台电脑或服务器，装好工作需要的软件，把项目和工具交给本机 Agent。OpenViking SuperAgent（Vika）让 Agent 接入 OpenViking 和即时通讯（IM）：你在聊天里交办任务，Agent 在这台机器的工作区中调用工具、完成工作，再把结果发回聊天；OpenViking 提供知识、记忆和跨会话上下文。

Vika 支持 Codex、Claude Code、Pi 等运行时，可接入飞书、微信、Telegram、Slack、QQ，也能处理邮箱事件。命令仍是 `vika`。

[快速安装](install.md) · [功能与架构](docs/usage-reference.md) · [配置示例](config.example.json) · [贡献指南](../CONTRIBUTING.md)

## 为什么用它

- **在聊天里交办任务。** 调试代码、修改文档、整理资料，不必把需求反复复制到终端。
- **沿用你的 Agent。** 使用本机已登录的 coding agent，或内置的 Pi / Durable。→ [运行时配置](docs/installation-reference.md#准备)
- **上下文可以延续。** OpenViking 保存会话历史，供新会话和运行时切换时读取；也提供长期记忆与资料检索。→ [会话上下文](docs/ov-session-context.md)
- **多个入口，共同协作。** 同一实例可连接多个聊天渠道；多个实例各有工作区、数据目录和 OV 配置。→ [多实例](docs/installation-reference.md#多个-agent)

## 快速开始

需要一台 macOS 或 Linux 机器、一个可用的模型运行时，以及 **OpenViking 服务（本机或远端均可，必需）**。Windows 当前发布版使用 WSL2。

```bash
curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/vika/install.sh | sh
```

安装包自带 Node、pm2、聊天 CLI、Pi 和 Durable。首次安装进入 `vika setup`：连接 OV，选择运行时和工作目录，再授权聊天渠道。无需提前全局安装 lark-cli；使用 Codex 或 Claude Code 时需先安装并登录对应 CLI。

配置完成后，在机器人私聊中发送一个任务，例如：

> 查看工作区里的项目，解释启动方式，并检查测试能否通过。

详见 [安装指南](install.md)。

## 日常使用

在机器人私聊里发 `/help` 查看命令；`/status` 查看运行状态，`/model` 和 `/effort` 查看或调整模型与思考档位，`/new` 开启新会话。终端里运行：

```bash
vika setup           # 添加或修改实例
vika status
vika logs primary
vika upgrade primary
```

控制台位于 `http://127.0.0.1:3737`，可查看消息处理与工具活动。

**只接入可信聊天。** Agent 能操作配置的工作区，同一实例的聊天共享运行时上下文。飞书 setup 会让你选择是否授权以本人身份搜索和读取消息；权限范围请在授权前确认。→ [消息访问权限](docs/usage-reference.md#机器人能读到哪些消息)

## 文档

| 你想了解 | 文档 |
|---|---|
| 安装、模型登录、首次接入 | [安装指南](install.md) |
| 各聊天渠道、邮箱、附件与卡片 | [功能参考](docs/usage-reference.md#安装) |
| 权限、重新登录、升级回滚、源码开发 | [配置与运维参考](docs/installation-reference.md) |
| 上下文归档与恢复 | [OV session context](docs/ov-session-context.md) |
| 接入新消息源 | [Connector protocol](docs/connector-protocol.md) |
| Durable 的模型与恢复机制 | [Durable runtime](docs/pi-durable-runtime.md) |
| 可靠性限制与验收 | [可靠性边界](docs/usage-reference.md#可靠性边界) · [飞书验收](docs/usage-reference.md#飞书端验收) |

源码中的能力可能尚未发布，以安装的 release 为准。
