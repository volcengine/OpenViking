# VikingBot

VikingBot is the multi-channel AI agent built into OpenViking. You can use it directly from the command line or run it as a long-lived Gateway connected to Feishu, Slack, Telegram, and other platforms. When connected to OpenViking, it also gains resource retrieval, user memory, experience memory, and session consolidation.

## Key Capabilities

- **Multiple chat entry points**: `vikingbot chat`, `ov chat`, HTTP APIs, and multiple chat platforms.
- **Agent tools**: built-in file, shell, web, image generation, scheduled task, and OpenViking tools.
- **Skills and subagents**: load Skills on demand and delegate independent work to background subagents.
- **Long-term context**: recall Resources, Peer Memories, and Experiences from OpenViking, and commit sessions automatically.
- **Safer execution**: Direct, SRT, OpenSandbox, and AIO Sandbox backends.
- **Service deployment**: the Gateway provides synchronous chat, SSE streaming, feedback, and an OpenViking API proxy.

## Installation

### Install from PyPI

```bash
pip install "openviking[bot]"
```

### Install from Source

Python 3.11 or later is required. We recommend using [uv](https://github.com/astral-sh/uv):

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[bot]"
```

On Windows, activate the virtual environment with:

```powershell
.venv\Scripts\activate
```

## Quick Start: Choose Your Scenario

VikingBot supports three primary usage scenarios. They are different entry points for different needs rather than mutually exclusive modes.

| Scenario | Best for | Start command | OpenViking |
|----------|----------|---------------|------------|
| **A. OpenViking + Bot together** | A complete local experience with resources, memory, and the Agent | `openviking-server --with-bot` | The Bot uses the OpenViking Server started by this command |
| **B. Debug the Agent locally** | Quickly testing the Bot or developing Tools and Skills | `vikingbot chat` | Optional; without it, the Bot cannot use OpenViking features |
| **C. Unified Gateway entry point** | Starting the Bot separately and connecting it to an existing OpenViking Server | `vikingbot gateway` | May be configured explicitly or omitted |

### Scenario A: Start OpenViking and the Bot Together

Use this for the complete local experience. OpenViking Server and VikingBot Gateway start together. `ov chat` first calls OpenViking Server, whose `/bot/v1` route forwards the request to VikingBot.

```text
ov chat → OpenViking Server → VikingBot Gateway → Agent
```

#### 1. Prepare the configuration

Follow the [OpenViking quickstart](../docs/en/getting-started/03-quickstart-server.md) to configure the models and storage required by OpenViking. By default, the Bot inherits the root-level `vlm` configuration as its Agent model. Configure `bot.agents` only if the Bot should use a separate model.

In this combined mode, the Bot always uses the OpenViking Server started by the same command and ignores `bot.ov_server` settings that point to another service. OpenViking Server injects an authenticated request-scoped identity into every Chat request sent to the Bot.

#### 2. Start both services

```bash
openviking-server --with-bot
```

This command starts the current OpenViking Server and a managed VikingBot Gateway. The Bot uses this Server and does not connect to the service named by `bot.ov_server.server_url`.

#### 3. Configure and use the `ov` CLI

Run the interactive configuration manager:

```bash
ov config
```

Point the active CLI configuration to OpenViking Server, for example `http://127.0.0.1:1933`. If the Server requires authentication, also enter the caller's User/Admin API Key. Then run:

```bash
ov chat
ov chat -m "Remember that I prefer concise answers"
ov find "my response preferences"
```

The identity flow is:

- `ovcli.conf.api_key` represents the current caller.
- OpenViking Server validates the identity and passes a request-scoped connection to the Bot.
- The request identity takes priority over any process-level default identity, preventing multiple callers from sharing one Bot user.

### Scenario B: Debug the Agent Locally

Use this to try VikingBot quickly or develop Agents, Tools, and Skills. `vikingbot chat` starts the Agent in the current process. It does not require a running Gateway and does not use `ovcli.conf` as the Bot configuration.

#### 1. Configure a model

Edit `~/.openviking/ov.conf`:

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

Alternatively, configure only the root-level `vlm` section. VikingBot inherits its model, provider, API key, API base, and timeout settings.

#### 2. Start chatting

```bash
# Send one message
vikingbot chat -m "Summarize the structure of the current project"

# Start an interactive multi-turn conversation
vikingbot chat

# Use a specific session
vikingbot chat --session my-session
```

If no OpenViking Server is available, VikingBot runs in standalone mode. File, shell, web, and Skill capabilities remain available, but OpenViking memory and file tools are disabled.

To connect local debugging to OpenViking, configure `server` in the same `ov.conf`, or set `bot.ov_server.server_url` explicitly. See [Connect to OpenViking](#connect-to-openviking).

### Scenario C: Use the Gateway as the Unified Entry Point

Use this for long-running deployments, remote access, and multiple chat channels. `ovcli.conf.url` can point directly to VikingBot Gateway:

```text
ov chat                  → Gateway /bot/v1/chat
ov ls/find/session/...   → Gateway /api/v1/* → OpenViking Server
```

The Gateway has three OpenViking connection states:

| State | Condition | Behavior |
|-------|-----------|----------|
| **Explicit** | `bot.ov_server.server_url` is configured | Connects to the specified OpenViking service; startup fails if it is unreachable |
| **Inherited** | No explicit URL, but the same `ov.conf` contains `server` | Connects to that OpenViking service; falls back to standalone if it is unreachable |
| **Standalone** | No OpenViking service is available | Chat works; OpenViking tools are disabled and `/api/v1/*` returns 503 |

#### 1. Configure the Gateway and OpenViking

The following example connects explicitly to a remote OpenViking service:

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

If the remote OpenViking service uses `trusted` mode, set `api_key_type` to `"root"` and provide the Root Key in `api_key`.

#### 2. Start the Gateway

```bash
vikingbot gateway
```

The startup log reports the effective state, such as `openviking_explicit`, `openviking_inherited`, or `standalone_local`.

#### 3. Point the `ov` CLI to the Gateway

Use `ov config`, or edit `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://127.0.0.1:18790",
  "api_key": "<caller-openviking-user-or-admin-api-key>",
  "actor_peer_id": "cli"
}
```

Chat and other OpenViking commands now use the same entry point:

```bash
ov chat -m "Search the project resources and give me a conclusion"
ov ls viking://resources/
ov find "project release process"
```

#### 4. Configure a Gateway Token for public listeners

By default, the Gateway listens only on `127.0.0.1`. If you change the host to `0.0.0.0` or another non-localhost address, you must configure a token or the Gateway will refuse to start:

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

Add the token to `ovcli.conf` on the client:

```json
{
  "url": "https://bot.example.com",
  "api_key": "<caller-openviking-user-or-admin-api-key>",
  "gateway_token": "<strong-random-token>",
  "actor_peer_id": "cli"
}
```

The Gateway Token protects only the Gateway entry point. The OpenViking API Key represents the caller identity. They cannot replace one another, and the Gateway Token is never forwarded to OpenViking.

## Connect Chat Platforms

To use Feishu, Slack, Telegram, Discord, WhatsApp, DingTalk, QQ, Email, or MoChat, configure `bot.channels` on top of Scenario C and start the Gateway.

For example, to configure Feishu:

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

You can configure multiple instances of the same channel type. VikingBot uses `type + channel_id + chat_id` to isolate sessions and route replies. See [Channel Configuration](docs/en/concepts/05-channel.md) for credentials, event subscriptions, and permissions for each platform.

## Connect to OpenViking

VikingBot and OpenViking share `~/.openviking/ov.conf`. Connections are resolved as follows:

1. A managed Bot started by `openviking-server --with-bot` uses the current Server.
2. A normal `vikingbot gateway/chat` process first uses an explicit `bot.ov_server.server_url`.
3. Without an explicit URL, it derives the address from `ov.conf.server` in the same file.
4. Without an available address, it runs in standalone mode.

Authentication requirements:

| OpenViking `auth_mode` | Bot credential | Gateway request |
|------------------------|----------------|-----------------|
| `dev` | Local use | The Gateway must listen on localhost |
| `api_key` | `bot.ov_server.api_key` must be a User/Admin Key | The Chat caller must also provide a valid User/Admin Key; Root Keys cannot access data APIs |
| `trusted` | Explicit connections use a Root Key; inherited connections may read `server.root_api_key` | Non-local entry points must also pass the Gateway Token first |

The Gateway validates the upstream service and Bot credential at startup, then checks the current OpenViking authentication mode on every request. If the mode changes at runtime, it fails closed and asks you to fix the configuration or restart the Gateway.

VikingBot uses OpenViking to:

- read the current Peer Profile;
- recall events, entities, and preferences by type;
- retrieve Agent Experiences;
- browse, search, and read Resources;
- incrementally synchronize and commit Sessions to extract long-term memories and experiences.

See [VikingBot and OpenViking Integration](docs/en/concepts/04-openviking-integration.md) for the complete call flow. The Gateway entry points and authentication boundaries follow [RFC #3042](https://github.com/volcengine/OpenViking/discussions/3042).

## Configuration

The default configuration file is `~/.openviking/ov.conf`. Use an environment variable to select another file:

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
```

Restart `vikingbot gateway` after changing the configuration.

### Common Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `bot.agents.temperature` | `0.7` | Model sampling temperature |
| `bot.agents.thinking` | `true` | Enable reasoning/thinking when supported by the Provider |
| `bot.agents.timeout` | Inherits `vlm.timeout` | Timeout for one model request |
| `bot.agents.max_tool_iterations` | `50` | Maximum tool iterations in one turn |
| `bot.agents.memory_window` | `50` | Local history window and session commit message threshold |
| `bot.agents.subagent_enabled` | `true` | Whether to expose the `spawn` tool |
| `bot.gateway.host` | `127.0.0.1` | Gateway listen address |
| `bot.gateway.port` | `18790` | Gateway listen port |
| `bot.sandbox.backend` | `direct` | Execution backend |
| `bot.sandbox.mode` | `shared` | Workspace isolation mode |
| `bot.heartbeat.enabled` | `true` | Whether to check `HEARTBEAT.md` periodically |
| `bot.heartbeat.interval_seconds` | `600` | Heartbeat interval |
| `bot.mode` | `normal` | One of `normal`, `readonly`, or `debug` |

### OpenViking Recall Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `bot.ov_server.memory_recall_events_limit` | `10` | Event memories recalled per turn |
| `bot.ov_server.memory_recall_entities_limit` | `10` | Entity memories recalled per turn |
| `bot.ov_server.memory_recall_preferences_limit` | `3` | Preference memories recalled per turn |
| `bot.ov_server.memory_recall_max_chars` | `4000` | Character budget for injected Peer Memories |
| `bot.ov_server.exp_recall_limit` | `5` | Number of Experiences recalled |
| `bot.ov_server.exp_recall_max_chars` | `10000` | Character budget for injected Experiences |
| `bot.ov_server.exp_write_tools` | `write_file`,`edit_file` | Tools that trigger experience recall before writes |

## Workspace and Agent Customization

The Workspace is VikingBot's local working directory. It contains Agent bootstrap instructions, Skills, Heartbeat tasks, and files used by file and Shell tools. The OpenViking workspace is accessed through `openviking_*` tools for Resources, Memories, and Skills; it is not the same local directory.

### Find the Active Workspace

The Workspace root is derived from `storage.workspace`:

```text
<storage.workspace>/bot/workspace
```

When `storage.workspace` is omitted, the default is `~/.openviking/data/bot/workspace`. Check the resolved path with:

```bash
vikingbot status
```

The active directory used by the Agent also depends on `bot.sandbox.mode`:

| Mode | Active Workspace |
|------|------------------|
| `shared` (default) | `<workspace>/shared` |
| `per-session` | `<workspace>/<session-key>` |
| `per-channel` | `<workspace>/<channel-key>` |

For example, with the default configuration, edit `~/.openviking/data/bot/workspace/shared/SOUL.md`.

### Customize the Agent

When an active Workspace is first used, VikingBot copies initial files from the built-in `bot/workspace` template. The main customization points are:

| File or directory | Purpose | How it is loaded |
|-------------------|---------|------------------|
| `SOUL.md` | Personality, values, and communication style | Added to the system prompt on every turn |
| `AGENTS.md` | Global working rules and task constraints; create it when needed | Added to the system prompt on every turn |
| `IDENTITY.md` | Agent name, role, and identity background; create it when needed | Added to the system prompt on every turn |
| `TOOLS.md` | Tool selection, execution boundaries, and safety rules | Added to the system prompt on every turn |
| `skills/<name>/SKILL.md` | Workflows and supporting resources for a class of tasks | A summary is injected first; full instructions are loaded progressively |
| `HEARTBEAT.md` | Tasks checked periodically | Read only by Heartbeat |

For example, edit `SOUL.md` in the active Workspace:

```markdown
# Soul

You are the team's engineering assistant.

- Lead with the conclusion, then add only necessary detail
- Inspect the current state before changing code
- Run relevant verification after making a change
- State assumptions clearly and never invent results
```

Saved changes normally take effect on the next Agent turn without restarting the Gateway. `SOUL.md` changes prompt behavior only; it cannot bypass Channel permissions, tool visibility, or Sandbox restrictions.

> [!NOTE]
> Edit files in the active Workspace. `bot/workspace` in the repository or installed package is an initialization template and does not overwrite an existing Workspace. Never store API Keys or other secrets in bootstrap files.

For the complete loading order, file responsibilities, and customization boundaries, see [Agent Capabilities](docs/en/concepts/02-agent-capabilities.md#workspace-and-agent-customization).

## Agent Tools

### Built-in Tools

| Category | Tools |
|----------|-------|
| Files and commands | `read_file`, `write_file`, `edit_file`, `list_dir`, `exec` |
| Web | `web_search`, `web_fetch` |
| OpenViking | `openviking_list`, `openviking_search`, `openviking_grep`, `openviking_glob`, `openviking_multi_read`, `openviking_add_resource`, `openviking_memory_commit` |
| Other | `message`, `generate_image`, `cron`, `spawn` |

`readonly` mode does not register `openviking_add_resource`. When a channel sets `ov_tools_enable: false`, it does not expose OpenViking tools or inject Profiles, Memories, and Experiences.

### MCP Tools

Configure third-party MCP Servers under `bot.tools.mcp_servers`:

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

Supported transports are `stdio`, `sse`, and `streamableHttp`. Tool names use the form `mcp_<server>_<tool>`. A failed MCP connection does not block other Agent capabilities.

## Sandbox

| Backend | Description |
|---------|-------------|
| `direct` | Default; executes directly on the Bot host and is not a strong isolation boundary |
| `srt` | Supports file and network allow/deny policies |
| `opensandbox` | Connects to OpenSandbox Server |
| `aiosandbox` | Connects to an AIO Sandbox service |

Workspace modes:

- `shared`: all sessions share one workspace;
- `per-session`: every Session has an independent workspace;
- `per-channel`: sessions on the same channel instance share a workspace.

DirectBackend defaults to `restrict_to_workspace: false`. For a Gateway exposed to untrusted users, choose an isolated backend and configure channel allowlists and network/file policies.

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

The Gateway Bot API uses the `/bot/v1` prefix:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/bot/v1/chat` | Synchronous chat |
| POST | `/bot/v1/chat/stream` | SSE streaming chat |
| POST | `/bot/v1/feedback` | Submit response feedback |
| GET/POST | `/bot/v1/sessions` | List or create API Sessions |
| GET/DELETE | `/bot/v1/sessions/{id}` | Retrieve or delete a Session |

When an OpenViking upstream is configured, `/api/v1/*` is proxied to OpenViking Server.

## Operations Commands

| Command | Purpose |
|---------|---------|
| `vikingbot status` | Show model and configuration status |
| `vikingbot channels status` | Show configured channels |
| `vikingbot channels login` | Log in to the WhatsApp bridge |
| `vikingbot cron list` | List scheduled jobs |
| `vikingbot cron add` | Add a scheduled job |
| `vikingbot cron run` | Run a job manually |
| `vikingbot feedback-stats` | Aggregate response feedback and outcome metrics |

Enable Langfuse with:

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

The repository includes `deploy/docker/deploy_langfuse.sh` for local deployment.

## Security Notes

- Never commit model API Keys, OpenViking API Keys, or Gateway Tokens to the repository.
- A non-localhost Gateway requires a strong random Token and should be protected with HTTPS at the network layer.
- `X-Gateway-Token` protects only the Gateway; it does not replace an OpenViking user identity.
- `allow_from: []` allows every sender. Configure an explicit allowlist for public deployments.
- The `direct` backend executes files and shell commands with the Bot process user's permissions and is not suitable for untrusted callers.
- `openviking_connection` may come only from a trusted Server proxy or a trusted local path. Do not accept identity claims directly from a public request body.

## More Documentation

- [VikingBot Architecture](docs/en/concepts/01-architecture.md)
- [Agent Capabilities](docs/en/concepts/02-agent-capabilities.md)
- [Channels, Gateway, and Operations](docs/en/concepts/03-channels-and-gateway.md)
- [VikingBot and OpenViking Integration](docs/en/concepts/04-openviking-integration.md)
- [Channel Configuration](docs/en/concepts/05-channel.md)
