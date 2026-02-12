# Server Deployment

OpenViking can run as a standalone HTTP server, allowing multiple clients to connect over the network.

## Quick Start

```bash
# Start server (reads ~/.openviking/ov.conf by default)
python -m openviking serve

# Or specify a custom config path
python -m openviking serve --config /path/to/ov.conf

# Verify it's running
curl http://localhost:1933/health
# {"status": "ok"}
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--config` | Path to ov.conf file | `~/.openviking/ov.conf` |
| `--host` | Host to bind to | `0.0.0.0` |
| `--port` | Port to bind to | `1933` |

**Examples**

```bash
# With default config
python -m openviking serve

# With custom port
python -m openviking serve --port 8000

# With custom config, host, and port
python -m openviking serve --config /path/to/ov.conf --host 127.0.0.1 --port 8000
```

## Configuration

The server reads all configuration from `ov.conf`. See [Configuration Guide](./01-configuration.md) for full details on config file format.

The `server` section in `ov.conf` controls server behavior:

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-secret-key",
    "cors_origins": ["*"]
  },
  "storage": {
    "agfs": { "backend": "local", "path": "/data/openviking" },
    "vectordb": { "backend": "local", "path": "/data/openviking" }
  }
}
```

## Deployment Modes

### Standalone (Embedded Storage)

Server manages local AGFS and VectorDB. Configure the storage path in `ov.conf`:

```json
{
  "storage": {
    "agfs": { "backend": "local", "path": "/data/openviking" },
    "vectordb": { "backend": "local", "path": "/data/openviking" }
  }
}
```

```bash
python -m openviking serve
```

### Hybrid (Remote Storage)

Server connects to remote AGFS and VectorDB services. Configure remote URLs in `ov.conf`:

```json
{
  "storage": {
    "agfs": { "backend": "remote", "url": "http://agfs:1833" },
    "vectordb": { "backend": "remote", "url": "http://vectordb:8000" }
  }
}
```

```bash
python -m openviking serve
```

## Connecting Clients

### Python SDK

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.find("how to use openviking")
client.close()
```

### CLI

The CLI reads connection settings from `ovcli.conf`. Create `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

Or set the config path via environment variable:

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

Then use the CLI:

```bash
python -m openviking ls viking://resources/
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
