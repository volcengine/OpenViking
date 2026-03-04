
# Vikingbot

**Vikingbot**, built on the [Nanobot](https://github.com/HKUDS/nanobot) project, is designed to deliver an OpenClaw-like bot integrated with OpenViking.

## 📦 Install

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
cd OpenViking/bot

# Create a virtual environment using Python 3.11 or higher
# uv will automatically fetch the required Python version if it's missing
uv venv --python 3.11

# Activate environment
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install dependencies (minimal)
uv pip install -e .

# Or install with optional features
uv pip install -e ".[langfuse,telegram,console]"
```

### Optional Dependencies

Install only the features you need:

| Feature Group | Install Command | Description |
|---------------|-----------------|-------------|
| **Full** | `uv pip install -e ".[full]"` | All features included |
| **Langfuse** | `uv pip install -e ".[langfuse]"` | LLM observability and tracing |
| **FUSE** | `uv pip install -e ".[fuse]"` | OpenViking filesystem mount |
| **Sandbox** | `uv pip install -e ".[sandbox]"` | Code execution sandbox |
| **OpenCode** | `uv pip install -e ".[opencode]"` | OpenCode AI integration |

#### Channels (chat apps)

| Channel | Install Command |
|---------|-----------------|
| **Telegram** | `uv pip install -e ".[telegram]"` |
| **Feishu/Lark** | `uv pip install -e ".[feishu]"` |
| **DingTalk** | `uv pip install -e ".[dingtalk]"` |
| **Slack** | `uv pip install -e ".[slack]"` |
| **QQ** | `uv pip install -e ".[qq]"` |

Multiple features can be combined:
```bash
uv pip install -e ".[langfuse,telegram,console]"
```

## 🚀 Quick Start

> [!TIP]
> The easiest way to configure vikingbot is through the Console Web UI!
> Get API keys: [OpenRouter](https://openrouter.ai/keys) (Global) · [Brave Search](https://brave.com/search/api/) (optional, for web search)

**1. Start the gateway**

```bash
vikingbot gateway
```

This will automatically:
- Create a default config at `~/.openviking/ov.conf`
- Start the Console Web UI at http://localhost:18791

**2. Configure via Console**

Open http://localhost:18791 in your browser and:
- Go to the **Config** tab
- Add your provider API keys (OpenRouter, OpenAI, etc.)
- Save the config

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

## 🐳 Docker Deployment

You can also deploy vikingbot using Docker for easier setup and isolation.

### Prerequisites

First, install Docker:
- **macOS**: Download [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Windows**: Download [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Linux**: Follow [Docker's official docs](https://docs.docker.com/engine/install/)

Verify Docker installation:
```bash
docker --version
```

### Quick Docker Deploy

```bash
# 1. Create necessary directories
mkdir -p ~/.vikingbot/

# 2. Start container
docker run -d \
    --name vikingbot \
    --restart unless-stopped \
    --platform linux/amd64 \
    -v ~/.openviking:/root/.openviking \
    -p 18791:18791 \
    vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot:latest \
    gateway

# 3. View logs
docker logs --tail 50 -f vikingbot
```

Press `Ctrl+C` to exit log view, the container continues running in background.

### Local Build and Deploy

If you want to build the Docker image locally:

```bash
# Build image
./deploy/docker/build-image.sh

# Deploy
./deploy/docker/deploy.sh

# Stop
./deploy/docker/stop.sh
```

For more Docker deployment options, see [deploy/docker/README.md](deploy/docker/README.md).

## 💬 Chat Apps

Talk to your vikingbot through Telegram, Discord, WhatsApp, Feishu, Mochat, DingTalk, Slack, Email, or QQ — anytime, anywhere.

| Channel | Setup |
|---------|-------|
| **Telegram** | Easy (just a token) |
| **Discord** | Easy (bot token + intents) |
| **WhatsApp** | Medium (scan QR) |
| **Feishu** | Medium (app credentials) |
| **Mochat** | Medium (claw token + websocket) |
| **DingTalk** | Medium (app credentials) |
| **Slack** | Medium (bot + app tokens) |
| **Email** | Medium (IMAP/SMTP credentials) |
| **QQ** | Easy (app credentials) |

<details>
<summary><b>Telegram</b> (Recommended)</summary>

**1. Create a bot**
- Open Telegram, search `@BotFather`
- Send `/newbot`, follow prompts
- Copy the token

**2. Configure**

```json
{
  "bot": {
    "channels": [
      {
        "type": "telegram",
        "enabled": true,
        "token": "YOUR_BOT_TOKEN",
        "allowFrom": ["YOUR_USER_ID"]
      }
    ]
  }
}
```

> You can find your **User ID** in Telegram settings. It is shown as `@yourUserId`.
> Copy this value **without the `@` symbol** and paste it into the config file.


**3. Run**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>Mochat (Claw IM)</b></summary>

Uses **Socket.IO WebSocket** by default, with HTTP polling fallback.

**1. Ask vikingbot to set up Mochat for you**

Simply send this message to vikingbot (replace `xxx@xxx` with your real email):

```
Read https://raw.githubusercontent.com/HKUDS/MoChat/refs/heads/main/skills/vikingbot/skill.md and register on MoChat. My Email account is xxx@xxx Bind me as your owner and DM me on MoChat.
```

vikingbot will automatically register, configure `~/.openviking/ov.conf`, and connect to Mochat.

**2. Restart gateway**

```bash
vikingbot gateway
```

That's it — vikingbot handles the rest!

<br>

<details>
<summary>Manual configuration (advanced)</summary>

If you prefer to configure manually, add the following to `~/.openviking/ov.conf`:

> Keep `claw_token` private. It should only be sent in `X-Claw-Token` header to your Mochat API endpoint.

```json
{
  "bot": {
    "channels": [
      {
        "type": "mochat",
        "enabled": true,
        "base_url": "https://mochat.io",
        "socket_url": "https://mochat.io",
        "socket_path": "/socket.io",
        "claw_token": "claw_xxx",
        "agent_user_id": "6982abcdef",
        "sessions": ["*"],
        "panels": ["*"],
        "reply_delay_mode": "non-mention",
        "reply_delay_ms": 120000
      }
    ]
  }
}
```



</details>

</details>

<details>
<summary><b>Discord</b></summary>

**1. Create a bot**
- Go to https://discord.com/developers/applications
- Create an application → Bot → Add Bot
- Copy the bot token

**2. Enable intents**
- In the Bot settings, enable **MESSAGE CONTENT INTENT**
- (Optional) Enable **SERVER MEMBERS INTENT** if you plan to use allow lists based on member data

**3. Get your User ID**
- Discord Settings → Advanced → enable **Developer Mode**
- Right-click your avatar → **Copy User ID**

**4. Configure**

```json
{
  "bot": {
    "channels": [
      {
        "type": "discord",
        "enabled": true,
        "token": "YOUR_BOT_TOKEN",
        "allowFrom": ["YOUR_USER_ID"]
      }
    ]
  }
}
```

**5. Invite the bot**
- OAuth2 → URL Generator
- Scopes: `bot`
- Bot Permissions: `Send Messages`, `Read Message History`
- Open the generated invite URL and add the bot to your server

**6. Run**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

Requires **Node.js ≥18**.

**1. Link device**

```bash
vikingbot channels login
# Scan QR with WhatsApp → Settings → Linked Devices
```

**2. Configure**

```json
{
  "bot": {
    "channels": [
      {
        "type": "whatsapp",
        "enabled": true,
        "allowFrom": ["+1234567890"]
      }
    ]
  }
}
```

**3. Run** (two terminals)

```bash
# Terminal 1
vikingbot channels login

# Terminal 2
vikingbot gateway
```

</details>

<details>
<summary><b>Feishu (飞书)</b></summary>

Uses **WebSocket** long connection — no public IP required.

**1. Create a Feishu bot**
- Visit [Feishu Open Platform](https://open.feishu.cn/app)
- Create a new app → Enable **Bot** capability
- **Permissions**: Add `im:message` (send messages)
- **Events**: Add `im.message.receive_v1` (receive messages)
  - Select **Long Connection** mode (requires running vikingbot first to establish connection)
- Get **App ID** and **App Secret** from "Credentials & Basic Info"
- Publish the app

**2. Configure**

```json
{
  "bot": {
    "channels": [
      {
        "type": "feishu",
        "enabled": true,
        "appId": "cli_xxx",
        "appSecret": "xxx",
        "encryptKey": "",
        "verificationToken": "",
        "allowFrom": []
      }
    ]
  }
}
```

> `encryptKey` and `verificationToken` are optional for Long Connection mode.
> `allowFrom`: Leave empty to allow all users, or add `["ou_xxx"]` to restrict access.

**3. Run**

```bash
vikingbot gateway
```

> [!TIP]
> Feishu uses WebSocket to receive messages — no webhook or public IP needed!

</details>

<details>
<summary><b>QQ (QQ单聊)</b></summary>

Uses **botpy SDK** with WebSocket — no public IP required. Currently supports **private messages only**.

**1. Register & create bot**
- Visit [QQ Open Platform](https://q.qq.com) → Register as a developer (personal or enterprise)
- Create a new bot application
- Go to **开发设置 (Developer Settings)** → copy **AppID** and **AppSecret**

**2. Set up sandbox for testing**
- In the bot management console, find **沙箱配置 (Sandbox Config)**
- Under **在消息列表配置**, click **添加成员** and add your own QQ number
- Once added, scan the bot's QR code with mobile QQ → open the bot profile → tap "发消息" to start chatting

**3. Configure**

> - `allowFrom`: Leave empty for public access, or add user openids to restrict. You can find openids in the vikingbot logs when a user messages the bot.
> - For production: submit a review in the bot console and publish. See [QQ Bot Docs](https://bot.q.qq.com/wiki/) for the full publishing flow.

```json
{
  "bot": {
    "channels": [
      {
        "type": "qq",
        "enabled": true,
        "appId": "YOUR_APP_ID",
        "secret": "YOUR_APP_SECRET",
        "allowFrom": []
      }
    ]
  }
}
```

**4. Run**

```bash
vikingbot gateway
```

Now send a message to the bot from QQ — it should respond!

</details>

<details>
<summary><b>DingTalk (钉钉)</b></summary>

Uses **Stream Mode** — no public IP required.

**1. Create a DingTalk bot**
- Visit [DingTalk Open Platform](https://open-dev.dingtalk.com/)
- Create a new app -> Add **Robot** capability
- **Configuration**:
  - Toggle **Stream Mode** ON
- **Permissions**: Add necessary permissions for sending messages
- Get **AppKey** (Client ID) and **AppSecret** (Client Secret) from "Credentials"
- Publish the app

**2. Configure**

```json
{
  "bot": {
    "channels": [
      {
        "type": "dingtalk",
        "enabled": true,
        "clientId": "YOUR_APP_KEY",
        "clientSecret": "YOUR_APP_SECRET",
        "allowFrom": []
      }
    ]
  }
}
```

> `allowFrom`: Leave empty to allow all users, or add `["staffId"]` to restrict access.

**3. Run**

```bash
vikingbot gateway
```

</details>

<details>
<summary><b>Slack</b></summary>

Uses **Socket Mode** — no public URL required.

**1. Create a Slack app**
- Go to [Slack API](https://api.slack.com/apps) → **Create New App** → "From scratch"
- Pick a name and select your workspace

**2. Configure the app**
- **Socket Mode**: Toggle ON → Generate an **App-Level Token** with `connections:write` scope → copy it (`xapp-...`)
- **OAuth & Permissions**: Add bot scopes: `chat:write`, `reactions:write`, `app_mentions:read`
- **Event Subscriptions**: Toggle ON → Subscribe to bot events: `message.im`, `message.channels`, `app_mention` → Save Changes
- **App Home**: Scroll to **Show Tabs** → Enable **Messages Tab** → Check **"Allow users to send Slash commands and messages from the messages tab"**
- **Install App**: Click **Install to Workspace** → Authorize → copy the **Bot Token** (`xoxb-...`)

**3. Configure vikingbot**

```json
{
  "bot": {
    "channels": [
      {
        "type": "slack",
        "enabled": true,
        "botToken": "xoxb-...",
        "appToken": "xapp-...",
        "groupPolicy": "mention"
      }
    ]
  }
}
```

**4. Run**

```bash
vikingbot gateway
```

DM the bot directly or @mention it in a channel — it should respond!

> [!TIP]
> - `groupPolicy`: `"mention"` (default — respond only when @mentioned), `"open"` (respond to all channel messages), or `"allowlist"` (restrict to specific channels).
> - DM policy defaults to open. Set `"dm": {"enabled": false}` to disable DMs.

</details>

<details>
<summary><b>Email</b></summary>

Give vikingbot its own email account. It polls **IMAP** for incoming mail and replies via **SMTP** — like a personal email assistant.

**1. Get credentials (Gmail example)**
- Create a dedicated Gmail account for your bot (e.g. `my-vikingbot@gmail.com`)
- Enable 2-Step Verification → Create an [App Password](https://myaccount.google.com/apppasswords)
- Use this app password for both IMAP and SMTP

**2. Configure**

> - `consentGranted` must be `true` to allow mailbox access. This is a safety gate — set `false` to fully disable.
> - `allowFrom`: Leave empty to accept emails from anyone, or restrict to specific senders.
> - `smtpUseTls` and `smtpUseSsl` default to `true` / `false` respectively, which is correct for Gmail (port 587 + STARTTLS). No need to set them explicitly.
> - Set `"autoReplyEnabled": false` if you only want to read/analyze emails without sending automatic replies.

```json
{
  "bot": {
    "channels": [
      {
        "type": "email",
        "enabled": true,
        "consentGranted": true,
        "imapHost": "imap.gmail.com",
        "imapPort": 993,
        "imapUsername": "my-vikingbot@gmail.com",
        "imapPassword": "your-app-password",
        "smtpHost": "smtp.gmail.com",
        "smtpPort": 587,
        "smtpUsername": "my-vikingbot@gmail.com",
        "smtpPassword": "your-app-password",
        "fromAddress": "my-vikingbot@gmail.com",
        "allowFrom": ["your-real-email@gmail.com"]
      }
    ]
  }
}
```


**3. Run**

```bash
vikingbot gateway
```

</details>

## 🌐 Agent Social Network

🐈 vikingbot is capable of linking to the agent social network (agent community). **Just send one message and your vikingbot joins automatically!**

| Platform | How to Join (send this message to your bot) |
|----------|-------------|
| [**Moltbook**](https://www.moltbook.com/) | `Read https://moltbook.com/skill.md and follow the instructions to join Moltbook` |
| [**ClawdChat**](https://clawdchat.ai/) | `Read https://clawdchat.ai/skill.md and follow the instructions to join ClawdChat` |

Simply send the command above to your vikingbot (via CLI or any chat channel), and it will handle the rest.

## ⚙️ Configuration

Config file: `~/.openviking/ov.conf`

> [!IMPORTANT]
> After modifying the configuration (either via Console UI or by editing the file directly),
> you need to restart the gateway service for changes to take effect.

> [!NOTE]
> Configuration has been migrated from `~/.vikingbot/config.json` to `~/.openviking/ov.conf`.
> The configuration is now nested under the `bot` key.

### Manual Configuration (Advanced)

If you prefer to edit the config file directly instead of using the Console UI:

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
  "langfuse": {
    "enabled": true,
    "secret_key": "sk-lf-vikingbot-secret-key-2026",
    "public_key": "pk-lf-vikingbot-public-key-2026",
    "base_url": "http://localhost:3000"
  }
}
```

For Langfuse Cloud, use `https://cloud.langfuse.com` as the `base_url`.

**Install Langfuse support:**
```bash
uv pip install -e ".[langfuse]"
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
  "sandbox": {
    "backend": "srt",
    "mode": "per-session"
  }
}
```

**Available Backends:**
| Backend | Description |
|---------|-------------|
| `direct` | (Default) Runs code directly on the host |
| `opensandbox` | Uses OpenSandbox service |
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
  "sandbox": {
    "backends": {
      "direct": {
        "restrictToWorkspace": false
      }
    }
  }
}
```

**OpenSandbox Backend:**
```json
{
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
```

**Docker Backend:**
```json
{
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
```

**SRT Backend:**
```json
{
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
```

**AIO Sandbox Backend:**
```json
{
  "sandbox": {
    "backend": "aiosandbox",
    "backends": {
      "aiosandbox": {
        "baseUrl": "http://localhost:18794"
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
  "sandbox": {
    "backends": {
      "srt": {
        "nodePath": "/usr/local/bin/node"
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
| `vikingbot gateway` | Start the gateway and Console Web UI |
| `vikingbot status` | Show status |
| `vikingbot channels login` | Link WhatsApp (scan QR) |
| `vikingbot channels status` | Show channel status |

## 🖥️ Console Web UI

The Console Web UI is automatically started when you run `vikingbot gateway`, accessible at http://localhost:18791.

**Features:**
- **Dashboard**: Quick overview of system status and sessions
- **Config**: Configure providers, agents, channels, and tools in a user-friendly interface
  - Form-based editor for easy configuration
  - JSON editor for advanced users
- **Sessions**: View and manage chat sessions
- **Workspace**: Browse and edit files in the workspace directory

> [!IMPORTANT]
> After saving configuration changes in the Console, you need to restart the gateway service for changes to take effect.

Interactive mode exits: `exit`, `quit`, `/exit`, `/quit`, `:q`, or `Ctrl+D`.

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
