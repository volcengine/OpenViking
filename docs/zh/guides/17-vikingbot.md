# VikingBot 安装与配置

VikingBot 是 OpenViking 内置的多渠道 AI Agent。它既可以和 OpenViking 一起启动，也可以在本地独立调试，或作为长期运行的 Gateway 接入聊天平台。

本指南介绍安装方式，以及三种主要使用场景的配置和启动方法。Agent 工具、聊天渠道、架构等完整说明请参见 [VikingBot 中文文档](https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md)。

## 安装

VikingBot 建议使用 Python 3.11 或更高版本。

### 从 PyPI 安装

选择你常用的 Python 包管理工具安装 VikingBot：

::: code-group

```bash [uv（推荐）]
uv tool install "openviking[bot]" --upgrade
```

```bash [pip]
pip install "openviking[bot]" --upgrade --force-reinstall
```

```bash [pipx]
# 安装
pipx install "openviking[bot]"

# 更新
pipx upgrade openviking
```

:::

安装后检查版本：

```bash
vikingbot --version
```

### 从源码安装

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking

uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[bot]"
```

Windows 使用以下命令激活虚拟环境：

```powershell
.venv\Scripts\activate
```

## 配置文件

VikingBot 与 OpenViking 共用 `~/.openviking/ov.conf`。如果配置文件位于其他路径，通过环境变量指定：

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
```

修改配置后，需要重启 VikingBot 或 OpenViking Server 才会生效。

## 选择使用场景

| 场景 | 适用情况 | 启动命令 | OpenViking |
|------|----------|----------|------------|
| **A. OpenViking + Bot 一体启动** | 完整体验资源、记忆和 Agent | `openviking-server --with-bot` | 使用当前启动的 Server |
| **B. 本地调试 Agent** | 快速试用 Bot，开发 Tool 或 Skill | `vikingbot chat` | 可选 |
| **C. Gateway 统一入口** | 长期运行、远程访问或接入聊天平台 | `vikingbot gateway` | 可连接已有 Server，也可 standalone 运行 |

三种场景是不同的运行入口，可以共用同一份 `ov.conf`。

## 场景 A：OpenViking + Bot 一体启动

这是本地完整体验的推荐方式。OpenViking Server 和 VikingBot Gateway 会一起启动：

```text
ov chat → OpenViking Server → VikingBot Gateway → Agent
```

### 1. 配置 OpenViking

首次使用时运行初始化向导，并检查模型和存储配置：

```bash
openviking-server init
openviking-server doctor
```

详细配置见 [OpenViking 配置指南](01-configuration.md)。VikingBot 默认继承根级 `vlm` 作为 Agent 模型，因此通常不需要重复配置 `bot.agents`。

### 2. 一体启动

```bash
openviking-server --with-bot
```

在此模式下，Bot 固定连接当前启动的 OpenViking Server，不使用 `bot.ov_server.server_url` 指向其他服务。

### 3. 配置并使用 `ov` CLI

```bash
ov config
ov chat
ov chat -m "记住我更喜欢简洁的回答"
ov find "我的回答偏好"
```

`ov config` 中的 URL 应指向当前 OpenViking Server，默认是 `http://127.0.0.1:1933`。如果 Server 开启了鉴权，还需要配置当前调用者的 User/Admin API Key。

## 场景 B：本地调试 Agent

适合快速试用 VikingBot，或开发 Agent、Tool 和 Skill。`vikingbot chat` 会在当前进程中直接运行 Agent，不需要先启动 Gateway。

### 1. 配置 Agent 模型

如果 `ov.conf` 已经配置根级 `vlm`，VikingBot 会直接继承。也可以使用独立的 Agent 模型：

```json
{
  "bot": {
    "agents": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "api_key": "<your-model-api-key>"
    }
  }
}
```

### 2. 启动对话

```bash
# 单次调用
vikingbot chat -m "帮我总结当前目录的项目结构"

# 交互式多轮对话
vikingbot chat

# 指定会话
vikingbot chat --session my-session
```

没有可用的 OpenViking Server 时，VikingBot 会以 standalone 方式运行。本地文件、Shell、Web 和 Skill 等能力仍可使用，但不会提供 OpenViking 资源检索和长期记忆能力。

## 场景 C：Gateway 统一入口

适合长期运行、远程访问和接入飞书、Slack、Telegram 等聊天平台。Gateway 提供 Bot HTTP API，也可以代理 OpenViking API，让 `ov` CLI 使用同一个入口。

### 1. 配置 Gateway 和 OpenViking

下面的示例让 Gateway 连接一个已有的 OpenViking Server：

```json
{
  "bot": {
    "agents": {
      "provider": "openai",
      "model": "gpt-4o-mini",
      "api_key": "<your-model-api-key>"
    },
    "gateway": {
      "host": "127.0.0.1",
      "port": 18790
    },
    "ov_server": {
      "server_url": "https://openviking.example.com",
      "api_key": "<bot-openviking-user-api-key>"
    }
  }
}
```

Gateway 有三种 OpenViking 连接状态：

- 配置 `bot.ov_server.server_url`：连接指定的 OpenViking Server；连接失败时拒绝启动。
- 未配置该 URL，但同一份 `ov.conf` 配置了 `server`：继承该 Server 地址；不可用时降级为 standalone。
- 没有可用 Server：Chat 仍可使用，但 OpenViking 工具和 API 代理不可用。

### 2. 启动 Gateway

```bash
vikingbot gateway
```

### 3. 让 `ov` CLI 使用 Gateway

编辑 `~/.openviking/ovcli.conf`：

```json
{
  "url": "http://127.0.0.1:18790",
  "api_key": "<caller-openviking-user-or-admin-api-key>",
  "actor_peer_id": "cli"
}
```

随后 Chat 和 OpenViking 命令都可以通过 Gateway：

```bash
ov chat -m "检索项目资料并给出结论"
ov ls viking://resources/
ov find "项目发布流程"
```

Gateway 默认只监听 `127.0.0.1`。如果改为 `0.0.0.0` 或其他非 localhost 地址，必须配置 `bot.gateway.token`，并在客户端设置对应的 `gateway_token`。

聊天平台的凭证和权限配置见 [VikingBot 渠道配置](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/05-channel.md)。

## 更多文档

- [VikingBot 完整使用说明](https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md)
- [VikingBot 架构](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/01-architecture.md)
- [Agent 能力体系](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/02-agent-capabilities.md)
- [渠道、Gateway 与运行管理](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/03-channels-and-gateway.md)
- [VikingBot 与 OpenViking 集成](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/04-openviking-integration.md)
- [聊天渠道配置](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/concepts/05-channel.md)
