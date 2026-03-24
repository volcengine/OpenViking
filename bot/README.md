# Vikingbot

**Vikingbot**, built on the [Nanobot](https://github.com/HKUDS/nanobot) project, is designed to deliver an OpenClaw-like bot integrated with OpenViking.

## ✨ Core Features of OpenViking

Vikingbot is deeply integrated with OpenViking, providing powerful knowledge management and memory retrieval capabilities:

- **Dual local/remote modes**: Supports local storage (`~/.openviking/data/`) and remote server mode
- **7 dedicated Agent tools**: Resource management, semantic search, regex search, glob search, memory search
- **Three-level content access**: L0 (summary), L1 (overview), L2 (full content)
- **Automatic session memory submission**: Conversation history is automatically saved to OpenViking
- **Model configuration**: Read from OpenViking configuration (`vlm` section), no need to set provider separately in bot configuration

## 📦 Install

**Option 1: Install from PyPI (Simplest)**
```bash
pip install "openviking[bot]"
```

**Option 2: Install from source (for development)**

**Prerequisites**

First, install [uv](https://github.com/astral-sh/uv) (an extremely fast Python package installer):

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Install from source** (latest features, recommended for development)

```bash
git clone https://github.com/volcengine/OpenViking
cd OpenViking

# Create a virtual environment using Python 3.11 or higher
uv venv --python 3.11

# Activate environment
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install dependencies (minimal)
uv pip install -e ".[bot]"

# Or install with optional features
uv pip install -e ".[bot,bot-langfuse,bot-telegram]"
```

### Optional Dependencies

Install only the features you need:

| Feature Group | Install Command | Description |
|---------------|-----------------|-------------|
| **Full** | `uv pip install -e ".[bot-full]"` | All features included |
| **Langfuse** | `uv pip install -e ".[bot-langfuse]"` | LLM observability and tracing |
| **FUSE** | `uv pip install -e ".[bot-fuse]"` | OpenViking filesystem mount |
| **Sandbox** | `uv pip install -e ".[bot-sandbox]"` | Code execution sandbox |
| **OpenCode** | `uv pip install -e ".[bot-opencode]"` | OpenCode AI integration |

#### Channels (chat apps)

| Channel | Install Command |
|---------|-----------------|
| **Telegram** | `uv pip install -e ".[bot-telegram]"` |
| **Feishu/Lark** | `uv pip install -e ".[bot-feishu]"` |
| **DingTalk** | `uv pip install -e ".[bot-dingtalk]"` |
| **Slack** | `uv pip install -e ".[bot-slack]"` |
| **QQ** | `uv pip install -e ".[bot-qq]"` |

Multiple features can be combined:
```bash
uv pip install -e ".[bot,bot-langfuse,bot-telegram]"
```

## 🚀 Quick Start

> [!TIP]
> Configure vikingbot through the configuration file `~/.openviking/ov.conf`!
> Get API keys: [OpenRouter](https://openrouter.ai/keys) (Global) · [Brave Search](https://brave.com/search/api/) (optional, for web search)

**1. Initialize configuration**

```bash
vikingbot gateway
```

This will automatically:
- Create a default config at `~/.openviking/ov.conf`
- Create bot startup files in the OpenViking workspace, default path is `~/.openviking/data/bot/`

**2. Configure via ov.conf**

Edit `~/.openviking/ov.conf` to add your provider API keys (OpenRouter, OpenAI, etc.) and save the config.

**3. Chat**

```bash
# Send a single message directly
vikingbot chat -m "What is 2+2?"

# Enter interactive chat mode (supports multi-turn conversations)
vikingbot chat

# Show plain-text replies (no Markdown rendering)
vikingbot chat --no-markdown

# Show runtime logs during chat (useful for debugging)
vikingbot chat --logs
```

That's it! You have a working AI assistant in 2 minutes.

Talk to your vikingbot through Telegram, Discord, WhatsApp, Feishu, Mochat, DingTalk, Slack, Email, or QQ — anytime, anywhere.

For detailed configuration, please refer to [CHANNEL.md](bot/docs/CHANNEL.md).

## 🌐 Agent Social Network

🐈 vikingbot is capable of linking to the agent social network (agent community). **Just send one message and your vikingbot joins automatically!**

| Platform | How to Join (send this message to your bot) |
|----------|-------------|
| [**Moltbook**](https://www.moltbook.com/) | `Read https://moltbook.com/skill.md and follow the instructions to join Moltbook` |
| [**ClawdChat**](https://clawdchat.ai/) | `Read https://clawdchat.ai/skill.md and follow the instructions to join ClawdChat` |

Simply send the command above to your vikingbot (via CLI or any chat channel), and it will handle the rest.

## ⚙️ Configuration

Config file: `~/.openviking/ov.conf` (custom path can be set via environment variable `OPENVIKING_CONFIG_FILE`)

> [!TIP]
> Vikingbot shares the same configuration file with OpenViking. Configuration items are located under the `bot` field of the file, and will automatically merge global configurations such as `vlm`, `storage`, `server`, etc. No need to maintain a separate configuration file.

> [!IMPORTANT]
> After modifying the configuration (by editing the file directly),
> you need to restart the gateway service for changes to take effect.

### OpenViking Server Configuration
The bot will connect to the remote OpenViking server. Please start the OpenViking Server before use. By default, the OpenViking server information configured in `ov.conf` is used
- OpenViking default startup address is 127.0.0.1:1933
- If `root_api_key` is configured, multi-tenant mode is enabled. For details, see [Multi-tenant](https://github.com/volcengine/OpenViking/blob/main/examples/multi_tenant/README.md)
- OpenViking Server configuration example
```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933,
    "root_api_key": "test"
  }
}
```

### Bot Configuration
All configurations are under the `bot` field in `ov.conf`, with default values for configuration items. The optional manual configuration items are described as follows:
- `agents`: Agent configuration
  - `max_tool_iterations`: Maximum number of cycles for a single round of conversation tasks, returns results directly if exceeded
  - `memory_window`: Upper limit of conversation rounds for automatically submitting sessions to OpenViking
  - `gen_image_model`: Model for generating images
- `gateway`: Gateway configuration
  - `host`: Gateway listening address, default value is `0.0.0.0`
  - `port`: Gateway listening port, default value is `18790`
- `sandbox`: Sandbox configuration
  - `mode`: Sandbox mode, optional values are `shared` (all sessions share workspace) or `private` (private, workspace isolated by Channel and session). Default value is `shared`.
- `ov_server`: OpenViking Server configuration.
  - If not configured, the OpenViking server information configured in `ov.conf` is used by default
  - If you don't use the locally started OpenViking Server, you can configure the url and the corresponding root user's API Key here
    - root_api_key: In a multi-tenant scenario, the API KEY must have root privileges; otherwise, the bot cannot automatically register multiple OpenViking users, which is used to implement memory isolation.
    - account_id: Defaults to default, which is the account ID of OpenViking. All users under an OpenViking account share resources.
- `channels`: Message platform configuration, see [Message Platform Configuration](bot/docs/CHANNEL.md) for details

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

### OpenViking Agent Tools

Vikingbot provides 7 dedicated OpenViking tools:

| Tool Name | Description |
|----------|------|
| `openviking_read` | Read OpenViking resources (supports three levels: abstract/overview/read) |
| `openviking_list` | List OpenViking resources |
| `openviking_search` | Semantic search OpenViking resources |
| `openviking_add_resource` | Add local files as OpenViking resources |
| `openviking_grep` | Search OpenViking resources using regular expressions |
| `openviking_glob` | Match OpenViking resources using glob patterns |
| `user_memory_search` | Search OpenViking user memory |

### OpenViking Hooks

Vikingbot enables OpenViking hooks by default:

```json
{
  "hooks": ["vikingbot.hooks.builtins.openviking_hooks.hooks"]
}
```

| Hook | Function |
|------|------|
| `OpenVikingCompactHook` | Automatically submit session messages to OpenViking |
| `OpenVikingPostCallHook` | Post tool call hook (for testing purposes) |

### Manual Configuration (Advanced)

Edit the config file directly:

```json
{
  "bot": {
    "agents": {
      "model": "openai/doubao-seed-2-0-pro-260215"
    }
  }
}
```

Provider configuration is read from OpenViking config (`vlm` section in `ov.conf`).

### Providers

> [!TIP]
> - **Groq** provides free voice transcription via Whisper. If configured, Telegram voice messages will be automatically transcribed.
> - **Zhipu Coding Plan**: If you're on Zhipu's coding plan, set `"apiBase": "https://open.bigmodel.cn/api/coding/paas/v4"` in your zhipu provider config.
> - **MiniMax (Mainland China)**: If your API key is from MiniMax's mainland China platform (minimaxi.com), set `"apiBase": "https://api.minimaxi.com/v1"` in your minimax provider config.

| Provider | Purpose | Get API Key |
|----------|---------|-------------|
| `openrouter` | LLM (recommended, access to all models) | [openrouter.ai](https://openrouter.ai) |
| `anthropic` | LLM (Claude direct) | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | LLM (GPT direct) | [platform.openai.com](https://platform.openai.com) |
| `deepseek` | LLM (DeepSeek direct) | [platform.deepseek.com](https://platform.deepseek.com) |
| `groq` | LLM + **Voice transcription** (Whisper) | [console.groq.com](https://console.groq.com) |
| `gemini` | LLM (Gemini direct) | [aistudio.google.com](https://aistudio.google.com) |
| `minimax` | LLM (MiniMax direct) | [platform.minimax.io](https://platform.minimax.io) |
| `aihubmix` | LLM (API gateway, access to all models) | [aihubmix.com](https://aihubmix.com) |
| `dashscope` | LLM (Qwen) | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| `moonshot` | LLM (Moonshot/Kimi) | [platform.moonshot.cn](https://platform.moonshot.cn) |
| `zhipu` | LLM (Zhipu GLM) | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `vllm` | LLM (local, any OpenAI-compatible server) | — |

<details>
<summary><b>Adding a New Provider (Developer Guide)</b></summary>

vikingbot uses a **Provider Registry** (`vikingbot/providers/registry.py`) as the single source of truth.
Adding a new provider only takes **2 steps** — no if-elif chains to touch.

**Step 1.** Add a `ProviderSpec` entry to `PROVIDERS` in `vikingbot/providers/registry.py`:

```python
ProviderSpec(
    name="myprovider",                   # config field name
    keywords=("myprovider", "mymodel"),  # model-name keywords for auto-matching
    env_key="MYPROVIDER_API_KEY",        # env var for LiteLLM
    display_name="My Provider",          # shown in `vikingbot status`
    litellm_prefix="myprovider",         # auto-prefix: model → myprovider/model
    skip_prefixes=("myprovider/",),      # don't double-prefix
)
```

**Step 2.** Add a field to `ProvidersConfig` in `vikingbot/config/schema.py`:

```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = ProviderConfig()
```

That's it! Environment variables, model prefixing, config matching, and `vikingbot status` display will all work automatically.

**Common `ProviderSpec` options:**

| Field | Description | Example |
|-------|-------------|---------|
| `litellm_prefix` | Auto-prefix model names for LiteLLM | `"dashscope"` → `dashscope/qwen-max` |
| `skip_prefixes` | Don't prefix if model already starts with these | `("dashscope/", "openrouter/")` |
| `env_extras` | Additional env vars to set | `(("ZHIPUAI_API_KEY", "{api_key}"),)` |
| `model_overrides` | Per-model parameter overrides | `(("kimi-k2.5", {"temperature": 1.0}),)` |
| `is_gateway` | Can route any model (like OpenRouter) | `True` |
| `detect_by_key_prefix` | Detect gateway by API key prefix | `"sk-or-"` |
| `detect_by_base_keyword` | Detect gateway by API base URL | `"openrouter"` |
| `strip_model_prefix` | Strip existing prefix before re-prefixing | `True` (for AiHubMix) |

</details>


### Security

| Option | Default | Description |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `true` | When `true`, restricts **all** agent tools (shell, file read/write/edit, list) to the workspace directory. Prevents path traversal and out-of-scope access. |
| `channels.*.allowFrom` | `[]` (allow all) | Whitelist of user IDs. Empty = allow everyone; non-empty = only listed users can interact. |

### Observability (Optional)

**Langfuse** integration for LLM observability and tracing.

<details>
<summary><b>Langfuse Configuration</b></summary>

**Option 1: Local Deployment (Recommended for testing)**

Deploy Langfuse locally using Docker:

```bash
# Navigate to the deployment script
cd deploy/docker

# Run the deployment script
./deploy_langfuse.sh
```

This will start Langfuse locally at `http://localhost:3000` with pre-configured credentials.

**Option 2: Langfuse Cloud**

1. Sign up at [langfuse.com](https://langfuse.com)
2. Create a new project
3. Copy the **Secret Key** and **Public Key** from project settings

**Configuration**

Add to `~/.openviking/ov.conf`:

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

For Langfuse Cloud, use `https://cloud.langfuse.com` as the `base_url`.

**Install Langfuse support:**
```bash
uv pip install -e ".[bot-langfuse]"
```

**Restart vikingbot:**
```bash
vikingbot gateway
```

**Features enabled:**
- Automatic trace creation for each conversation
- Session and user tracking
- LLM call monitoring
- Token usage tracking

</details>

### Sandbox

vikingbot supports sandboxed execution for enhanced security.

**By default, no sandbox configuration is needed in `ov.conf`:**
- Default backend: `direct` (runs code directly on host)
- Default mode: `shared` (single sandbox shared across all sessions)

You only need to add sandbox configuration when you want to change these defaults.

<details>
<summary><b>Sandbox Configuration Options</b></summary>

**To use a different backend or mode:**
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

**Available Backends:**
| Backend | Description |
|---------|-------------|
| `direct` | (Default) Runs code directly on the host |
| `srt` | Uses Anthropic's SRT sandbox runtime |

**Available Modes:**
| Mode | Description |
|------|-------------|
| `shared` | (Default) Single sandbox shared across all sessions |
| `per-session` | Separate sandbox instance for each session |

**Backend-specific Configuration (only needed when using that backend):**

**Direct Backend:**
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

**SRT Backend:**
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


**SRT Backend Setup:**

The SRT backend uses `@anthropic-ai/sandbox-runtime`.

**System Dependencies:**

The SRT backend also requires these system packages to be installed:
- `ripgrep` (rg) - for text search
- `bubblewrap` (bwrap) - for sandbox isolation
- `socat` - for network proxy

**Install on macOS:**
```bash
brew install ripgrep bubblewrap socat
```

**Install on Ubuntu/Debian:**
```bash
sudo apt-get install -y ripgrep bubblewrap socat
```

**Install on Fedora/CentOS:**
```bash
sudo dnf install -y ripgrep bubblewrap socat
```

To verify installation:

```bash
npm list -g @anthropic-ai/sandbox-runtime
```

If not installed, install it manually:

```bash
npm install -g @anthropic-ai/sandbox-runtime
```

**Node.js Path Configuration:**

If `node` command is not found in PATH, specify the full path in your config:

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

To find your Node.js path:

```bash
which node
# or
which nodejs
```

</details>


## CLI Reference

| Command | Description |
|---------|-------------|
| `vikingbot chat -m "..."` | Chat with the agent |
| `vikingbot chat` | Interactive chat mode |
| `vikingbot chat --no-markdown` | Show plain-text replies |
| `vikingbot chat --logs` | Show runtime logs during chat |
| `vikingbot gateway` | Start the gateway |
| `vikingbot status` | Show status |
| `vikingbot channels login` | Link WhatsApp (scan QR) |
| `vikingbot channels status` | Show channel status |


<details>
<summary><b>Scheduled Tasks (Cron)</b></summary>

```bash
# Add a job
vikingbot cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"
vikingbot cron add --name "hourly" --message "Check status" --every 3600

# List jobs
vikingbot cron list

# Remove a job
vikingbot cron remove <job_id>
```

</details>
