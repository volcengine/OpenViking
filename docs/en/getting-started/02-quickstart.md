# Quick Start

Start a local server, import a resource, and run your first search.

## Prerequisites

Before using OpenViking, ensure your environment meets the following requirements:

- **Python Version**: 3.10 or higher
- **Operating System**: Linux, macOS, Windows
- **Network Connection**: Stable network connection required (for downloading dependencies and accessing model services)

## Installation & Startup

Choose one server installation method: a Python package or Docker. The Python SDK connects to either server over HTTP.

### Option 1: Install the server and CLI

Choose your preferred Python package manager to install OpenViking:

::: code-group

```bash [uv (recommended)]
uv tool install openviking --upgrade
```

```bash [pip]
python -m pip install openviking --upgrade
```

```bash [pipx]
# Install
pipx install openviking

# Update
pipx upgrade openviking
```

:::

After installation, use `ov` as the client command (`openviking` is an alias) and `openviking-server` as the server command.

### Option 2: Start via Docker (As an independent service)

If you prefer to run OpenViking as a standalone service, Docker is recommended.

1. **Prepare Configuration Directory**
   Create the OpenViking directory on your host and prepare the `ov.conf` configuration file (see the "Configuration" section below for details). All persistent state — config and workspace data — lives under this single directory:
   ```bash
   mkdir -p ~/.openviking
   ```

   Write a complete JSON configuration to `~/.openviking/ov.conf` using the manual template below before starting the container. An empty file is not a valid configuration.

2. **Start with Docker Compose**
   Create a `docker-compose.yml` file:
   ```yaml
   services:
     openviking:
       # Prefer ghcr.io. If it is hard to reach, use openviking-cn-beijing.cr.volces.com/volcengine/openviking:latest
       image: ghcr.io/volcengine/openviking:latest
       container_name: openviking
       ports:
         - "127.0.0.1:1933:1933"
       volumes:
         - ~/.openviking:/app/.openviking
       restart: unless-stopped
   ```
   Then run the following command in the same directory:
   ```bash
   docker compose up -d
   ```

   By default, the container starts the OpenViking API server on `1933` (which also serves the Web Studio UI at `/studio`) and the bundled `vikingbot` gateway. If you need to disable `vikingbot`, add either `command: ["--without-bot"]` or `environment: ["OPENVIKING_WITH_BOT=0"]`.

   On platforms that don't allow bind mounts, set `OPENVIKING_CONF_CONTENT` to the full config JSON to bootstrap on first start, or `docker exec` in and run `openviking-server init` after the container is up. See [Deployment Guide](../guides/03-deployment.md#when-docker--v-is-not-available) for details.

