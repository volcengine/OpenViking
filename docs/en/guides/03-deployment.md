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

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -e .
EXPOSE 1933
CMD ["python", "-m", "openviking", "serve", "--config", "/etc/openviking/ov.conf"]
```

```bash
docker build -t openviking .
docker run -d -p 1933:1933 \
  -v /path/to/ov.conf:/etc/openviking/ov.conf:ro \
  -v /data/openviking:/data/openviking \
  openviking
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: openviking
spec:
  replicas: 1
  selector:
    matchLabels:
      app: openviking
  template:
    metadata:
      labels:
        app: openviking
    spec:
      containers:
        - name: openviking
          image: openviking:latest
          ports:
            - containerPort: 1933
          volumeMounts:
            - name: config
              mountPath: /etc/openviking
              readOnly: true
            - name: data
              mountPath: /data/openviking
          livenessProbe:
            httpGet:
              path: /health
              port: 1933
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /ready
              port: 1933
            initialDelaySeconds: 10
            periodSeconds: 15
      volumes:
        - name: config
          configMap:
            name: openviking-config
        - name: data
          persistentVolumeClaim:
            claimName: openviking-data
---
apiVersion: v1
kind: Service
metadata:
  name: openviking
spec:
  selector:
    app: openviking
  ports:
    - port: 1933
      targetPort: 1933
```

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
