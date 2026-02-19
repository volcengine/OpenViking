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

client = ov.SyncHTTPClient(url="http://localhost:1933")
```

If the server has authentication enabled, pass the API key:

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
```

**Full example:**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")

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

## Recommended Cloud Deployment: Volcengine ECS

To achieve high-performance and scalable Context Memory—providing your Agents with a robust "long-term memory"—we recommend deploying on **Volcengine Elastic Compute Service (ECS)** using the **veLinux** operating system.

### 1. Instance Provisioning & Configuration

When creating an instance in the [Volcengine ECS Console](https://www.google.com/search?q=https://console.volcengine.com/ecs/region:ecs%2Bcn-beijing/dashboard%3F), we recommend the following specifications:

| Item | Recommended Setting | Notes |
| --- | --- | --- |
| **Image** | **veLinux 2.0 (CentOS Compatible)** | Check "Security Hardening" |
| **Instance Type** | **Compute Optimized c3a** (2 vCPU, 4GiB+) | Meets basic inference and retrieval needs |
| **Storage** | **Add 256 GiB Data Disk** | For vector data persistence |
| **Networking** | Configure as needed | Open only required business ports (e.g., TCP 1933) |

### 2. Environment Preparation (Mounting the Data Disk)

Once the instance is running, you must mount the data disk to the `/data` directory. Execute the following commands to automate formatting and mounting:

```bash
# 1. Create mount point
mkdir -p /data

# 2. Configure auto-mount (using UUID to prevent drive letter drifting)
cp /etc/fstab /etc/fstab.bak
DISK_UUID=$(blkid -s UUID -o value /dev/vdb)

if [ -z "$DISK_UUID" ]; then
    echo "ERROR: /dev/vdb UUID not found"
else
    # Append to fstab
    echo "UUID=${DISK_UUID} /data ext4 defaults,nofail 0 0" >> /etc/fstab
    # Verify and mount
    mount -a
    echo "Mount successful. Current disk status:"
    df -Th /data
fi

```

### 3. Installing Dependencies and OpenViking

```bash
yum install -y curl git tree

# Step 1: Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Step 2: Configure environment variables
echo 'source $HOME/.cargo/env' >> ~/.bashrc
source ~/.bashrc

# Verify installation
uv --version

# Step 3: Create a virtual environment on the data disk
cd /data
uv venv ovenv --python 3.11

# Step 4: Activate the virtual environment
source /data/ovenv/bin/activate

# Step 5: Verification
echo "Ready"
echo "Python path: $(which python)"
echo "Python version: $(python --version)"

```

* **Install OpenViking**: Install the tool within your activated virtual environment:

```bash
uv tool install openviking --upgrade

```

### 4. OpenViking Server Configuration and Startup

Configure your AI models and set up the service to run as a background daemon.

#### Prepare Configuration Files

Create the directory and configuration file before starting the service.

**Create config directory:**

```bash
mkdir -p ~/.openviking

```

**Create and edit the config file:**

```bash
vim ~/.openviking/ov.conf

```

**Configuration Template:**

```json
{
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",   // e.g., https://ark.cn-beijing.volces.com/api/v3
      "api_key"  : "<your-api-key>",   // Model service API Key
      "provider" : "<provider-type>",  // volcengine or openai
      "dimension": 1024,               // Vector dimension
      "model"    : "<model-name>",     // e.g., doubao-embedding-vision-250615
      "input"    : "multimodal"        // Use "multimodal" for doubao-embedding-vision models
    }
  },
  "vlm": {
    "api_base"   : "<api-endpoint>",   
    "api_key"    : "<your-api-key>",   
    "provider"   : "<provider-type>",  
    "max_retries": 2,
    "model"      : "<model-name>"      // e.g., doubao-seed-1-8-251228 or gpt-4-vision-preview
  }
}

```

> **Tip:** Press `i` to enter Insert mode, paste your config, then press `Esc` and type `:wq` to save and exit.

#### Start the Service in the Background

We will run the server as a background process using the virtual environment.

* **Activate environment & create logs:**

```bash
source /data/ovenv/bin/activate
mkdir -p /data/log/

```

* **Launch with nohup:**

```bash
nohup openviking-server > /data/log/openviking.log 2>&1 &

# Note: Data will be stored in ./data relative to the execution path.
# To stop the service: pkill openviking; pkill agfs

```

*Note: For production environments requiring auto-restart on failure, we recommend using `systemctl` (not covered here).*

#### Verify Service Status

* **Check Process:**
```bash
ps aux | grep openviking-server
```

* **Check Logs:**
```bash
tail -f /data/log/openviking.log # TODO: Implement log rotation
```

### 5. Client Configuration and Testing (CLI)

Ensure `openviking` is also installed locally to use the CLI. You must point the `ovcli.conf` to your server address.

* **Prepare client config:**

```bash
vim ~/.openviking/ovcli.conf
```

* **Add the following (replace with your server's IP):**

```json
{
  "url": "http://XXX.XXX.XXX.XXX:1933",
  "api_key": "your-key"
}

```

* **Monitor System Health:**

```bash
openviking observer system
```

* **Functional Testing (Upload & Search):**

```bash
# Upload a test resource
openviking add-resource https://raw.githubusercontent.com/ZaynJarvis/doc-eval/refs/heads/main/text.md

# List resources
openviking ls viking://resources

# Test retrieval
openviking find "who is Alice"
```

## Next Steps

- [Server Deployment](../guides/03-deployment.md) - Configuration, authentication, and deployment options
- [API Overview](../api/01-overview.md) - Complete API reference
- [Authentication](../guides/04-authentication.md) - Secure your server with API keys
