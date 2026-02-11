# Quick Start: Server Mode

Run OpenViking as a standalone HTTP server and connect from any client.

## Prerequisites

- OpenViking installed (`pip install openviking`)
- Model configuration ready (see [Quick Start](02-quickstart.md) for setup)

## Start the Server

Make sure you have a config file at `~/.openviking/ov.conf` with your model and storage settings (see [Configuration](../guides/01-configuration.md)).

```bash
# Config file at default path ~/.openviking/ov.conf — just start
python -m openviking serve

# Config file at a different location — specify with --config
python -m openviking serve --config /path/to/ov.conf

# Override host/port
python -m openviking serve --port 8000
```

You should see:

```
INFO:     Uvicorn running on http://0.0.0.0:1933
```

## Verify

```bash
curl http://localhost:1933/health
# {"status": "ok"}
```

## Connect with Python SDK

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933")
```

If the server has authentication enabled, pass the API key:

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933", api_key="your-key")
```

**Full example:**

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933")

try:
    client.initialize()

    # Add a resource
    result = client.add_resource(
        "https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = result["root_uri"]

    # Wait for processing
    client.wait_processed()

    # Search
    results = client.find("what is openviking", target_uri=root_uri)
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

finally:
    client.close()
```

## Connect with CLI

Create a CLI config file `~/.openviking/ovcli.conf` that points to your server:

```json
{
  "url": "http://localhost:1933"
}
```

Then use CLI commands to interact with the server:

```bash
python -m openviking health
python -m openviking find "what is openviking"
```

If the config file is at a different location, specify it via environment variable:

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## Connect with curl

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

## Next Steps

- [Server Deployment](../guides/03-deployment.md) - Configuration, authentication, and deployment options
- [API Overview](../api/01-overview.md) - Complete API reference
- [Authentication](../guides/04-authentication.md) - Secure your server with API keys
