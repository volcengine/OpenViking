# Server Deployment

Before provisioning a self-managed environment, review the [deployment checklist](19-deployment-checklist.md). If you have a VikingDB / OpenViking private deployment package, use [Enterprise Deployment](20-private-deployment.md) and [operations](21-private-operations.md).

OpenViking runs as an HTTP service. Choose who operates it before installing a server:

| Service | What you need |
| --- | --- |
| [Volcano Engine managed OpenViking](https://www.volcengine.com/product/openviking-service) | Obtain an API key in the [console](https://console.volcengine.com/vikingdb/openviking/region:openviking+cn-beijing), then connect with the independent CLI. No local server or model configuration is needed. |
| An existing team or remote deployment | Obtain the service URL and a user/admin key from its administrator. |
| Self-hosted OpenViking | Install and configure the server using the instructions below. |

Managed and existing-service users can start with the [CLI quickstart](../getting-started/02-quickstart.md). For managed-service availability, plans, and limits, see the [official service documentation](https://docs.volcengine.com/docs/84313/2374478).

The rest of this guide covers self-hosting.

For sizing, measure memory and disk use with a representative corpus and expected concurrency. Budget storage for source files, indexes, and snapshots; use [observability](../guides/05-observability.md) to check headroom before expanding the workload.

## Quick Start

Install the server first using the [self-hosted quickstart](../getting-started/02-quickstart.md). For Volcengine Ark on Python 3.14, the upstream Python SDK may emit Pydantic V1 compatibility warnings; Python 3.13 avoids that warning in this setup.

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

# In another terminal, configure ov for this server using the quickstart, then verify
ov health
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
# Replace with the user and group that run OpenViking
User=your-username
Group=your-group
# Replace with your working directory
WorkingDirectory=/var/lib/openviking
# Use the absolute executable path from your installation
ExecStart=/path/to/your/python/bin/openviking-server
Restart=always
RestartSec=5
# Path to config file
Environment="OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf"

[Install]
WantedBy=multi-user.target
```

Before starting, replace every placeholder. Run `command -v openviking-server` as the service user to find the executable, create the working directory, and grant that user access to the config, workspace, and any local encryption key. The config shown here is `/etc/openviking/ov.conf`; a config generated under your own home directory is not automatically used by this unit. Omitting `User` from a system service runs it as root.

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

```bash
python -m pip install --upgrade openviking-sdk
```

```python
import openviking_sdk as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.find(query="how to use openviking")
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
ov ls viking://resources/
```

### curl

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## Cloud Deployment

### Docker

OpenViking provides pre-built Docker images published to GitHub Container Registry. The runtime working directory is `/app/.openviking`, so the default `storage.workspace` (`./data`) resolves to `/app/.openviking/data`. The default workspace, `ov.conf`, and `ovcli.conf` therefore share one persistent mount. If you configure an absolute workspace outside this directory, mount that path separately:

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

> We recommend the `ghcr.io` image. If `ghcr.io` is hard to reach, use `openviking-cn-beijing.cr.volces.com/volcengine/openviking:latest` instead. The same applies to the commands below.

By default, the Docker image starts:
- OpenViking HTTP service on port `1933` (bound to `0.0.0.0`), also serving the Web Studio UI at `/studio`
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

**Upgrading from an older image:** images that started in `/app` resolved `./data` to `/app/data`, outside the default mount. Before removing the old container, stop it and back up its configured workspace (for example, `docker cp openviking:/app/data ./openviking-data-backup`). Restore that backup into the mounted host workspace, normally `~/.openviking/data`, before starting the replacement. Do not overwrite an existing workspace without checking which data to retain. Absolute workspace paths are unchanged; other relative paths now resolve from `/app/.openviking`.

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
  -e OPENVIKING_CONF_CONTENT="$(cat ~/.openviking/ov.conf)" \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

**Option B: configure interactively after the container is up.** While the container is sleeping (waiting for `ov.conf`), `docker exec` in and run the setup wizard — it honors `OPENVIKING_CONFIG_FILE` and writes to the path the server is watching:

```bash
docker exec -it openviking openviking-server init
```

As soon as `ov.conf` appears, the entrypoint resumes and starts the server automatically.

You can also use Docker Compose, which provides a `docker-compose.yml` in the project root:

```bash
docker compose up -d
```

After startup, you can access:
- API service: `http://localhost:1933`
- Web Studio: `http://localhost:1933/studio` (same origin as the API)
- Legacy entry point: `http://localhost:1934` (Caddy reverse proxy to 1933, kept for existing deployments)

### Deploy on Railway

Click the badge below to deploy OpenViking on Railway:

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/openviking)

#### Provisioned Resources & Defaults

- **Container Image**: Uses official image `ghcr.io/volcengine/openviking:latest`, listening on port 1933 with an automatically assigned HTTPS domain.
- **Persistent Storage**: Mounts a persistent volume at `/app/.openviking`. The configured `storage.workspace` (`/app/.openviking/data`) is stored on this volume, ensuring accounts, resources, and vector data persist across redeployments.
- **Default Configuration**: Preconfigured with OpenAI defaults. `OPENAI_API_KEY` is the only required input during deployment. The admin key `OPENVIKING_ROOT_API_KEY` is generated automatically and viewable in the **Variables** tab.

#### Quick Start (Web Studio)

Initial bootstrap can be completed entirely within the browser:

1. **Set Admin Key**: Copy `OPENVIKING_ROOT_API_KEY` from Railway Variables, open `https://<your-domain>/studio/settings`, and save it.
2. **Create User**: Navigate to `https://<your-domain>/studio/users` to create your initial account and user. The user API key will be displayed immediately.
3. **Start Using**: Use this user API key for subsequent access via Web Studio, the `ov` CLI, and SDKs.

#### Configuration Management

- **Initial Configuration**: The template pre-populates `OPENVIKING_CONF_CONTENT` with a complete configuration referencing `${OPENAI_API_KEY}`. This variable is used only on the first startup, when `ov.conf` does not yet exist.
- **Later Changes**: After the first startup, edit the volume-backed `ov.conf` directly using `railway ssh` or `railway service files upload --overwrite`. Alternatively, delete `ov.conf` and redeploy to regenerate it from the current `OPENVIKING_CONF_CONTENT` value.

#### Pricing & Resource Sizing

- **Recommended Plan**: For continuous hosting, the **Hobby** plan ($5/mo, which includes $5 of usage credits) is recommended. At ~0.5 GB resident memory, typical monthly cost is around $5–$7.
- **Free/Trial Limitations**: Railway Free plan ($1/mo credit) is insufficient for continuous service. Trial credits ($5 one-time) are suitable for short-term evaluation; note that volumes are purged 30 days after trial expiration.

> **Security Note**: The service is publicly accessible by default. Keep `OPENVIKING_ROOT_API_KEY` confidential and consult the [public access guide](12-public-access.md) before production rollout.

### Multi-instance notes

With an embedded vector backend (`local` or `cuvs`), OpenViking holds an exclusive OS file lock on `storage.workspace` by default. The `.openviking.lock` file remains on disk; its presence does not mean a server is running. The OS releases the lock when the server closes it or the process terminates. Do not manually delete a running server's lock file.

Remote vector backends (`http`, `volcengine`, `vikingdb`) do not acquire this workspace lock, including when files are stored on a shared NAS. They do not require `storage.skip_process_lock=true`. Placing an embedded vector database on NAS does not make it safe to share between processes.

For embedded vector backends, when upgrading from a version that uses `.openviking.pid`, stop all older servers using the workspace before starting the new version. The new version does not use leftover PIDs to determine ownership; the two locking protocols cannot be mixed.

For multi-instance deployments, prefer these settings:

- Set `server.temp_upload.default_mode` to `"shared"` so uploaded temporary files can be consumed by a different replica.
- Use a remote vector backend for shared storage. The existing `storage.skip_process_lock` option only disables the embedded-backend startup guard; it does not add multi-process support to embedded vector storage.
- For QueueFS, prefer an explicit per-instance local SQLite path via `storage.agfs.queuefs.db_path`. If usage audit is enabled, prefer an explicit per-instance local SQLite path via `server.observability.usage_audit.sqlite_path` instead of mixing these files into a shared workspace volume.

Example:

```json
{
  "server": {
    "temp_upload": {
      "default_mode": "shared"
    }
  },
  "storage": {
    "vectordb": {
      "backend": "http",
      "url": "http://vector-db:5000"
    }
  }
}
```

This example uses a remote HTTP vector service. Replace its URL with your deployment's vector-service address, or configure a `volcengine` or `vikingdb` backend.

Example with explicit local SQLite paths for QueueFS and usage audit:

```json
{
  "server": {
    "temp_upload": {
      "default_mode": "shared"
    },
    "observability": {
      "usage_audit": {
        "sqlite_path": "/var/lib/openviking-local/usage_audit.sqlite3"
      }
    }
  },
  "storage": {
    "vectordb": {
      "backend": "http",
      "url": "http://vector-db:5000"
    },
    "agfs": {
      "queuefs": {
        "db_path": "/var/lib/openviking-local/queue.db"
      }
    }
  }
}
```

This variant is useful when multiple instances share the same `workspace`, but QueueFS and usage audit SQLite files still need per-instance local paths.

For public HTTPS access, see the [Public Access Guide](12-public-access.md).

To build the image yourself, pass an explicit OpenViking version:
`docker build --build-arg OPENVIKING_VERSION=0.3.12 -t openviking:latest .`

### Kubernetes + Helm

This chart deploys the open-source service and is separate from the private delivery ovadmin / Operator workflow. See the [Helm README](https://github.com/volcengine/OpenViking/blob/main/deploy/helm/README.md) for chart parameters.

The project provides a Helm chart located at `deploy/helm/openviking/`:

```bash
helm install openviking ./deploy/helm/openviking \
  --set-string config.server.root_api_key="YOUR_ROOT_API_KEY" \
  --set-string config.embedding.dense.api_key="YOUR_API_KEY" \
  --set-string config.vlm.api_key="YOUR_API_KEY"
```

For a detailed cloud deployment guide (including Volcengine TOS + VikingDB + Ark configuration), see the [Cloud Deployment Guide](https://github.com/volcengine/OpenViking/blob/main/examples/cloud/GUIDE.md).

## Health Checks

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | No | Liveness probe — returns `{"status": "ok"}` immediately |
| `GET /ready` | No | Readiness probe — checks AGFS, VectorDB, APIKeyManager, Embedding, Ollama |

```bash
# Liveness
curl http://localhost:1933/health

# Readiness
curl http://localhost:1933/ready
# {"status": "ready", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok", "embedding": "ok", "ollama": "ok"}}
```

Use `/health` for Kubernetes liveness probes and `/ready` for readiness probes.

## Related Documentation

- [Public Access & Reverse Proxy](12-public-access.md) - HTTPS, Caddy, nginx
- [Authentication](04-authentication.md) - API key setup
- [OAuth Guide](11-oauth.md) - OAuth 2.1 for MCP clients
- [Observability & Diagnostics](05-observability.md) - Health checks, tracing, and debugging
- [API Overview](../api/01-overview.md) - Complete API reference
