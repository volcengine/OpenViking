# VikingBot Installation and Configuration

VikingBot is the multi-channel AI Agent built into OpenViking. It can start together with OpenViking, run independently for local debugging, or operate as a long-running Gateway connected to chat platforms.

This guide covers installation and configuration for the three main usage scenarios. For complete documentation about Agent tools, chat channels, and architecture, see the [VikingBot documentation](https://github.com/volcengine/OpenViking/blob/main/bot/README.md).

## Installation

Python 3.11 or later is recommended for VikingBot.

### Install from PyPI

```bash
pip install "openviking[bot]"
```

Verify the installation:

```bash
vikingbot --version
```

### Install from Source

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

## Configuration File

VikingBot and OpenViking share `~/.openviking/ov.conf`. If the file is stored elsewhere, set:

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
```

Restart VikingBot or OpenViking Server after changing the configuration.

## Choose a Usage Scenario

| Scenario | Best for | Start command | OpenViking |
|----------|----------|---------------|------------|
| **A. Start OpenViking and the Bot together** | Full experience with resources, memory, and the Agent | `openviking-server --with-bot` | Uses the Server being started |
| **B. Debug the Agent locally** | Trying the Bot or developing Tools and Skills | `vikingbot chat` | Optional |
| **C. Use the Gateway as a unified entry point** | Long-running service, remote access, or chat platforms | `vikingbot gateway` | Connects to an existing Server or runs standalone |

The three scenarios are different runtime entry points and can share the same `ov.conf`.

## Scenario A: Start OpenViking and the Bot Together

This is the recommended option for a complete local experience. OpenViking Server and VikingBot Gateway start together:

```text
ov chat → OpenViking Server → VikingBot Gateway → Agent
```

### 1. Configure OpenViking

Run the initialization wizard, then validate the model and storage configuration:

```bash
openviking-server init
openviking-server doctor
```

See the [OpenViking Configuration Guide](01-configuration.md) for details. VikingBot inherits the root-level `vlm` as its Agent model by default, so you normally do not need to configure `bot.agents` again.

### 2. Start Both Services

```bash
openviking-server --with-bot
```

In this mode, the Bot always connects to the OpenViking Server being started and does not use `bot.ov_server.server_url` to connect to another service.

### 3. Configure and Use the `ov` CLI

```bash
ov config
ov chat
ov chat -m "Remember that I prefer concise answers"
ov find "my response preference"
```

The URL configured by `ov config` should point to the current OpenViking Server, which defaults to `http://127.0.0.1:1933`. If authentication is enabled, also configure the current caller's User/Admin API Key.

## Scenario B: Debug the Agent Locally

Use this scenario to try VikingBot quickly or develop Agents, Tools, and Skills. `vikingbot chat` runs the Agent directly in the current process, without starting the Gateway first.

### 1. Configure the Agent Model

If `ov.conf` already contains a root-level `vlm`, VikingBot inherits it. You can also configure a separate Agent model:

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

### 2. Start a Chat

```bash
# Send one message
vikingbot chat -m "Summarize the structure of the current project"

# Start an interactive multi-turn conversation
vikingbot chat

# Use a specific session
vikingbot chat --session my-session
```

When no OpenViking Server is available, VikingBot runs in standalone mode. Local files, Shell, Web, and Skills remain available, but OpenViking resource retrieval and long-term memory are disabled.

## Scenario C: Use the Gateway as a Unified Entry Point

Use this scenario for a long-running service, remote access, or chat platforms such as Feishu, Slack, and Telegram. The Gateway exposes the Bot HTTP API and can proxy OpenViking APIs, allowing the `ov` CLI to use one entry point.

### 1. Configure the Gateway and OpenViking

The following example connects the Gateway to an existing OpenViking Server:

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

The Gateway has three OpenViking connection states:

- When `bot.ov_server.server_url` is configured, it connects to that Server and refuses to start if the connection fails.
- When that URL is omitted but the same `ov.conf` contains `server`, the Gateway inherits that Server address and falls back to standalone mode if it is unavailable.
- When no Server is available, Chat still works, but OpenViking tools and API proxying are disabled.

### 2. Start the Gateway

```bash
vikingbot gateway
```

### 3. Point the `ov` CLI to the Gateway

Edit `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://127.0.0.1:18790",
  "api_key": "<caller-openviking-user-or-admin-api-key>",
  "actor_peer_id": "cli"
}
```

Chat and OpenViking commands can then use the same Gateway:

```bash
ov chat -m "Retrieve the project information and give me a conclusion"
ov ls viking://resources/
ov find "project release process"
```

The Gateway listens on `127.0.0.1` by default. If you change it to `0.0.0.0` or another non-localhost address, configure `bot.gateway.token` and set the matching `gateway_token` on the client.

For credentials and permissions required by each chat platform, see [VikingBot Channel Configuration](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/05-channel.md).

## More Documentation

- [Complete VikingBot documentation](https://github.com/volcengine/OpenViking/blob/main/bot/README.md)
- [VikingBot Architecture](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/01-architecture.md)
- [Agent Capabilities](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/02-agent-capabilities.md)
- [Channels, Gateway, and Operations](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/03-channels-and-gateway.md)
- [VikingBot and OpenViking Integration](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/04-openviking-integration.md)
- [Chat Channel Configuration](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/05-channel.md)
