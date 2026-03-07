# Advanced Installation & Configuration

Detailed configuration and deployment options for OpenViking.

For the quick start guide, see **[INSTALL.md](./INSTALL.md)**.

---

## Table of Contents

- [Full Configuration Reference](#full-configuration-reference)
- [Alternative Installation Methods](#alternative-installation-methods)
- [Building from Source](#building-from-source)
- [Cloud Deployment](#cloud-deployment)
- [Docker/Container Setup](#dockercontainer-setup)
- [Multiple Model Providers](#multiple-model-providers)
- [Authentication and Security](#authentication-and-security)
- [Advanced Troubleshooting](#advanced-troubleshooting)

---

## Full Configuration Reference

### Complete ov.conf Template

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": null,
    "cors_origins": ["*"]
  },
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "name": "context",
      "backend": "local",
      "project": "default",
      "volcengine": {
        "region": "cn-beijing",
        "ak": null,
        "sk": null
      }
    },
    "agfs": {
      "port": 1833,
      "log_level": "warn",
      "backend": "local",
      "timeout": 10,
      "retry_times": 3,
      "s3": {
        "bucket": null,
        "region": null,
        "access_key": null,
        "secret_key": null,
        "endpoint": null,
        "prefix": "",
        "use_ssl": true
      }
    }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-vision-250615",
      "api_key": "your-api-key",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "model": "doubao-seed-1-8-251228",
    "api_key": "your-api-key",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.0,
    "max_retries": 2,
    "thinking": false
  },
  "rerank": {
    "ak": null,
    "sk": null,
    "host": "api-vikingdb.vikingdb.cn-beijing.volces.com",
    "model_name": "doubao-seed-rerank",
    "model_version": "251028",
    "threshold": 0.1
  },
  "auto_generate_l0": true,
  "auto_generate_l1": true,
  "default_search_mode": "thinking",
  "default_search_limit": 3,
  "enable_memory_decay": true,
  "memory_decay_check_interval": 3600,
  "log": {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "output": "stdout",
    "rotation": true,
    "rotation_days": 3,
    "rotation_interval": "midnight"
  },
  "parsers": {
    "pdf": {
      "strategy": "auto",
      "max_content_length": 100000,
      "max_section_size": 4000,
      "section_size_flexibility": 0.3,
      "mineru_endpoint": "https://mineru.example.com/api/v1",
      "mineru_api_key": "{your-mineru-api-key}",
      "mineru_timeout": 300.0
    },
    "code": {
      "enable_ast": true,
      "extract_functions": true,
      "extract_classes": true,
      "extract_imports": true,
      "include_comments": true,
      "max_line_length": 1000,
      "max_token_limit": 50000,
      "truncation_strategy": "head",
      "warn_on_truncation": true
    },
    "image": {
      "enable_ocr": false,
      "enable_vlm": true,
      "ocr_lang": "eng",
      "vlm_model": "gpt-4-vision",
      "max_dimension": 2048
    },
    "audio": {
      "enable_transcription": true,
      "transcription_model": "whisper-large-v3",
      "language": null,
      "extract_metadata": true
    },
    "video": {
      "extract_frames": true,
      "frame_interval": 10.0,
      "enable_transcription": true,
      "enable_vlm_description": false,
      "max_duration": 3600.0
    },
    "markdown": {
      "preserve_links": true,
      "extract_frontmatter": true,
      "include_metadata": true,
      "max_heading_depth": 3
    },
    "html": {
      "extract_text_only": false,
      "preserve_structure": true,
      "clean_html": true,
      "extract_metadata": true
    },
    "text": {
      "detect_language": true,
      "split_by_paragraphs": true,
      "max_paragraph_length": 1000,
      "preserve_line_breaks": false
    }
  }
}
```

### Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `server.host` | string | `"0.0.0.0"` | Server bind address |
| `server.port` | int | `1933` | Server port |
| `server.api_key` | string | `null` | API key for authentication |
| `storage.workspace` | string | `"./data"` | Main data directory |
| `storage.agfs.port` | int | `1833` | AGFS server port |
| `storage.vectordb.backend` | string | `"local"` | Vector DB backend: local, volcengine |
| `storage.agfs.backend` | string | `"local"` | AGFS backend: local, s3 |
| `embedding.dense.provider` | string | `"volcengine"` | Embedding provider |
| `embedding.dense.dimension` | int | `1024` | Vector dimension |
| `vlm.provider` | string | `"volcengine"` | VLM provider |
| `vlm.temperature` | float | `0.0` | Model temperature |
| `auto_generate_l0` | bool | `true` | Auto-generate abstracts |
| `auto_generate_l1` | bool | `true` | Auto-generate overviews |
| `log.level` | string | `"INFO"` | Log level: DEBUG, INFO, WARN, ERROR |

### Environment Variables

```bash
# Set config file location
export OPENVIKING_CONFIG_FILE=~/.openviking/ov.conf

# Or set config directory (looks for ov.conf in that dir)
export OPENVIKING_CONFIG_DIR=~/.openviking

# Set CLI config location
export OPENVIKING_CLI_CONFIG_FILE=~/.openviking/ovcli.conf
```

---

## Alternative Installation Methods

### Using pip

While uv is the recommended method, you can also use pip:

```bash
pip install openviking
```

### Development Installation

Install from source for development:

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
uv pip install -e ".[dev,test]"
```

### Virtual Environment (manual)

If not using uv's automatic virtualenv:

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate  # Windows

pip install openviking
```

---

## Building from Source

> ⚠️ **Note:** Building from source is only needed for development or if you need to modify the code. For regular use, use `uv tool install` or `pip install` to get pre-built wheels.

### Prerequisites

- Python 3.10+
- Rust (for ov CLI)
- Go 1.21+ (for AGFS server)
- CMake 3.15+
- GCC/G++ or Clang

### Build Steps

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking

# Install Python package (builds AGFS and C++ extensions)
pip install .

# Or with uv
uv pip install .

# Install ov CLI
cargo install --path crates/ov_cli
```

### AGFS Build Details

The AGFS (Agent Filesystem) server is written in Go and is automatically built during installation:

```bash
# AGFS source location
third_party/agfs/agfs-server/

# To build manually:
cd third_party/agfs/agfs-server
go build -o build/agfs-server cmd/server/main.go
```

### C++ Extensions

OpenViking includes C++ extensions for high-performance vector operations:

```bash
# CMake is used automatically during pip install
# To build manually:
mkdir build && cd build
cmake ..
make -j$(nproc)
```

---

## Cloud Deployment

### Volcengine ECS (Recommended)

See detailed guide: `docs/en/getting-started/03-quickstart-server.md`

Quick setup:

```bash
# 1. Create ECS instance with veLinux 2.0
# 2. Mount data disk to /data
mkdir -p /data

# 3. Install uv and openviking
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install openviking

# 4. Configure
mkdir -p ~/.openviking
# Create ov.conf with your API keys

# 5. Start with nohup
export OPENVIKING_CONFIG_DIR=~/.openviking
nohup openviking-server > /data/openviking.log 2>&1 &
```

### AWS/GCP/Azure

General cloud VM setup:

```bash
# Update system
sudo apt update && sudo apt upgrade -y  # Ubuntu/Debian
# or
sudo yum update -y  # CentOS/RHEL

# Install dependencies
sudo apt install -y curl git  # Ubuntu/Debian

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# Install openviking
uv tool install openviking

# Configure firewall (example for UFW)
sudo ufw allow 1933/tcp

# Setup systemd service (see below)
```

### Systemd Service

Create `/etc/systemd/system/openviking.service`:

```ini
[Unit]
Description=OpenViking Server
After=network.target

[Service]
Type=simple
User=openviking
Environment=OPENVIKING_CONFIG_DIR=/home/openviking/.openviking
ExecStart=/home/openviking/.cargo/bin/openviking-server
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable openviking
sudo systemctl start openviking
sudo systemctl status openviking
```

---

## Docker/Container Setup

### Docker Run

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -v $(pwd)/data:/data \
  -v $(pwd)/ov.conf:/app/ov.conf \
  -e OPENVIKING_CONFIG_FILE=/app/ov.conf \
  volcengine/openviking:latest
```

### Docker Compose

```yaml
version: '3.8'

services:
  openviking:
    image: volcengine/openviking:latest
    ports:
      - "1933:1933"
    volumes:
      - ./data:/data
      - ./ov.conf:/app/ov.conf
    environment:
      - OPENVIKING_CONFIG_FILE=/app/ov.conf
    restart: unless-stopped
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
        image: volcengine/openviking:latest
        ports:
        - containerPort: 1933
        env:
        - name: OPENVIKING_CONFIG_FILE
          value: /app/ov.conf
        volumeMounts:
        - name: config
          mountPath: /app/ov.conf
          subPath: ov.conf
        - name: data
          mountPath: /data
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

---

## Multiple Model Providers

### OpenAI Configuration

```json
{
  "embedding": {
    "dense": {
      "provider": "openai",
      "model": "text-embedding-3-large",
      "api_key": "sk-...",
      "api_base": "https://api.openai.com/v1",
      "dimension": 3072
    }
  },
  "vlm": {
    "provider": "openai",
    "model": "gpt-4-vision-preview",
    "api_key": "sk-...",
    "api_base": "https://api.openai.com/v1"
  }
}
```

### Anthropic Configuration

```json
{
  "vlm": {
    "provider": "anthropic",
    "model": "claude-3-opus-20240229",
    "api_key": "sk-ant-...",
    "api_base": "https://api.anthropic.com"
  }
}
```

### Local vLLM

```bash
# Start vLLM server
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

```json
{
  "vlm": {
    "provider": "vllm",
    "model": "meta-llama/Llama-3.1-8B-Instruct",
    "api_key": "dummy",
    "api_base": "http://localhost:8000/v1"
  }
}
```

---

## Authentication and Security

### Enable API Key Authentication

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1933,
    "api_key": "your-secure-api-key",
    "cors_origins": ["https://yourdomain.com"]
  }
}
```

### CLI with Authentication

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secure-api-key"
}
```

### HTTPS/SSL

Use a reverse proxy like Nginx or Caddy:

```nginx
server {
    listen 443 ssl;
    server_name openviking.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:1933;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Advanced Troubleshooting

### Debug Logging

```json
{
  "log": {
    "level": "DEBUG",
    "output": "file",
    "file_path": "/data/openviking.log"
  }
}
```

### Vector DB Issues

**Reset local vector DB:**

```bash
# Stop server
pkill openviking-server

# Remove vector DB data
rm -rf ~/.openviking/data/vectordb

# Restart server
openviking-server
```

### Memory Issues

For large deployments, adjust:

```json
{
  "parsers": {
    "pdf": {
      "max_content_length": 50000,
      "max_section_size": 2000
    },
    "code": {
      "max_token_limit": 25000
    }
  }
}
```

### Port Conflicts

If port 1933 is taken, use alternative ports like 11933:

```json
{
  "server": {
    "port": 11933
  },
  "storage": {
    "agfs": {
      "port": 11944
    }
  }
}
```

### Connection Issues from Remote Clients

1. Check firewall rules
2. Verify server binds to `0.0.0.0` (not `127.0.0.1`)
3. Test with curl from remote machine:
   ```bash
   curl http://server-ip:1933/health
   ```

---

## Performance Tuning

### For High Throughput

```json
{
  "server": {
    "workers": 4
  },
  "storage": {
    "vectordb": {
      "backend": "volcengine"
    }
  }
}
```

### For Low Memory

```json
{
  "embedding": {
    "dense": {
      "dimension": 512
    }
  },
  "auto_generate_l1": false
}
```
