# VikingBot

VikingBot 是 OpenViking 内置的多渠道 AI Agent。它可以在命令行中直接使用，也可以作为长期运行的 Gateway 接入飞书、Slack、Telegram 等平台；连接 OpenViking 后，还能使用资源检索、用户记忆、经验记忆和会话沉淀能力。

## 主要能力

- **多入口对话**：支持 `vikingbot chat`、`ov chat`、HTTP API 和多个聊天平台。
- **Agent 工具**：内置文件、Shell、Web、图片生成、定时任务和 OpenViking 工具。
- **Skill 与子 Agent**：按需加载 Skill，可使用后台子 Agent 处理独立任务。
- **长期上下文**：从 OpenViking 召回 Resource、Peer Memory 和 Experience，并自动提交会话。
- **安全执行**：支持 Direct、SRT、OpenSandbox 和 AIO Sandbox 后端。
- **服务化运行**：Gateway 提供同步 Chat API、SSE 流式事件、反馈和 OpenViking API 代理。

## 安装

### 从 PyPI 安装

```bash
pip install "openviking[bot]"
```

### 从源码安装

需要 Python 3.11 或更高版本，并建议使用 [uv](https://github.com/astral-sh/uv)：

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[bot]"
```

Windows 激活虚拟环境：

```powershell
.venv\Scripts\activate
```

## 快速开始：先选择使用场景

VikingBot 有三种主要使用方式。它们不是互相替代的模式，而是面向不同需求的入口。

| 场景 | 适合谁                             | 启动命令 | OpenViking                    |
|------|---------------------------------|----------|-------------------------------|
| **A. OpenViking + Bot 一体启动** | 本地完整体验资源、记忆和 Agent              | `openviking-server --with-bot` | bot将使用当前启动的 OpenViking Server |
| **B. 本地调试 Agent** | 想快速测试 Bot、开发 Tool/Skill         | `vikingbot chat` | 可选；未配置时，bot无法使用OpenViking功能   |
| **C. Gateway 统一入口** | 单独启动bot，并配置已有的OpenViking Server | `vikingbot gateway` | 可显式配置或不配置                     |

### 场景 A：OpenViking + Bot 一体启动

适合本地完整体验。OpenViking Server 和 VikingBot Gateway 一起启动，`ov chat` 先访问 OpenViking Server，再由 Server 的 `/bot/v1` 路由转发到 VikingBot。

```text
ov chat → OpenViking Server → VikingBot Gateway → Agent
```

#### 1. 准备配置

先按照 [OpenViking 快速开始](../docs/zh/getting-started/03-quickstart-server.md)配置好 OpenViking 所需的模型和存储。Bot 默认继承根级 `vlm` 作为 Agent 模型；如需使用独立模型，再配置 `bot.agents`。

一体启动时，Bot 固定使用当前启动的 OpenViking Server，忽略 `bot.ov_server` 中指向其他服务的配置。OpenViking Server 会为每个 Chat 请求向 Bot 注入已经认证的 request-scoped 身份。

#### 2. 一体启动

```bash
openviking-server --with-bot
```

该命令会启动当前 OpenViking Server，并启动一个受管的 VikingBot Gateway。此时 Bot 使用当前 Server，不会连接 `bot.ov_server.server_url` 指向的另一套服务。

#### 3. 配置并使用 `ov` CLI

运行交互式配置：

```bash
ov config
```

让当前 CLI 配置指向 OpenViking Server，例如 `http://127.0.0.1:1933`；如果 Server 开启了鉴权，再填写调用者的 User/Admin API Key。然后：

```bash
ov chat
ov chat -m "记住我更喜欢简洁的回答"
ov find "我的回答偏好"
```

这里的身份关系是：

- `ovcli.conf.api_key` 是当前调用者身份；
- OpenViking Server 校验该身份后，将 request-scoped 连接传给 Bot；
- 该请求身份优先于任何进程级默认身份，避免多个调用者共享同一个 Bot 用户。

### 场景 B：本地调试 Agent

适合快速试用 VikingBot，或者开发 Agent、Tool、Skill。`vikingbot chat` 在当前进程内启动 Agent，不需要先启动 Gateway，也不读取 `ovcli.conf` 作为 Bot 配置。

#### 1. 配置模型

编辑 `~/.openviking/ov.conf`：

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

也可以只配置根级 `vlm`，VikingBot 会继承其中的模型、Provider、API Key、API Base 和超时配置。

#### 2. 开始对话

```bash
# 单次调用
vikingbot chat -m "帮我总结当前目录的项目结构"

# 交互式多轮对话
vikingbot chat

# 指定会话
vikingbot chat --session my-session
```

没有可用 OpenViking Server 时，VikingBot 会以 standalone 方式运行：文件、Shell、Web、Skill 等能力仍可使用，但不会提供 OpenViking 记忆和文件工具。

如果希望调试时连接 OpenViking，可在同一个 `ov.conf` 中配置 `server`，或显式配置 `bot.ov_server.server_url`，参见[连接 OpenViking](#连接-openviking)。

### 场景 C：Gateway 统一入口

适合长期运行、远程访问和多渠道接入。`ovcli.conf.url` 可以直接指向 VikingBot Gateway：

```text
ov chat                  → Gateway /bot/v1/chat
ov ls/find/session/...   → Gateway /api/v1/* → OpenViking Server
```

Gateway 与 OpenViking 有三种连接状态：

| 状态 | 条件 | 行为 |
|------|------|------|
| **Explicit** | 配置 `bot.ov_server.server_url` | 连接指定 OpenViking；不可达时启动失败 |
| **Inherited** | 未显式配置 URL，但同一 `ov.conf` 有 `server` | 连接该 OpenViking；不可达时降级为 standalone |
| **Standalone** | 没有可用 OpenViking | Chat 可用；OpenViking 工具禁用，`/api/v1/*` 返回 503 |

#### 1. 配置 Gateway 和 OpenViking

下面是显式连接远端 OpenViking 的示例：

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

如果远端 OpenViking 使用 `trusted` 模式，应设置 `api_key_type: "root"`，并在 `api_key` 中填写 Root Key。

#### 2. 启动 Gateway

```bash
vikingbot gateway
```

启动日志会显示 `openviking_explicit`、`openviking_inherited` 或 `standalone_local` 等实际状态。

#### 3. 让 `ov` CLI 指向 Gateway

可以使用 `ov config`，也可以编辑 `~/.openviking/ovcli.conf`：

```json
{
  "url": "http://127.0.0.1:18790",
  "api_key": "<caller-openviking-user-or-admin-api-key>",
  "actor_peer_id": "cli"
}
```

随后 Chat 和其他 OpenViking 命令都使用同一个入口：

```bash
ov chat -m "检索项目资料并给我一个结论"
ov ls viking://resources/
ov find "项目发布流程"
```

#### 4. 对外监听时配置 Gateway Token

Gateway 默认只监听 `127.0.0.1`。改成 `0.0.0.0` 或其他非 localhost 地址时，必须配置 Token，否则拒绝启动：

```json
{
  "bot": {
    "gateway": {
      "host": "0.0.0.0",
      "port": 18790,
      "token": "<strong-random-token>"
    }
  }
}
```

客户端在 `ovcli.conf` 中增加：

```json
{
  "url": "https://bot.example.com",
  "api_key": "<caller-openviking-user-or-admin-api-key>",
  "gateway_token": "<strong-random-token>",
  "actor_peer_id": "cli"
}
```

Gateway Token 只保护 Gateway 入口；OpenViking API Key 表示调用者身份，两者不能互相替代。Gateway Token 不会转发给 OpenViking。

## 接入聊天平台

需要飞书、Slack、Telegram、Discord、WhatsApp、钉钉、QQ、Email 或 MoChat 时，在场景 C 的基础上配置 `bot.channels`，然后启动 Gateway。

以飞书为例：

```json
{
  "bot": {
    "channels": [
      {
        "type": "feishu",
        "enabled": true,
        "app_id": "<feishu-app-id>",
        "app_secret": "<feishu-app-secret>",
        "allow_from": [],
        "ov_tools_enable": true
      }
    ]
  }
}
```

```bash
vikingbot gateway
vikingbot channels status
```

同一种渠道可以配置多个实例。VikingBot 使用 `type + channel_id + chat_id` 隔离会话和路由回复。各平台的凭证、事件订阅和权限配置见 [渠道配置](docs/zh/concepts/05-channel.md)。

## 连接 OpenViking

VikingBot 与 OpenViking 共用 `~/.openviking/ov.conf`。连接优先级和行为如下：

1. `openviking-server --with-bot` 启动的受管 Bot 使用当前 Server；
2. 普通 `vikingbot gateway/chat` 优先使用显式 `bot.ov_server.server_url`；
3. 没有显式 URL 时，从同一份 `ov.conf.server` 推导地址；
4. 没有可用地址时以 standalone 运行。

鉴权要求：

| OpenViking `auth_mode` | Bot 凭证 | Gateway 请求 |
|------------------------|----------|----------------|
| `dev` | 本地使用 | Gateway 必须监听 localhost |
| `api_key` | `bot.ov_server.api_key` 必须是 User/Admin Key | Chat 调用者也必须提供有效 User/Admin Key；Root Key 不可用于数据接口 |
| `trusted` | 显式连接使用 Root Key；继承连接可读取 `server.root_api_key` | 非本地入口还必须先通过 Gateway Token |

Gateway 会在启动时校验 upstream 和 Bot 凭证，并在每个请求中检查 OpenViking 当前鉴权模式。运行时模式发生变化时会 fail closed，要求修正配置或重启 Gateway。

VikingBot 使用 OpenViking 完成：

- 读取当前 Peer Profile；
- 按类型召回 events、entities 和 preferences；
- 检索 Agent Experience；
- 浏览、搜索和读取 Resource；
- 增量同步并提交 Session，提取长期记忆与经验。

详细调用链见 [VikingBot 与 OpenViking 集成](docs/zh/concepts/04-openviking-integration.md)。Gateway 入口与鉴权边界来自 [RFC #3042](https://github.com/volcengine/OpenViking/discussions/3042)。

## 配置说明

配置文件默认为 `~/.openviking/ov.conf`，可通过环境变量指定：

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
```

修改配置后需要重启 `vikingbot gateway`。

### 常用配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `bot.agents.temperature` | `0.7` | 模型采样温度 |
| `bot.agents.thinking` | `true` | Provider 支持时启用 reasoning/thinking |
| `bot.agents.timeout` | 继承 `vlm.timeout` | 单次模型请求超时 |
| `bot.agents.max_tool_iterations` | `50` | 单轮最大工具迭代数 |
| `bot.agents.memory_window` | `50` | 本地历史窗口和会话提交消息阈值 |
| `bot.agents.subagent_enabled` | `true` | 是否提供 `spawn` 工具 |
| `bot.gateway.host` | `127.0.0.1` | Gateway 监听地址 |
| `bot.gateway.port` | `18790` | Gateway 监听端口 |
| `bot.sandbox.backend` | `direct` | 执行后端 |
| `bot.sandbox.mode` | `shared` | 工作区隔离方式 |
| `bot.heartbeat.enabled` | `true` | 是否周期检查 `HEARTBEAT.md` |
| `bot.heartbeat.interval_seconds` | `600` | 心跳间隔 |
| `bot.mode` | `normal` | 可选 `normal`、`readonly`、`debug` |

### OpenViking 召回配置

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `bot.ov_server.memory_recall_events_limit` | `10` | 每轮 events 记忆条数 |
| `bot.ov_server.memory_recall_entities_limit` | `10` | 每轮 entities 记忆条数 |
| `bot.ov_server.memory_recall_preferences_limit` | `3` | 每轮 preferences 记忆条数 |
| `bot.ov_server.memory_recall_max_chars` | `4000` | Peer 记忆注入字符预算 |
| `bot.ov_server.exp_recall_limit` | `5` | Experience 召回条数 |
| `bot.ov_server.exp_recall_max_chars` | `10000` | Experience 注入字符预算 |
| `bot.ov_server.exp_write_tools` | `write_file`,`edit_file` | 写操作前触发经验召回的工具 |

## Workspace 与 Agent 定制

Workspace 是 VikingBot 的本地工作目录。它保存 Agent 启动指令、Skill、Heartbeat 任务以及文件和 Shell 工具操作的内容；OpenViking Workspace 则通过 `openviking_*` 工具访问 Resource、Memory 和 Skill，两者不是同一个目录。

### 找到当前 Workspace

Workspace 根目录由 `storage.workspace` 决定：

```text
<storage.workspace>/bot/workspace
```

未配置 `storage.workspace` 时，默认为 `~/.openviking/data/bot/workspace`。可以运行以下命令确认：

```bash
vikingbot status
```

Agent 实际使用的活动目录还取决于 `bot.sandbox.mode`：

| 模式 | 活动 Workspace |
|------|----------------|
| `shared`（默认） | `<workspace>/shared` |
| `per-session` | `<workspace>/<session-key>` |
| `per-channel` | `<workspace>/<channel-key>` |

例如，默认配置下应修改 `~/.openviking/data/bot/workspace/shared/SOUL.md`。

### 定制 Agent

首次使用某个活动 Workspace 时，VikingBot 会从内置 `bot/workspace` 模板复制初始文件。常用定制入口如下：

| 文件或目录 | 作用 | 加载方式 |
|------------|------|----------|
| `SOUL.md` | 人格、价值观和表达风格 | 每轮自动加入系统提示 |
| `AGENTS.md` | 全局工作规则和任务约束；可按需创建 | 每轮自动加入系统提示 |
| `IDENTITY.md` | Agent 名称、角色和身份背景；可按需创建 | 每轮自动加入系统提示 |
| `TOOLS.md` | 工具选择、调用边界和安全规则 | 每轮自动加入系统提示 |
| `skills/<name>/SKILL.md` | 某类任务的操作流程和配套资源 | 先注入摘要，需要时渐进加载全文 |
| `HEARTBEAT.md` | 周期检查的任务清单 | 仅由 Heartbeat 读取 |

例如，可以修改活动 Workspace 中的 `SOUL.md`：

```markdown
# Soul

你是团队的研发助手。

- 默认使用中文回答
- 先给结论，再补充必要细节
- 修改代码前先确认现状，修改后运行相关验证
- 不确定时明确说明假设，不编造结果
```

保存后通常会在下一轮 Agent 对话中生效，无需重启 Gateway。`SOUL.md` 只能改变提示行为，不能绕过 Channel 权限、工具可见性或 Sandbox 限制。

> [!NOTE]
> 请修改活动 Workspace 中的文件。仓库或安装包中的 `bot/workspace` 是初始化模板，不会覆盖已经存在的 Workspace。不要在启动文件中保存 API Key 等秘密。

完整加载顺序、文件职责和定制边界见 [Agent 能力体系](docs/zh/concepts/02-agent-capabilities.md#workspace-与-agent-定制)。

## Agent 工具

### 内置工具

| 类别 | 工具 |
|------|------|
| 文件与命令 | `read_file`、`write_file`、`edit_file`、`list_dir`、`exec` |
| Web | `web_search`、`web_fetch` |
| OpenViking | `openviking_list`、`openviking_search`、`openviking_grep`、`openviking_glob`、`openviking_multi_read`、`openviking_add_resource`、`openviking_memory_commit` |
| 其他 | `message`、`generate_image`、`cron`、`spawn` |

`readonly` 模式不会注册 `openviking_add_resource`。渠道设置 `ov_tools_enable: false` 时，该渠道不显示 OpenViking 工具，也不注入 Profile、Memory 和 Experience。

### MCP 工具

第三方 MCP Server 配置在 `bot.tools.mcp_servers`：

```json
{
  "bot": {
    "tools": {
      "mcp_servers": {
        "filesystem": {
          "type": "stdio",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
          "tool_timeout": 30,
          "enabled_tools": ["*"]
        },
        "remote": {
          "type": "streamableHttp",
          "url": "https://example.com/mcp",
          "headers": {"Authorization": "Bearer $MCP_TOKEN"},
          "enabled_tools": ["search"]
        }
      }
    }
  }
}
```

支持 `stdio`、`sse` 和 `streamableHttp`。工具注册名为 `mcp_<server>_<tool>`；单个 MCP 连接失败不会阻断其他 Agent 能力。

## 沙箱

| 后端 | 说明 |
|------|------|
| `direct` | 默认，直接在 Bot 宿主机执行，不是强隔离环境 |
| `srt` | 支持网络和文件允许/拒绝策略 |
| `opensandbox` | 连接 OpenSandbox Server |
| `aiosandbox` | 连接 AIO Sandbox 服务 |

工作区模式支持：

- `shared`：所有会话共享工作区；
- `per-session`：每个 Session 独立；
- `per-channel`：同一渠道实例共享。

DirectBackend 默认 `restrict_to_workspace: false`。对不可信用户开放 Gateway 时，应选择隔离后端，并设置渠道白名单和网络/文件策略。

```json
{
  "bot": {
    "sandbox": {
      "backend": "srt",
      "mode": "per-session"
    }
  }
}
```

## HTTP API

Gateway 的 Bot API 前缀为 `/bot/v1`：

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/bot/v1/chat` | 同步对话 |
| POST | `/bot/v1/chat/stream` | SSE 流式对话 |
| POST | `/bot/v1/feedback` | 提交回复反馈 |
| GET/POST | `/bot/v1/sessions` | 查询或创建 API Session |
| GET/DELETE | `/bot/v1/sessions/{id}` | 查询或删除 Session |

配置 OpenViking upstream 后，`/api/v1/*` 会代理到 OpenViking Server。

## 运维命令

| 命令 | 用途 |
|------|------|
| `vikingbot status` | 查看模型、配置和运行状态 |
| `vikingbot channels status` | 查看渠道状态 |
| `vikingbot channels login` | 登录 WhatsApp bridge |
| `vikingbot cron list` | 查看定时任务 |
| `vikingbot cron add` | 添加定时任务 |
| `vikingbot cron run` | 手动执行任务 |
| `vikingbot feedback-stats` | 汇总回复反馈与结果指标 |

启用 Langfuse：

```json
{
  "bot": {
    "langfuse": {
      "enabled": true,
      "secret_key": "<langfuse-secret-key>",
      "public_key": "<langfuse-public-key>",
      "base_url": "http://localhost:3000"
    }
  }
}
```

仓库内提供了 `deploy/docker/deploy_langfuse.sh`，可用于本地部署。

## 安全提示

- 不要把模型 API Key、OpenViking API Key 或 Gateway Token 提交到仓库。
- 非 localhost Gateway 必须配置高强度随机 Token，并在网络层启用 HTTPS。
- `X-Gateway-Token` 只保护 Gateway，不能代替 OpenViking 用户身份。
- `allow_from: []` 表示不限制发送者；对外服务建议配置明确白名单。
- `direct` 后端会以 Bot 进程用户权限执行文件和 Shell 操作，不适合不可信调用者。
- `openviking_connection` 只能来自可信 Server 代理或本地可信链路，不应接受公网请求体自行声明。

## 更多文档

- [VikingBot 架构](docs/zh/concepts/01-architecture.md)
- [Agent 能力体系](docs/zh/concepts/02-agent-capabilities.md)
- [渠道、Gateway 与运行管理](docs/zh/concepts/03-channels-and-gateway.md)
- [VikingBot 与 OpenViking 集成](docs/zh/concepts/04-openviking-integration.md)
- [渠道配置](docs/zh/concepts/05-channel.md)
