
# Vikingbot

**Vikingbot** 基于 [Nanobot](https://github.com/HKUDS/nanobot) 项目构建，旨在提供一个与 OpenViking 集成的类 OpenClaw 机器人。

## ✨ OpenViking 核心特性

Vikingbot 深度集成 OpenViking，提供强大的知识管理和记忆检索能力：

- **本地/远程双模式**：支持本地存储（`~/.openviking/data/`）和远程服务器模式
- **7 个专用 Agent 工具**：资源管理、语义搜索、正则搜索、通配符搜索、记忆搜索
- **三级内容访问**：L0（摘要）、L1（概览）、L2（完整内容）
- **会话记忆自动提交**：对话历史自动保存到 OpenViking
- **模型配置**：从 OpenViking 配置（`vlm` 部分）读取，无需在 bot 配置中单独设置 provider

## 📦 安装

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
cd OpenViking/bot

# 创建 Python 3.11 或更高版本 虚拟环境
uv venv --python 3.11

# 激活环境
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# 安装依赖（最小化）
uv pip install -e .

# 或安装包含可选功能
uv pip install -e ".[langfuse,telegram,console]"
```

### 可选依赖

只安装你需要的功能：

| 功能组 | 安装命令 | 描述 |
|---------------|-----------------|-------------|
| **完整版** | `uv pip install -e ".[full]"` | 包含所有功能 |
| **Langfuse** | `uv pip install -e ".[langfuse]"` | LLM 可观测性和追踪 |
| **FUSE** | `uv pip install -e ".[fuse]"` | OpenViking 文件系统挂载 |
| **沙箱** | `uv pip install -e ".[sandbox]"` | 代码执行沙箱 |
| **OpenCode** | `uv pip install -e ".[opencode]"` | OpenCode AI 集成 |

#### 聊天渠道

| 渠道 | 安装命令 |
|---------|-----------------|
| **Telegram** | `uv pip install -e ".[telegram]"` |
| **飞书/Lark** | `uv pip install -e ".[feishu]"` |
| **钉钉** | `uv pip install -e ".[dingtalk]"` |
| **Slack** | `uv pip install -e ".[slack]"` |
| **QQ** | `uv pip install -e ".[qq]"` |

可以组合多个功能：
```bash
uv pip install -e ".[langfuse,telegram,console]"
```

## 🚀 快速开始

> [!TIP]
> 配置 vikingbot 最简单的方式是通过控制台 Web UI！
> 获取 API 密钥：[OpenRouter](https://openrouter.ai/keys)（全球）· [Brave Search](https://brave.com/search/api/)（可选，用于网页搜索）

**1. 启动网关**

```bash
vikingbot gateway
```

这将自动：
- 在 `~/.openviking/ov.conf` 创建默认配置
- 在 openviking的工作空间下创建bot启动文件。默认路径为 `~/.openviking/data/bot/`
- 在 http://localhost:18791 启动控制台 Web UI

**2. 通过控制台配置**

在浏览器中打开 http://localhost:18791 并：
- 进入 **Config** 标签页
- 添加您的提供商 API 密钥（OpenRouter、OpenAI 等）
- 保存配置

**3. 聊天**

```bash
# 直接发送单条消息
vikingbot chat -m "What is 2+2?"

# 进入交互式聊天模式（支持多轮对话）
vikingbot chat

# 显示纯文本回复（不渲染 Markdown）
vikingbot chat --no-markdown

# 聊天时显示运行时日志（便于调试）
vikingbot chat --logs
```

就这么简单！您只需 2 分钟就能拥有一个可用的 AI 助手。

## 🐳 Docker 部署

您也可以使用 Docker 部署 vikingbot，以便更轻松地设置和隔离。

### 前置要求

首先安装 Docker：
- **macOS**：下载 [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Windows**：下载 [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Linux**：参考 [Docker 官方文档](https://docs.docker.com/engine/install/)

验证 Docker 安装：
```bash
docker --version
```

### 快速火山引擎镜像仓库部署（推荐）
### 快速 Docker 部署

```bash
# 1. 创建必要目录
mkdir -p ~/.openviking/

# 2. 启动容器
docker run -d \
    --name vikingbot \
    --restart unless-stopped \
    --platform linux/amd64 \
    -v ~/.openviking:/root/.openviking \
    -p 18791:18791 \
    vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:latest \
    gateway

# 3. 查看日志
docker logs --tail 50 -f vikingbot
```

按 `Ctrl+C` 退出日志视图，容器将继续在后台运行。

### 本地构建和部署

如果您想在本地构建 Docker 镜像：

```bash
# 构建镜像
./deploy/docker/build-image.sh

# 部署
./deploy/docker/deploy.sh

# 停止
./deploy/docker/stop.sh
```

更多 Docker 部署选项，请查看 [deploy/docker/README.md](deploy/docker/README.md)。


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
> 修改配置后（无论是通过控制台 UI 还是直接编辑文件），
> 您需要重启网关服务以使更改生效。

### Openviking Server配置
bot将连接远程的OpenViking服务器，使用前需启动Openviking Server。 默认使用`ov.conf`中配置的OpenViking server信息
- Openviking默认启动地址为 127.0.0.1:1933
- 如果配置了 root_api_key，则开启多租户模式。详见 [多租户](https://github.com/volcengine/OpenViking/blob/main/examples/multi_tenant/README.md)
- Openviking Server配置示例
```json
{
  "server": {
    
    "host": "127.0.0.1",
    "port": 1933,
    "root_api_key": "test"
  }
}
```

### bot配置
全部配置在`ov.conf`中`bot`字段下，配置项自带默认值。可选手动配置项说明如下：
- `agents`：Agent 配置
  - max_tool_iterations：单轮对话任务最大循环次数，超过则直接返回结果
  - memory_window：自动提交session到Openviking的对话轮次上限
  - gen_image_model：生成图片的模型
- gateway：Gateway 配置
  - host：Gateway 监听地址，默认值为 `0.0.0.0`
  - port：Gateway 监听端口，默认值为 `18790`
- sandbox：沙箱配置
  - mode：沙箱模式，可选值为 `shared`（所有session共享工作空间）或 `private`（私有，按Channel、session隔离工作空间）。默认值为 `shared`。
- ov_server：OpenViking Server 配置。
  - 不配置，默认使用`ov.conf`中配置的OpenViking server信息
  - 若不使用本地启动的OpenViking Server，可在此配置url和对应的root user的API Key
- channels：消息平台配置，详见 [消息平台配置](bot/docs/CHANNEL.md)

```json
{
  "bot": {
    "agents": {
      "max_tool_iterations": 50,
      "memory_window": 50,
      "gen_image_model": "openai/doubao-seedream-4-5-251128"
    },
    "gateway": {
      "host": "0.0.0.0",
      "port": 18790
    },
    "sandbox": {
      "mode": "shared"
    },
    "ov_server": {
      "server_url": "http://127.0.0.1:1933",
      "root_api_key": "test"
    },
    "channels": [
      {
        "type": "feishu",
        "enabled": true,
        "appId": "",
        "appSecret": "",
        "allowFrom": []
      }
    ]
  }
}
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
| `user_memory_search` | 搜索 OpenViking 用户记忆 |

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

如果您更喜欢直接编辑配置文件而不是使用控制台 UI：

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
uv pip install -e ".[langfuse]"
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

</details>

### 安全

| 选项 | 默认值 | 描述 |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `true` | 当为 `true` 时，将**所有**代理工具（shell、文件读/写/编辑、列表）限制到工作区目录。防止路径遍历和范围外访问。 |
| `channels.*.allowFrom` | `[]`（允许所有） | 用户 ID 白名单。空 = 允许所有人；非空 = 只有列出的用户可以交互。 |

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
      "backend": "opensandbox",
      "mode": "per-session"
    }
  }
}
```

**可用后端：**
| 后端 | 描述 |
|---------|-------------|
| `direct` | （默认）直接在主机上运行代码 |
| `docker` | 使用 Docker 容器进行隔离 |
| `opensandbox` | 使用 OpenSandbox 服务 |
| `srt` | 使用 Anthropic 的 SRT 沙箱运行时 |
| `aiosandbox` | 使用 AIO Sandbox 服务 |

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

**OpenSandbox 后端：**
```json
{
  "bot": {
    "sandbox": {
      "backend": "opensandbox",
      "backends": {
        "opensandbox": {
          "serverUrl": "http://localhost:18792",
          "apiKey": "",
          "defaultImage": "opensandbox/code-interpreter:v1.0.1"
        }
      }
    }
  }
}
```

**Docker 后端：**
```json
{
  "bot": {
    "sandbox": {
      "backend": "docker",
      "backends": {
        "docker": {
          "image": "python:3.11-slim",
          "networkMode": "bridge"
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
          "settingsPath": "~/.vikingbot/srt-settings.json",
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

**AIO Sandbox 后端：**
```json
{
  "bot": {
    "sandbox": {
      "backend": "aiosandbox",
      "backends": {
        "aiosandbox": {
          "baseUrl": "http://localhost:18794"
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
  "sandbox": {
    "backends": {
      "srt": {
        "nodePath": "/usr/local/bin/node"
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
| `vikingbot chat -m "..."` | 与代理聊天 |
| `vikingbot chat` | 交互式聊天模式 |
| `vikingbot chat --no-markdown` | 显示纯文本回复 |
| `vikingbot chat --logs` | 聊天期间显示运行时日志 |
| `vikingbot gateway` | 启动网关和控制台 Web UI |
| `vikingbot status` | 显示状态 |
| `vikingbot channels login` | 链接 WhatsApp（扫描二维码） |
| `vikingbot channels status` | 显示渠道状态 |

## 🖥️ 控制台 Web UI

当您运行 `vikingbot gateway` 时，控制台 Web UI 会自动启动，可通过 http://localhost:18791 访问。

**功能：**
- **仪表板**：系统状态和会话的快速概览
- **配置**：在用户友好的界面中配置提供商、代理、渠道和工具
  - 基于表单的编辑器，便于配置
  - 为高级用户提供的 JSON 编辑器
- **会话**：查看和管理聊天会话
- **工作区**：浏览和编辑工作区目录中的文件

> [!IMPORTANT]
> 在控制台中保存配置更改后，您需要重启网关服务以使更改生效。

交互模式退出：`exit`、`quit`、`/exit`、`/quit`、`:q` 或 `Ctrl+D`。

<details>
<summary><b>定时任务（Cron）</b></summary>

```bash
# 添加任务
vikingbot cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"
vikingbot cron add --name "hourly" --message "Check status" --every 3600

# 列出任务
vikingbot cron list

# 移除任务
vikingbot cron remove <job_id>
```

</details>

