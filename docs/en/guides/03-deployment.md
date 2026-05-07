# Server Deployment

OpenViking can run as a standalone HTTP server, allowing multiple clients to connect over the network.

## Quick Start

```bash
# Create or refresh ~/.openviking/ov.conf with the setup wizard
openviking-server init

# If you select OpenAI Codex in the wizard, init can import/login Codex for you

# Validate local config, model access, and auth before starting
openviking-server doctor

# Start server (reads ~/.openviking/ov.conf by default)
openviking-server

# Or specify a custom config path
openviking-server --config /path/to/ov.conf

# Verify it's running
curl http://localhost:1933/health
# {"status": "ok"}
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--config` | Path to ov.conf file | `~/.openviking/ov.conf` |
| `--host` | Host to bind to | `127.0.0.1` |
| `--port` | Port to bind to | `1933` |

**Examples**

```bash
# With default config
openviking-server

# With custom port
openviking-server --port 8000

# With custom config, host, and port
openviking-server --config /path/to/ov.conf --host 127.0.0.1 --port 8000
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

Server manages local RAGFS and VectorDB. Configure the storage path in `ov.conf`:

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
openviking-server
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
# Replace with your working directory
WorkingDirectory=/var/lib/openviking
# Choose one of the following start methods
ExecStart=/usr/bin/openviking-server
Restart=always
RestartSec=5
# Path to config file
Environment="OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf"

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

OpenViking provides pre-built Docker images published to GitHub Container Registry. All persistent state — `ov.conf`, `ovcli.conf`, and the workspace data — lives under `/app/.openviking` inside the container, so a single mount is enough:

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

By default, the Docker image starts:
- OpenViking HTTP service on port `1933` (bound to `0.0.0.0`)
- OpenViking Console on port `8020`
- `vikingbot` gateway

Since the server binds to `0.0.0.0` inside the container (required for Docker port-mapping to work), you **must** set `root_api_key` in your `ov.conf`:

```json
{
  "server": {
    "root_api_key": "your-secret-root-key"
  }
}
```

The server will refuse to start without it. You can override the bind address via the `OPENVIKING_SERVER_HOST` environment variable if needed.

Upgrade the container:
```bash
docker stop openviking
docker pull ghcr.io/volcengine/openviking:latest
docker rm -f openviking
# Then re-run docker run ...
```

If you want to disable `vikingbot` for a specific container run, you can use either of the following:

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest \
  --without-bot
```

```bash
docker run -d \
  --name openviking \
  -e OPENVIKING_WITH_BOT=0 \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

#### When `docker -v` is not available

Some managed platforms (Railway, Fly.io, Heroku-style PaaS) don't let you bind-mount a host path. If `ov.conf` doesn't exist when the container starts, the entrypoint will not crash — it prints a fix-it message and waits for the file to appear. You have two ways to provide it:

**Option A: pass the full config through `OPENVIKING_CONF_CONTENT`.** The entrypoint writes the env value to `OPENVIKING_CONFIG_FILE` (defaults to `/app/.openviking/ov.conf`) before starting the server:

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -p 8020:8020 \
  -e OPENVIKING_CONF_CONTENT="$(cat ~/.openviking/ov.conf)" \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

**Option B: configure interactively after the container is up.** While the container is sleeping (waiting for `ov.conf`), `docker exec` in and run the setup wizard — it honors `OPENVIKING_CONFIG_FILE` and writes to the path the server is watching:

```bash
docker exec -it openviking openviking-server init
```

As soon as `ov.conf` appears, the entrypoint resumes and starts the server + console automatically.

You can also use Docker Compose, which provides a `docker-compose.yml` in the project root:

```bash
docker compose up -d
```

After startup, you can access:
- API service: `http://localhost:1933`
- Console UI: `http://localhost:8020`

To build the image yourself, pass an explicit OpenViking version:
`docker build --build-arg OPENVIKING_VERSION=0.3.12 -t openviking:latest .`

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
- [Observability & Diagnostics](05-observability.md) - Health checks, tracing, and debugging
- [API Overview](../api/01-overview.md) - Complete API reference
