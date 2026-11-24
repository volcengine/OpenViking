# 连接 BytePlus OpenViking

[BytePlus](https://openviking.ai/byteplus) 在亚太地区（柔佛）托管 OpenViking。使用客户端连接你的托管实例，无需在本机运行 OpenViking 服务端。

## 准备工作

- 一个 BytePlus OpenViking 实例。
- 该实例的服务地址和 OpenViking API Key。请使用包含路径的完整服务地址（如有路径），不要使用火山引擎的地址或密钥。

## 连接 CLI

1. [安装 `ov` CLI](../getting-started/05-cli-setup.md#安装)。
2. 运行 `ov config`。添加**自定义（Custom）**连接，输入 BytePlus 服务地址和 API Key。保存新连接并设为当前配置。
3. 运行 `ov health` 检查连接。要导入并搜索示例文档，请继续[导入示例](../getting-started/02-quickstart.md#导入样例)。

## 连接 Agent

如果使用 [Hermes](../agent-integrations/05-hermes.md#开始使用)，请按 Hermes 集成指南配置。通用安装器适用于菜单中列出的 Agent，包括 Claude Code、Codex 和 Cursor。安装器需要 macOS 或 Linux、Node.js 18 或更高版本，以及 `curl`。

在运行 Agent 的机器上执行：

```bash
curl -fsSL https://openviking.ai/install | bash
```

选择你的 Agent。如果安装器发现已有连接，选择**重新配置**。选择**自定义 URL**，按提示输入 BytePlus 服务地址和 API Key。

安装器会更新共享的 `~/.openviking/ovcli.conf`。CLI 和读取此文件的其他集成也会切换到新连接。重启 Agent 前，请运行 `ov config show` 核对当前服务地址。
