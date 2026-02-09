# Quick Start: Server Mode

Run OpenViking as a standalone HTTP server and connect from any client.

## Prerequisites

- OpenViking installed (`pip install openviking`)
- Model configuration ready (see [Quick Start](02-quickstart.md) for setup)

## Start the Server

```bash
python -m openviking serve --path ./data
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

Or use environment variables:

```bash
export OPENVIKING_URL="http://localhost:1933"
export OPENVIKING_API_KEY="your-key"  # if authentication is enabled
```

```python
import openviking as ov

# url and api_key are read from environment variables automatically
client = ov.OpenViking()
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
