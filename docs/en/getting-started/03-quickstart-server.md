# Quick Start: Server Mode

Run OpenViking as a standalone HTTP server and connect from any client.

## Prerequisites

- OpenViking installed (`uv tool install openviking --upgrade`)
- Model configuration ready (see [Quick Start](02-quickstart.md) for setup)

> Python 3.14 note for Volcengine Ark users:
> if your `ov.conf` uses `provider = "volcengine"` / Ark runtime, prefer Python 3.13 or lower for `openviking-server` right now.
> `volcengine-python-sdk[ark]` still emits a Pydantic V1 compatibility warning on Python 3.14, so the server works but startup/version commands may print noisy warnings until the upstream SDK removes that compatibility layer.

## Start the Server

Make sure you have a config file at `~/.openviking/ov.conf` with your model and storage settings (see [Configuration](../guides/01-configuration.md)).

For first-time setup, run `openviking-server init` first.

Before startup, validate local setup:

```bash
openviking-server doctor
```

`openviking-server doctor` validates that the configured local setup is usable, including provider-specific auth when required.

```bash
# Config file at default path ~/.openviking/ov.conf — just start
openviking-server

# Config file at a different location — specify with --config
openviking-server --config /path/to/ov.conf

# Use a different port
openviking-server --port 8000
```

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:1933
```

## Verify

```bash
curl http://localhost:1933/health
# {"status": "ok"}
```

`openviking-server doctor` checks local configuration, model access, and auth readiness. `curl /health` only confirms that the server process is already running.

Web Studio is also served at `http://localhost:1933/studio` (bundled with pip/pipx installs since v0.3.21 — no Docker required).

## Connect with Python SDK

Install the standalone SDK in your client Python environment:

```bash
python -m pip install --upgrade openviking-sdk
```

Server installations made with `uv tool` or pipx use a separate environment. You can also run a saved script with `uv run --with openviking-sdk python example.py`.

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933")
```

### Authentication

When authentication is enabled, pass an API key. OpenViking uses a two-tier key system:

**Regular data access: use a `user_key` (recommended)**

For most scenarios, use a `user_key` — it directly works with tenant-scoped APIs like `add_resource`, `find`, and `ls`:

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
)
```

> `user_key` is created via the Admin API (see [Authentication](../guides/04-authentication.md)). The server can automatically resolve the tenant from the key.

**Administrative operations: use a `root_key`**

`root_key` is for management operations (creating accounts, system status, etc.).
Tenant-scoped data APIs such as `add_resource`, `find`, and sessions need a key
that is bound to an account/user, such as a user key or admin key:

```python
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-or-admin-key>",
)
```

> Using `root_key` for tenant-scoped data APIs in `api_key` mode returns
> `PERMISSION_DENIED`. Use a user/admin key for data access, or trusted mode for
> upstream identity assertion.

See [Authentication](../guides/04-authentication.md) for details (trusted mode, CLI config, etc.).

**Full example:**

For an authenticated server, set `OPENVIKING_API_KEY` to a user/admin key before running this script. The SDK reads this environment variable automatically.

```python
import time

from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933")

try:
    client.initialize()

    # Add a resource
    result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
    )

    task_id = result["task_id"]
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

    # Search
    results = client.find(
        query="what is openviking",
        target_uri=root_uri,
    )
    for result in results.get("resources", []):
        print(f"  {result['uri']} (score: {result.get('score', 0.0):.4f})")

finally:
    client.close()
```

## Connect with CLI

For the default local server, omit `api_key`. For an authenticated server, replace `your-key` below with a user/admin key.

Create a CLI config file `~/.openviking/ovcli.conf` that points to your server:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

Once configured, use the CLI to manage resources and query your Agent's memory:

```bash
# Check system health
openviking observer system

# Add a resource to memory
openviking add-resource https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md

# List all synchronized resources
openviking ls viking://resources

# Query
openviking find "what is openviking"

```

If the config file is at a different location, specify it via environment variable:

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## Connect with curl

The examples below use the default local server. With authentication enabled, add `-H "X-API-Key: $OPENVIKING_API_KEY"` to each request, using a user/admin key. Resource imports are asynchronous: poll `GET /api/v1/tasks/{task_id}` with the returned `task_id` until it completes before searching.

Use direct `path` for remote URLs. For local files, upload first with `POST /api/v1/resources/temp_upload`, then call the target API with the returned `temp_file_id`. For local directories in raw HTTP mode, zip the directory first and upload the `.zip` file.

```bash
# Add a resource
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{"path": "https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"}'

# List resources
curl "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/"

# Semantic search
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -d '{"query": "what is openviking"}'
```

## Deploy on a cloud server

For Volcengine ECS, create an instance in the [official ECS console](https://console.volcengine.com/ecs/). Follow the [Deployment Guide](../guides/03-deployment.md) for persistent storage, Docker, and service management.

The local server binds to `127.0.0.1` by default. Before allowing remote clients, configure [authentication](../guides/04-authentication.md), a reachable bind address, and the required network access rules. See [Public Access](../guides/12-public-access.md) for TLS and reverse-proxy configuration. Use a tenant-bound user/admin key for data access.

## Next Steps

- [Server Deployment](../guides/03-deployment.md) - Configuration, authentication, and deployment options
- [API Overview](../api/01-overview.md) - Complete API reference
- [Authentication](../guides/04-authentication.md) - Secure your server with API keys