The container entrypoint binds to `0.0.0.0` inside the container. The [port mapping](https://docs.docker.com/engine/network/port-publishing/) above makes it accessible at `http://localhost:1933` on the host, including macOS. For access from other machines, see [Authentication](../guides/04-authentication.md) and [Public Access](../guides/12-public-access.md).

## Model Preparation

OpenViking requires the following model capabilities:
- **VLM Model**: For image and content understanding
- **Embedding Model**: For vectorization and semantic retrieval

OpenViking supports multiple model services:
- **Volcengine (Doubao Models)**: Recommended, cost-effective with good performance, free quota for new users. For purchase and activation, see: [Volcengine Purchase Guide](../guides/02-volcengine-purchase-guide.md)
- **OpenAI Models**: Supports vision-capable models and embedding models through the OpenAI API
- **OpenAI Codex**: Supports Codex as the VLM provider through ChatGPT/Codex OAuth
- **Other Custom Model Services**: Supports model services compatible with OpenAI API format

## Configuration

### Configuration File Template

For a Python package installation, use the setup wizard:

```bash
openviking-server init
openviking-server doctor
```

For Docker, or if you prefer manual setup, create `~/.openviking/ov.conf`:

```json
{
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",
      "api_key"  : "<your-api-key>",
      "provider" : "<provider-type>",
      "dimension": 1024,
      "model"    : "<model-name>"
    }
  },
  "vlm": {
    "api_base" : "<api-endpoint>",
    "api_key"  : "<your-api-key>",
    "provider" : "<provider-type>",
    "model"    : "<model-name>"
  }
}
```

`provider`, `model`, `api_base`, and `api_key` depend on the VLM service you choose. Some providers may use local OAuth state instead of a manually copied API key.

For complete examples for each model provider, see [Configuration Guide - Examples](../guides/01-configuration.md#configuration-examples).

For first-time setup, `openviking-server init` is the recommended path. It helps you pick a provider and writes a working config template for the selected setup.

### Environment Variables

When the config file is at the default path `~/.openviking/ov.conf`, no additional setup is needed — OpenViking loads it automatically.

If the config file is at a different location, specify it via environment variable:

```bash
export OPENVIKING_CONFIG_FILE=/path/to/your/ov.conf
```

## Start the Local Server

After configuring a Python package installation, start the server. If Docker is already running, skip this step:

```bash
openviking-server
```

Keep the server running and open another terminal for the Python SDK example below.
To use a custom config path, start it with `openviking-server --config /path/to/ov.conf`.

The default local setup does not require an API key. For an authenticated server, set
`OPENVIKING_API_KEY` before running the example.

## Run Your First Example

### Create Python Script

Create `example.py`:

```python
import time

from openviking_sdk import SyncHTTPClient

# Connect to the local OpenViking Server
client = SyncHTTPClient(url="http://localhost:1933")

try:
    # Check the connection
    client.initialize()

    # Add resource (supports URL, file, or directory)
    # Local directory scans respect .gitignore by default.
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
    )

    task_id = add_result["task_id"]
    print(f"Import task: {task_id}")
    while True:
        task = client.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} is no longer available")
        if task["status"] == "completed":
            break
        if task["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Import task {task_id}: {task['status']} ({task.get('error')})")
        time.sleep(2)
    root_uri = task["result"]["root_uri"]

    # Explore the resource tree structure
    ls_result = client.ls(uri=root_uri)
    print(f"Directory structure:\n{ls_result}\n")

    # Use glob to find markdown files
    glob_result = client.glob(pattern="**/*.md", uri=root_uri)
    if glob_result['matches']:
        content = client.read(uri=glob_result["matches"][0])
        print(f"Content preview: {content[:200]}...\n")

    # Get abstract and overview of the resource
    abstract = client.abstract(uri=root_uri)
    overview = client.overview(uri=root_uri)
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    # Perform semantic search
    results = client.find(
        query="what is openviking",
        target_uri=root_uri,
    )
    print("Search results:")
    for result in results.get("resources", []):
        print(f"  {result['uri']} (score: {result.get('score', 0.0):.4f})")

finally:
    client.close()
```

### Run the Script

Install the SDK in the environment that runs the script. [`uv tool install`](https://docs.astral.sh/uv/guides/tools/#installing-tools) and `pipx install` isolate server dependencies; they do not install the SDK into your current Python environment. Docker also does not install a client on the host.

::: code-group

```bash [uv]
uv run --with openviking-sdk python example.py
```

```bash [pip (in your virtual environment)]
python -m pip install --upgrade openviking-sdk
python example.py
```

:::

### Expected Output

```
Import task: ...

Directory structure:
...

Content preview: ...

Abstract:
...

Overview:
...

Search results:
  viking://resources/... (score: 0.8523)
  ...
```

Congratulations! You have successfully run OpenViking.

## Server Mode

Want to run OpenViking as a shared service? See [Quick Start: Server Mode](03-quickstart-server.md).

## Next Steps

- [Configuration Guide](../guides/01-configuration.md) - Detailed configuration options
- [API Overview](../api/01-overview.md) - API reference
- [Resource Management](../api/02-resources.md) - Resource management API
