# 服务端部署

OpenViking 可以作为独立的 HTTP 服务器运行，允许多个客户端通过网络连接。

## 快速开始

```bash
# 使用初始化向导创建或刷新 ~/.openviking/ov.conf
openviking-server init

# 如果你在向导中选择 OpenAI Codex，init 会帮你处理 Codex 登录/导入

# 启动前校验本地配置、模型访问和鉴权状态
openviking-server doctor

# 配置文件在默认路径 ~/.openviking/ov.conf 时，直接启动
openviking-server

# 配置文件在其他位置时，通过 --config 指定
openviking-server --config /path/to/ov.conf

# 验证服务器是否运行
curl http://localhost:1933/health
# {"status": "ok"}
```

## 命令行选项

| 选项 | 描述 | 默认值 |
|------|------|--------|
| `--config` | 配置文件路径 | `~/.openviking/ov.conf` |
| `--host` | 绑定的主机地址 | `0.0.0.0` |
| `--port` | 绑定的端口 | `1933` |

**示例**

```bash
# 使用默认配置
openviking-server

# 使用自定义端口
openviking-server --port 8000

# 指定配置文件、主机地址和端口
openviking-server --config /path/to/ov.conf --host 127.0.0.1 --port 8000
```

## 配置

服务端从 `ov.conf` 读取所有配置。配置文件各段详情见 [配置指南](01-configuration.md)。

`ov.conf` 中的 `server` 段控制服务端行为：

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

## 部署模式

### 独立模式（嵌入存储）

服务器管理本地 RAGFS 和 VectorDB。在 `ov.conf` 中配置本地存储路径：

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

## 使用 Systemd 部署服务（推荐）

对于 Linux 系统，可以使用 Systemd 服务来管理 OpenViking，实现自动重启、开机自启等功能。首先，你应该已经成功安装并配置了 OpenViking 服务器，确保它可以正常运行，再进行服务化部署。

### 创建 Systemd 服务文件

创建 `/etc/systemd/system/openviking.service` 文件：

```ini
[Unit]
Description=OpenViking HTTP Server
After=network.target

[Service]
Type=simple
# 替换为运行 OpenViking 的用户
User=your-username
# 替换为用户组
Group=your-group
# 替换为工作目录
WorkingDirectory=/var/lib/openviking
# 以下两种启动方式二选一
ExecStart=/path/to/your/python/bin/openviking-server
Restart=always
RestartSec=5
# 配置文件路径
Environment="OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf"

[Install]
WantedBy=multi-user.target
```

### 管理服务

创建好服务文件后，使用以下命令管理 OpenViking 服务：

```bash
# 重载 systemd 配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start openviking.service

# 设置开机自启
sudo systemctl enable openviking.service

# 查看服务状态
sudo systemctl status openviking.service

# 查看服务日志
sudo journalctl -u openviking.service -f
```

## 连接客户端

### Python SDK

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key", agent_id="my-agent")
client.initialize()

results = client.find("how to use openviking")
client.close()
```

### CLI

CLI 从 `ovcli.conf` 读取连接配置。在 `~/.openviking/ovcli.conf` 中配置：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

也可通过 `OPENVIKING_CLI_CONFIG_FILE` 环境变量指定配置文件路径：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

### curl

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## 云原生部署

### Docker

OpenViking 提供预构建的 Docker 镜像，发布在 GitHub Container Registry：

```bash
# 注意 ov.conf 需要指定 storage.workspace 为 /app/data 以确保数据持久化
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking/ov.conf:/app/ov.conf \
  -v ~/.openviking/data:/app/data \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

Docker 镜像默认会同时启动：
- OpenViking HTTP 服务，端口 `1933`
- OpenViking Console，端口 `8020`
- `vikingbot` gateway

升级容器的方式
```bash
docker stop openviking
docker pull ghcr.io/volcengine/openviking:latest
docker rm -f openviking
# 然后重新 docker run ...
```

如果你希望本次容器启动时关闭 `vikingbot`，可以使用下面任一方式：

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking/ov.conf:/app/ov.conf \
  -v ~/.openviking/data:/app/data \
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
  -v ~/.openviking/ov.conf:/app/ov.conf \
  -v ~/.openviking/data:/app/data \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

也可以使用 Docker Compose，项目根目录提供了 `docker-compose.yml`：

```bash
docker compose up -d
```

启动后可以访问：
- API 服务：`http://localhost:1933`
- Console 界面：`http://localhost:8020`

如需自行构建镜像：`docker build -t openviking:latest .`

### Kubernetes + Helm

项目提供了 Helm chart，位于 `examples/k8s-helm/`：

```bash
helm install openviking ./examples/k8s-helm \
  --set openviking.config.embedding.dense.api_key="YOUR_API_KEY" \
  --set openviking.config.vlm.api_key="YOUR_API_KEY"
```

详细的云上部署指南（包括火山引擎 TOS + VikingDB + 方舟配置）请参考 [云上部署指南](../../../examples/cloud/GUIDE.md)。

## 健康检查

| 端点 | 认证 | 用途 |
|------|------|------|
| `GET /health` | 否 | 存活探针 — 立即返回 `{"status": "ok"}` |
| `GET /ready` | 否 | 就绪探针 — 检查 AGFS、VectorDB、APIKeyManager |

```bash
# 存活探针
curl http://localhost:1933/health

# 就绪探针
curl http://localhost:1933/ready
# {"status": "ready", "checks": {"agfs": "ok", "vectordb": "ok", "api_key_manager": "ok"}}
```

在 Kubernetes 中，使用 `/health` 作为存活探针，`/ready` 作为就绪探针。

## 相关文档

- [认证](04-authentication.md) - API Key 设置
- [可观测性与排障](05-observability.md) - 健康检查、追踪与排障
- [API 概览](../api/01-overview.md) - 完整 API 参考
