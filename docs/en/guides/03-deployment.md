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
    "root_api_key": "your-secret-root-key",
    "cors_origins": ["*"]
  },
  "storage": {
    "workspace": "./data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local" }
  }
}
```

## Deployment Modes

### Standalone (Embedded Storage)

Server manages local AGFS and VectorDB. Configure the storage path in `ov.conf`:

```json
{
  "storage": {
    "workspace": "./data",
    "agfs": { "backend": "local" },
    "vectordb": { "backend": "local" }
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

## Deploying with Systemd (Recommended)

For Linux systems, you can use Systemd to manage OpenViking as a service, enabling automatic restart and startup on boot. Firstly, you should tried to install and configure openviking on your own.

### Create Systemd Service File

Create `/etc/systemd/system/openviking.service` file:

```ini
[Unit]
Description=OpenViking HTTP Server
After=network.target

[Service]
Type=simple
# Replace with the user running OpenViking
User=your-username
# Replace with the user group
Group=your-group
# Replace with your working directory
WorkingDirectory=/home/your-username/openviking_workspace
# Choose one of the following start methods
ExecStart=/path/to/your/python/bin/openviking-server
Restart=always
RestartSec=5
# Path to config file
Environment="OPENVIKING_CONFIG_FILE=/home/your-username/.openviking/ov.conf"

[Install]
WantedBy=multi-user.target
```

### Manage the Service

After creating the service file, use the following commands to manage the OpenViking service:

```bash
# Reload systemd configuration
sudo systemctl daemon-reload

# Start the service
sudo systemctl start openviking.service

# Enable service on boot
sudo systemctl enable openviking.service

# Check service status
sudo systemctl status openviking.service

# View service logs
sudo journalctl -u openviking.service -f
```

## Connecting Clients

### Python SDK

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key", agent_id="my-agent")
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

## Cloud Deployment

### Docker

OpenViking provides pre-built Docker images published to GitHub Container Registry:

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -v ~/.openviking/ov.conf:/app/ov.conf \
  -v /var/lib/openviking/data:/app/data \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:main
```

You can also use Docker Compose with the `docker-compose.yml` provided in the project root:

```bash
docker compose up -d
```

To build the image yourself: `docker build -t openviking:latest .`

### Kubernetes + Helm

The project provides a Helm chart located at `examples/k8s-helm/`:

```bash
helm install openviking ./examples/k8s-helm \
  --set openviking.config.embedding.dense.api_key="YOUR_API_KEY" \
  --set openviking.config.vlm.api_key="YOUR_API_KEY"
```

For a detailed cloud deployment guide (including Volcengine TOS + VikingDB + Ark configuration), see the [Cloud Deployment Guide](../../../examples/cloud/GUIDE.md).

## Health Checks

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | No | Liveness probe — returns `{"status": "ok"}` immediately |
| `GET /ready` | No | Readiness probe — checks AGFS, VectorDB, APIKeyManager |

```bash
# Liveness
curl http://localhost:1933/health

# Readiness
curl http://localhost:1933/ready
# {"status": "ready", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok"}}
```

Use `/health` for Kubernetes liveness probes and `/ready` for readiness probes.

## Related Documentation

- [Authentication](04-authentication.md) - API key setup
- [Monitoring](05-monitoring.md) - Health checks and observability
- [API Overview](../api/01-overview.md) - Complete API reference
