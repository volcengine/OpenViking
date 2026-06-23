
# Vikingbot

**Vikingbot** 基于 [Nanobot](https://github.com/HKUDS/nanobot) 项目构建，旨在提供一个与 OpenViking 集成的类 OpenClaw 机器人。

## ✨ OpenViking 核心特性

Vikingbot 深度集成 OpenViking，提供强大的知识管理和记忆检索能力：

- **本地/远程双模式**：支持本地存储（`~/.openviking/data/`）和远程服务器模式
- **7 个专用 Agent 工具**：资源管理、语义搜索、正则搜索、通配符搜索、记忆提交
- **三级内容访问**：L0（摘要）、L1（概览）、L2（完整内容）
- **会话记忆自动提交**：对话历史自动保存到 OpenViking
- **模型配置**：从 OpenViking 配置（`vlm` 部分）读取，无需在 bot 配置中单独设置 provider

## 📦 安装

**选项 1：从 PyPI 安装（最简单）**
```bash
pip install "openviking[bot]"
```

**选项 2：从源码安装（用于开发）**

**前置要求**

首先安装 [uv](https://github.com/astral-sh/uv)（一个极速的 Python 包安装器）：

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**从源码安装**（最新功能，推荐用于开发）

```bash
git clone https://github.com/volcengine/OpenViking
cd OpenViking

# 创建 Python 3.11 或更高版本 虚拟环境
uv venv --python 3.11

# 激活环境
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# 安装依赖（最小化）
uv pip install -e ".[bot]"

# 或安装包含可选功能
uv pip install -e ".[bot,bot-langfuse,bot-telegram]"
```

### 可选依赖

只安装你需要的功能：

| 功能组 | 安装命令 | 描述 |
|---------------|-----------------|-------------|
| **完整版** | `uv pip install -e ".[bot-full]"` | 包含所有功能 |
| **Langfuse** | `uv pip install -e ".[bot-langfuse]"` | LLM 可观测性和追踪 |
| **FUSE** | `uv pip install -e ".[bot-fuse]"` | OpenViking 文件系统挂载 |
| **沙箱** | `uv pip install -e ".[bot-sandbox]"` | 代码执行沙箱 |
| **OpenCode** | `uv pip install -e ".[bot-opencode]"` | OpenCode AI 集成 |

#### 聊天渠道

| 渠道 | 安装命令 |
|---------|-----------------|
| **Telegram** | `uv pip install -e ".[bot-telegram]"` |
| **飞书/Lark** | `uv pip install -e ".[bot-feishu]"` |
| **钉钉** | `uv pip install -e ".[bot-dingtalk]"` |
| **Slack** | `uv pip install -e ".[bot-slack]"` |
| **QQ** | `uv pip install -e ".[bot-qq]"` |

可以组合多个功能：
```bash
uv pip install -e ".[bot,bot-langfuse,bot-telegram]"
```

## 🚀 快速开始

> [!TIP]
> 通过配置文件 `~/.openviking/ov.conf` 配置 vikingbot！
> 获取 API 密钥：[OpenRouter](https://openrouter.ai/keys)（全球）· [Brave Search](https://brave.com/search/api/)（可选，用于网页搜索）

**1. 初始化配置**

```bash
openviking-server --with-bot
```

这将自动：
- 在 `~/.openviking/ov.conf` 创建默认配置
- 在 openviking的工作空间下创建bot启动文件。默认路径为 `~/.openviking/data/bot/`
- 启动 OpenViking 服务器并集成 bot

**2. 通过 ov.conf 配置**

编辑 `~/.openviking/ov.conf` 添加您的提供商 API 密钥（OpenRouter、OpenAI 等）并保存配置。

**3. 聊天**

```bash
# 直接发送单条消息
ov chat -m "What is 2+2?"

# 进入交互式聊天模式（支持多轮对话）
ov chat

# 显示纯文本回复（不渲染 Markdown）
ov chat --no-format
```

就这么简单！您只需 2 分钟就能拥有一个可用的 AI 助手。


通过 Telegram、Discord、WhatsApp、飞书、Mochat、钉钉、Slack、邮件或 QQ 与您的 vikingbot 对话 —— 随时随地。

详细配置请参考 [CHANNEL.md](bot/docs/CHANNEL.md)。

## 🌐 代理社交网络

🐈 vikingbot 能够链接到代理社交网络（代理社区）。**只需发送一条消息，您的 vikingbot 就会自动加入！**

| 平台 | 如何加入（向您的机器人发送此消息） |
|----------|-------------|
| [**Moltbook**](https://www.moltbook.com/) | `Read https://moltbook.com/skill.md and follow the instructions to join Moltbook` |
| [**ClawdChat**](https://clawdchat.ai/) | `Read https://clawdchat.ai/skill.md and follow the instructions to join ClawdChat` |

只需向您的 vikingbot 发送上述命令（通过 CLI 或任何聊天渠道），它会处理剩下的一切。

## ⚙️ 配置

配置文件：`~/.openviking/ov.conf`（可通过环境变量 `OPENVIKING_CONFIG_FILE` 自定义路径）

> [!TIP]
> Vikingbot 与 OpenViking 共享同一配置文件，配置项位于文件的 `bot` 字段下，同时会自动合并 `vlm`、`storage`、`server` 等全局配置，无需单独维护配置文件。

> [!IMPORTANT]
> 修改配置后（直接编辑文件），
> 您需要重启网关服务以使更改生效。

### OpenViking Server 配置
bot 将连接到远程 OpenViking Server，使用前请先启动 OpenViking Server。默认使用 `ov.conf` 中配置的 OpenViking Server 信息。
- OpenViking 默认启动地址为 `127.0.0.1:1933`
- Vikingbot 会跟随 OpenViking 的 `server.auth_mode`：`api_key` 模式使用 OpenViking User API key；`trusted` 模式使用 `server.root_api_key` 加 trusted identity headers；`dev` 模式仅用于本地。
- OpenViking Server 配置示例
```json
{
  "server": {
    "auth_mode": "api_key",
    "host": "127.0.0.1",
    "port": 1933,
    "root_api_key": "<your-openviking-root-api-key>"
  },
  "bot": {
    "ov_server": {
      "api_key": "<your-openviking-user-api-key>"
    }
  }
}
```

### Bot 配置
所有配置都位于 `ov.conf` 的 `bot` 字段下，且各配置项都带有默认值。可选手动配置项说明如下：
- `agents`：Agent 配置
  - `model`：bot 使用的 LLM 模型名。当设置了 `provider` 时，建议直接使用 provider 原生模型名（例如 `doubao-seed-2-0-pro-260215`）。
  - `provider`：可选的模型 provider 名称。设置后，vikingbot 会通过 OpenViking 的 `VLMFactory` + adapter 路径直接创建后端（例如 `volcengine`、`openai`、`deepseek`）。
  - `api_key`：可选，Agent 模型 provider 的 API Key。若希望 bot 使用独立凭证，可直接在这里配置。
  - `api_base`：可选，Agent 模型 provider 的 API Base。适用于 provider 网关或自定义端点，例如 VolcEngine Ark。
  - `extra_headers`：可选，透传给模型 provider 的额外 HTTP 请求头。
  - `max_tool_iterations`：单轮对话任务最大循环次数，超过则直接返回结果。
  - `memory_window`：自动提交 session 到 OpenViking 的对话轮次上限。
  - `gen_image_model`：生成图片使用的模型。
- `gateway`：Gateway 配置
  - host：Gateway 监听地址，默认值为 `0.0.0.0`
  - port：Gateway 监听端口，默认值为 `18790`
  - token：Gateway 鉴权 token。当 `host` 为非 localhost 地址（例如默认的 `0.0.0.0`）时必须设置——否则网关会拒绝启动（`SECURITY: bot.gateway.token is required when gateway.host is non-localhost`）。请设置为随机密钥；客户端通过 `X-Gateway-Token` 请求头携带。
- `sandbox`：沙箱配置
  - `mode`：沙箱模式，可选值为 `shared`（所有 session 共享工作空间）或 `private`（私有，按 Channel、session 隔离工作空间）。默认值为 `shared`。
- `ov_server`：OpenViking Server 配置
  - 若不配置，默认使用 `ov.conf` 中配置的 OpenViking Server 信息。
  - 如果你使用远端 OpenViking Server，可以在这里配置目标服务地址和 API Key。
    - `server_url`：OpenViking Server 基础地址，例如 `https://api.vikingdb.cn-beijing.volces.com/openviking` 或 `http://localhost:1933`。
    - `api_key`：bot 调用 OpenViking Server 使用的 API Key。`api_key` 模式下必须是 OpenViking User key；trusted 模式且 `api_key_type: "root"` 时，该字段就是 OpenViking root key。
    - `root_api_key`：废弃兼容字段。新配置不要使用；trusted 模式请使用 `api_key` 配合 `api_key_type: "root"`。
    - `account_id`：默认值为 `default`，即 OpenViking 的账号 ID。同一 OpenViking account 下的所有 user 共享 resources。
    - `api_key_type`：默认由同一个 `ov.conf` 中的 OpenViking `server.auth_mode` 推导：`api_key`/`dev` 为 `user`，`trusted` 为 `root`。通常无需手动配置。
      如果 `bot.ov_server` 指向另一套 OpenViking Server，且对方使用 trusted 鉴权，请设置 `api_key_type: "root"`，并在 `api_key` 中提供对方的 root key。
    - `exp_write_tools`：可选，触发经验记忆注入的工具名列表（自演化 agent memory 循环，详见 #2007）。默认 `["write_file", "edit_file"]`。该配置只控制 bot 侧注入触发时机；已存储 experience 的生成由 OpenViking 记忆抽取和当前 session 的 `memory_policy.memory_types` 白名单控制。
    - `recall_exp_first_round_only`：可选。为 `true` 时，`ContextBuilder._build_user_memory` 跳过每轮 user/agent 经验召回，仅在首个 user turn 注入一次经验。默认 `false`。
    - 每轮 user/peer 记忆召回默认使用 type-quota 检索。`profile.md` 仍由 profile 链路单独注入，不占自动检索候选位。
    - `memory_recall_events_limit`：可选。每轮召回的 `events/` 记忆条数。默认 `10`。
    - `memory_recall_entities_limit`：可选。每轮召回的 `entities/` 记忆条数。默认 `10`。
    - `memory_recall_preferences_limit`：可选。每轮召回的 `preferences/` 记忆条数。默认 `3`。
    - `memory_recall_max_chars`：可选。注入到上下文的 user/peer 记忆全文字符预算。默认 `4000`。
    - `exp_recall_limit`：可选。召回时每个任务检索的经验条数。默认 `5`。
    - `exp_recall_max_chars`：可选。注入到上下文的格式化经验块的字符预算。默认 `2000`。
- `channels`：消息平台配置，详见 [消息平台配置](bot/docs/CHANNEL.md)

```json
{
  "bot": {
    "agents": {
      "model": "doubao-seed-2-0-pro-260215",
      "api_key": "<your-ark-api-key>",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "provider": "volcengine",
      "max_tool_iterations": 50,
      "memory_window": 50
    },
    "gateway": {
      "host": "0.0.0.0",
      "port": 18790,
      "token": "<set-a-random-gateway-token>"
    },
    "sandbox": {
      "mode": "shared"
    },
    "ov_server": {
      "server_url": "https://api.vikingdb.cn-beijing.volces.com/openviking",
      "api_key": "<your-openviking-user-api-key>",
      "account_id": "default"
    },
    "channels": [
      {
        "type": "feishu",
        "enabled": true,
        "ov_tools_enable": true,
        "appId": "<your-feishu-app-id>",
        "appSecret": "<your-feishu-app-secret>",
        "allowFrom": []
      }
    ]
  }
}
```

如果你只是想通过 `vikingbot gateway` 或 `vikingbot chat` 体验 bot，可以将 `channels` 设置为空数组（`[]`）。

使用上述配置时，你可以直接体验 bot，或者同时配置飞书：

```bash
# 启动 HTTP Gateway
vikingbot gateway

# 或直接在 CLI 中与 bot 对话
vikingbot chat
vikingbot chat -m "Hello"
```

### OpenViking Agent 工具

Vikingbot 提供 7 个专用的 OpenViking 工具：

| 工具名称 | 描述 |
|----------|------|
| `openviking_read` | 读取 OpenViking 资源（支持 abstract/overview/read 三级） |
| `openviking_list` | 列出 OpenViking 资源 |
| `openviking_search` | 语义搜索 OpenViking 资源 |
| `openviking_add_resource` | 添加本地文件为 OpenViking 资源 |
| `openviking_grep` | 使用正则表达式搜索 OpenViking 资源 |
| `openviking_glob` | 使用 glob 模式匹配 OpenViking 资源 |
| `openviking_memory_commit` | 提交session到Openviking|

### 外部 MCP 服务器

Vikingbot 还可以接入第三方 [MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 服务器的工具（文件系统、GitHub、浏览器、数据库等）。在 `ov.conf` 的 `tools.mcp_servers` 下配置即可，每个服务器的工具会在代理启动时注册，名称前缀为 `mcp_<服务器>_<工具>`。

```json
{
  "bot": {
    "tools": {
      "mcp_servers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
          "env": {},
          "tool_timeout": 30,
          "enabled_tools": ["*"]
        },
        "github": {
          "type": "streamableHttp",
          "url": "https://api.githubcopilot.com/mcp/",
          "headers": {"Authorization": "Bearer $GITHUB_TOKEN"},
          "enabled_tools": ["search_repositories", "create_issue"]
        }
      }
    }
  }
}
```

| 字段 | 描述 |
|------|------|
| `type` | 传输模式：`stdio` / `sse` / `streamableHttp`。省略时自动探测（有 `command` 走 stdio，否则按 `url` 走 HTTP）。 |
| `command` | （stdio）启动服务器进程的命令（如 `npx`、`uvx`）。 |
| `args` | （stdio）命令参数。 |
| `env` | （stdio）传给服务器进程的额外环境变量。 |
| `url` | （sse / streamableHttp）接入地址。 |
| `headers` | （sse / streamableHttp）自定义请求头（如 `Authorization`）。 |
| `tool_timeout` | 单次工具调用超时（秒），默认 `30`。 |
| `enabled_tools` | 工具白名单，可使用原始 MCP 名称或包装后的 `mcp_<服务器>_<工具>` 名；`["*"]` 表示全部暴露。 |

> MCP 服务器在代理循环启动时连接，并在关闭时自动清理；若某服务器既没有 `command` 也没有 `url`，则跳过并打印警告。连接失败会记录错误日志，代理会在没有该服务器工具的情况下继续运行。

### OpenViking 钩子

Vikingbot 默认启用 OpenViking 钩子：

```json
{
  "hooks": ["vikingbot.hooks.builtins.openviking_hooks.hooks"]
}
```

| 钩子 | 功能 |
|------|------|
| `OpenVikingCompactHook` | 会话消息自动提交到 OpenViking |
| `OpenVikingPostCallHook` | 工具调用后钩子（测试用途） |

### 手动配置（高级）

直接编辑配置文件：

```json
{
  "bot": {
    "agents": {
      "model": "openai/doubao-seed-2-0-pro-260215"
    }
  }
}
```

Provider 配置从 OpenViking 配置（`ov.conf` 的 `vlm` 部分）读取。

### 提供商

> [!TIP]
> - **Groq** 通过 Whisper 提供免费的语音转录。如果已配置，Telegram 语音消息将自动转录。
> - **智谱编码计划**：如果您使用智谱的编码计划，请在您的 zhipu 提供商配置中设置 `"apiBase": "https://open.bigmodel.cn/api/coding/paas/v4"`。
> - **MiniMax（中国大陆）**：如果您的 API 密钥来自 MiniMax 的中国大陆平台（minimaxi.com），请在您的 minimax 提供商配置中设置 `"apiBase": "https://api.minimaxi.com/v1"`。
> - **MiniMax 推荐模型**：`MiniMax-M2.7`（性能更强）和 `MiniMax-M2.7-highspeed`（速度更快、更灵活）。可在 agent 配置中通过 `"model": "MiniMax-M2.7"` 使用。

| 提供商 | 用途 | 获取 API 密钥 |
|----------|---------|-------------|
| `openrouter` | LLM（推荐，可访问所有模型） | [openrouter.ai](https://openrouter.ai) |
| `anthropic` | LLM（Claude 直连） | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | LLM（GPT 直连） | [platform.openai.com](https://platform.openai.com) |
| `deepseek` | LLM（DeepSeek 直连） | [platform.deepseek.com](https://platform.deepseek.com) |
| `groq` | LLM + **语音转录**（Whisper） | [console.groq.com](https://console.groq.com) |
| `gemini` | LLM（Gemini 直连） | [aistudio.google.com](https://aistudio.google.com) |
| `minimax` | LLM（MiniMax 直连） | [platform.minimax.io](https://platform.minimax.io) |
| `aihubmix` | LLM（API 网关，可访问所有模型） | [aihubmix.com](https://aihubmix.com) |
| `dashscope` | LLM（通义千问） | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| `moonshot` | LLM（月之暗面/Kimi） | [platform.moonshot.cn](https://platform.moonshot.cn) |
| `zhipu` | LLM（智谱 GLM） | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `vllm` | LLM（本地，任何 OpenAI 兼容服务器） | — |

<details>
<summary><b>添加新提供商（开发者指南）</b></summary>

vikingbot 使用 **提供商注册表**（`vikingbot/providers/registry.py`）作为事实的单一来源。
添加新提供商只需 **2 步** —— 无需触及 if-elif 链。

**步骤 1.** 在 `vikingbot/providers/registry.py` 的 `PROVIDERS` 中添加一个 `ProviderSpec` 条目：

```python
ProviderSpec(
    name="myprovider",                   # 配置字段名称
    keywords=("myprovider", "mymodel"),  # 用于自动匹配的模型名称关键词
    env_key="MYPROVIDER_API_KEY",        # LiteLLM 的环境变量
    display_name="My Provider",          # 在 `vikingbot status` 中显示
    litellm_prefix="myprovider",         # 自动前缀：模型 → myprovider/model
    skip_prefixes=("myprovider/",),      # 不要双重前缀
)
```

**步骤 2.** 在 `vikingbot/config/schema.py` 的 `ProvidersConfig` 中添加一个字段：

```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = ProviderConfig()
```

就这么简单！环境变量、模型前缀、配置匹配和 `vikingbot status` 显示都将自动工作。

**常见的 `ProviderSpec` 选项：**

| 字段 | 描述 | 示例 |
|-------|-------------|---------|
| `litellm_prefix` | 为 LiteLLM 自动前缀模型名称 | `"dashscope"` → `dashscope/qwen-max` |
| `skip_prefixes` | 如果模型已经以这些开头，则不要前缀 | `("dashscope/", "openrouter/")` |
| `env_extras` | 要设置的额外环境变量 | `(("ZHIPUAI_API_KEY", "{api_key}"),)` |
| `model_overrides` | 每模型参数覆盖 | `(("kimi-k2.5", {"temperature": 1.0}),)` |
| `is_gateway` | 可以路由任何模型（如 OpenRouter） | `True` |
| `detect_by_key_prefix` | 通过 API 密钥前缀检测网关 | `"sk-or-"` |
| `detect_by_base_keyword` | 通过 API 基础 URL 检测网关 | `"openrouter"` |
| `strip_model_prefix` | 在重新前缀之前去除现有前缀 | `True`（对于 AiHubMix） |

</details>


### 可观测性（可选）

**Langfuse** 集成，用于 LLM 可观测性和追踪。

<details>
<summary><b>Langfuse 配置</b></summary>

**方式 1：本地部署（测试推荐）**

使用 Docker 在本地部署 Langfuse：

```bash
# 进入部署脚本目录
cd deploy/docker

# 运行部署脚本
./deploy_langfuse.sh
```

这将在 `http://localhost:3000` 启动 Langfuse，并使用预配置的凭据。

**方式 2：Langfuse Cloud**

1. 在 [langfuse.com](https://langfuse.com) 注册
2. 创建新项目
3. 从项目设置中复制 **Secret Key** 和 **Public Key**

**配置**

添加到 `~/.openviking/ov.conf`：

```json
{
  "bot": {
    "langfuse": {
      "enabled": true,
      "secret_key": "sk-lf-vikingbot-secret-key-2026",
      "public_key": "pk-lf-vikingbot-public-key-2026",
      "base_url": "http://localhost:3000"
    }
  }
}
```

对于 Langfuse Cloud，使用 `https://cloud.langfuse.com` 作为 `base_url`。

**安装 Langfuse 支持：**
```bash
uv pip install -e ".[bot-langfuse]"
```

**重启 vikingbot：**
```bash
vikingbot gateway
```

**启用的功能：**
- 每次对话自动创建 trace
- Session 和 User 追踪
- LLM 调用监控
- Token 使用量追踪
- 问答效果反馈观测方案：`bot/docs/vikingbot-feedback-observability-design.md`

</details>

### 安全

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `true` | 当为 `true` 时，将**所有**代理工具（shell、文件读/写/编辑、列表）限制到工作区目录。防止路径遍历和范围外访问。 |
| `channels.*.allowFrom` | `[]`（允许所有） | 用户 ID 白名单。空 = 允许所有人；非空 = 只有列出的用户可以交互。 |
| `channels.*.ov_tools_enable` | `true` | 当为 `false` 时，该 channel 禁用 OpenViking 工具（`openviking_*`），并跳过记忆/用户画像的上下文注入。适用于不需要 OV 记忆的轻量 channel。参见 [#1352](https://github.com/volcengine/OpenViking/pull/1352)。 |

### 沙箱

vikingbot 支持沙箱执行以增强安全性。

**默认情况下，`ov.conf` 中不需要配置 sandbox：**
- 默认后端：`direct`（直接在主机上运行代码）
- 默认模式：`shared`（所有会话共享一个沙箱）

只有当您想要更改这些默认值时，才需要添加 sandbox 配置。

<details>
<summary><b>沙箱配置选项</b></summary>

**使用不同的后端或模式：**
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

**可用后端：**
| 后端 | 描述 |
|---------|-------------|
| `direct` | （默认）直接在主机上运行代码 |
| `srt` | 使用 Anthropic 的 SRT 沙箱运行时 |

**可用模式：**
| 模式 | 描述 |
|------|-------------|
| `shared` | （默认）所有会话共享一个沙箱 |
| `per-session` | 每个会话使用独立的沙箱实例 |

**后端特定配置（仅在使用该后端时需要）：**

**Direct 后端：**
```json
{
  "bot": {
    "sandbox": {
      "backends": {
        "direct": {
          "restrictToWorkspace": false
        }
      }
    }
  }
}
```

**SRT 后端：**
```json
{
  "bot": {
    "sandbox": {
      "backend": "srt",
      "backends": {
        "srt": {
          "nodePath": "node",
          "network": {
            "allowedDomains": [],
            "deniedDomains": [],
            "allowLocalBinding": false
          },
          "filesystem": {
            "denyRead": [],
            "allowWrite": [],
            "denyWrite": []
          },
          "runtime": {
            "cleanupOnExit": true,
            "timeout": 300
          }
        }
      }
    }
  }
}
```

**SRT 后端设置：**

SRT 后端使用 `@anthropic-ai/sandbox-runtime`。

**系统依赖：**

SRT 后端还需要安装这些系统包：
- `ripgrep` (rg) - 用于文本搜索
- `bubblewrap` (bwrap) - 用于沙箱隔离
- `socat` - 用于网络代理

**在 macOS 上安装：**
```bash
brew install ripgrep bubblewrap socat
```

**在 Ubuntu/Debian 上安装：**
```bash
sudo apt-get install -y ripgrep bubblewrap socat
```

**在 Fedora/CentOS 上安装：**
```bash
sudo dnf install -y ripgrep bubblewrap socat
```

验证安装：

```bash
npm list -g @anthropic-ai/sandbox-runtime
```

如果未安装，请手动安装：

```bash
npm install -g @anthropic-ai/sandbox-runtime
```

**Node.js 路径配置：**

如果在 PATH 中找不到 `node` 命令，请在您的配置中指定完整路径：

```json
{
  "bot": {
    "sandbox": {
      "backends": {
        "srt": {
          "nodePath": "/usr/local/bin/node"
        }
      }
    }
  }
}
```

查找您的 Node.js 路径：

```bash
which node
# 或
which nodejs
```

</details>


## CLI 参考

| 命令 | 描述 |
|---------|-------------|
| `ov chat -m "..."` | 发送单条消息 |
| `ov chat` | 交互式聊天模式 |
| `ov chat --no-format` | 显示纯文本回复（无 Markdown） |

交互模式退出：`exit`、`quit`、`/exit`、`/quit`、`:q` 或 `Ctrl+D`。
