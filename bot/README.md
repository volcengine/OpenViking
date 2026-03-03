
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

# Install dependencies
uv pip install -e .
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
- Create a default config at `~/.vikingbot/config.json`
- Start the Console Web UI at http://localhost:18791

**2. Configure via Console**

Open http://localhost:18791 in your browser and:
- Go to the **Config** tab
- Add your provider API keys (OpenRouter, OpenAI, etc.)
- Save the config

**3. Chat**

```bash
vikingbot agent -m "What is 2+2?"
```

That's it! You have a working AI assistant in 2 minutes.

## 🐳 Docker Deployment

You can also deploy vikingbot using Docker for easier setup and isolation.

## ☁️ Volcengine VKE Deployment

If you want to deploy vikingbot on Volcengine Kubernetes Engine (VKE), see the detailed deployment guide:

👉 [VKE Deployment Guide (Chinese)](deploy/vke/README.md)

The guide includes:
- Complete prerequisites
- How to create Volcengine account, VKE cluster, container registry, and TOS bucket
- One-click deployment script usage
- Configuration details and troubleshooting
### Prerequisites

First, install Docker:
- **macOS**: Download [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Windows**: Download [Docker Desktop](https://www.docker.com/products/docker-desktop)
- **Linux**: Follow [Docker's official docs](https://docs.docker.com/engine/install/)

Verify Docker installation:
```bash
docker --version
```

### Quick Volcengine Registry Deploy (Recommended)
### Quick Docker Deploy

```bash
# 1. Create necessary directories
mkdir -p ~/.vikingbot/

# 2. Start container
docker run -d \
    --name vikingbot \
    --restart unless-stopped \
    --platform linux/amd64 \
    -v ~/.vikingbot:/root/.vikingbot \
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
  "channels": [
    {
      "type": "telegram",
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  ]
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

vikingbot will automatically register, configure `~/.vikingbot/config.json`, and connect to Mochat.

**2. Restart gateway**

```bash
vikingbot gateway
```

That's it — vikingbot handles the rest!

<br>

<details>
<summary>Manual configuration (advanced)</summary>

If you prefer to configure manually, add the following to `~/.vikingbot/config.json`:

> Keep `claw_token` private. It should only be sent in `X-Claw-Token` header to your Mochat API endpoint.

```json
{
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
  "channels": [
    {
      "type": "discord",
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  ]
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
  "channels": [
    {
      "type": "whatsapp",
      "enabled": true,
      "allowFrom": ["+1234567890"]
    }
  ]
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

Config file: `~/.vikingbot/config.json`

> [!IMPORTANT]
> After modifying the configuration (either via Console UI or by editing the file directly),
> you need to restart the gateway service for changes to take effect.

### Manual Configuration (Advanced)

If you prefer to edit the config file directly instead of using the Console UI:

```json
{
  "providers": {
    "openai": {
      "apiKey": "sk-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "openai/doubao-seed-2-0-pro-260215"
    }
  }
}
```

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

### Sandbox

vikingbot supports sandboxed execution for enhanced security. By default, sandbox is disabled. To enable sandbox with SRT backend in per-session mode, set `"enabled": true`.

<details>
<summary><b>Sandbox Configuration (SRT Backend)</b></summary>

```json
{
  "sandbox": {
    "enabled": false,
    "backend": "srt",
    "mode": "per-session",
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
    },
    "backends": {
      "srt": {
        "nodePath": "node"
      }
    }
  }
}
```

**Configuration Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `false` | Enable sandbox execution |
| `backend` | `"srt"` | Sandbox backend: `srt` or `docker` |
| `mode` | `"per-session"` | Sandbox mode: `per-session` (isolated per session) or `shared` (shared across sessions) |
| `network.allowedDomains` | `[]` | List of allowed domains for network access (empty = all allowed) |
| `network.deniedDomains` | `[]` | List of denied domains (blocked regardless of allowed list) |
| `network.allowLocalBinding` | `false` | Allow binding to local addresses (localhost, 127.0.0.1) |
| `filesystem.denyRead` | `[]` | Paths/files to deny read access |
| `filesystem.allowWrite` | `[]` | Paths/files to explicitly allow write access |
| `filesystem.denyWrite` | `[]` | Paths/files to deny write access |
| `runtime.cleanupOnExit` | `true` | Clean up sandbox resources on exit |
| `runtime.timeout` | `300` | Command execution timeout in seconds |
| `backends.srt.nodePath` | `"/usr/local/bin/node"` | Path to Node.js executable (use full path if `node` is not in PATH) |

**SRT Backend Setup:**

The SRT backend uses `@anthropic-ai/sandbox-runtime`. It's automatically installed when you run `vikingbot onboard`.

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
| `vikingbot agent -m "..."` | Chat with the agent |
| `vikingbot agent` | Interactive chat mode |
| `vikingbot agent --no-markdown` | Show plain-text replies |
| `vikingbot agent --logs` | Show runtime logs during chat |
| `vikingbot tui` | Launch TUI (Terminal User Interface) |
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
<summary><b>TUI (Terminal User Interface)</b></summary>

Launch the vikingbot TUI for a rich terminal-based chat experience:

```bash
vikingbot tui
```

The TUI provides:
- Rich text rendering with markdown support
- Message history and conversation management
- Real-time agent responses
- Keyboard shortcuts for navigation

</details>

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
