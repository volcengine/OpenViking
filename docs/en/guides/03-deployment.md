# Server Deployment

OpenViking can run as a standalone HTTP server, allowing multiple clients to connect over the network.

## Quick Start

```bash
# Start server with local storage
python -m openviking serve --path ./data

# Verify it's running
curl http://localhost:1933/health
# {"status": "ok"}
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--host` | Host to bind to | `0.0.0.0` |
| `--port` | Port to bind to | `1933` |
| `--path` | Local storage path (embedded mode) | None |
| `--vectordb-url` | Remote VectorDB URL (service mode) | None |
| `--agfs-url` | Remote AGFS URL (service mode) | None |
| `--api-key` | API key for authentication | None (auth disabled) |
| `--config` | Path to config file | `OPENVIKING_CONFIG_FILE` env var |

**Examples**

```bash
# Embedded mode with custom port
python -m openviking serve --path ./data --port 8000

# With authentication
python -m openviking serve --path ./data --api-key "your-secret-key"

# Service mode (remote storage)
python -m openviking serve \
  --vectordb-url http://vectordb:8000 \
  --agfs-url http://agfs:1833
```

## Configuration

### Config File

Server configuration is read from the JSON config file specified by `--config` or the `OPENVIKING_CONFIG_FILE` environment variable (the same file used for `OpenVikingConfig`):

```bash
python -m openviking serve --config ./ov.conf
# or
export OPENVIKING_CONFIG_FILE=./ov.conf
python -m openviking serve
```

The `server` section in the config file:

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-secret-key",
    "cors_origins": ["*"]
  },
  "storage": {
    "path": "/data/openviking"
  }
}
```

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENVIKING_HOST` | Server host | `0.0.0.0` |
| `OPENVIKING_PORT` | Server port | `1933` |
| `OPENVIKING_API_KEY` | API key | `sk-xxx` |
| `OPENVIKING_PATH` | Storage path | `./data` |
| `OPENVIKING_VECTORDB_URL` | Remote VectorDB URL | `http://vectordb:8000` |
| `OPENVIKING_AGFS_URL` | Remote AGFS URL | `http://agfs:1833` |

### Configuration Priority

From highest to lowest:

1. **Command line arguments** (`--port 8000`)
2. **Environment variables** (`OPENVIKING_PORT=8000`)
3. **Config file** (`OPENVIKING_CONFIG_FILE`)

## Deployment Modes

### Standalone (Embedded Storage)

Server manages local AGFS and VectorDB:

```bash
python -m openviking serve --path ./data
```

### Hybrid (Remote Storage)

Server connects to remote AGFS and VectorDB services:

```bash
python -m openviking serve \
  --vectordb-url http://vectordb:8000 \
  --agfs-url http://agfs:1833
```

## Connecting Clients

### Python SDK

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.find("how to use openviking")
client.close()
```

Or use environment variables:

```bash
export OPENVIKING_URL="http://localhost:1933"
export OPENVIKING_API_KEY="your-key"
```

```python
import openviking as ov

# url and api_key are read from environment variables automatically
client = ov.OpenViking()
client.initialize()
```

### curl

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## Related Documentation

- [Authentication](04-authentication.md) - API key setup
- [Monitoring](05-monitoring.md) - Health checks and observability
- [API Overview](../api/01-overview.md) - Complete API reference
