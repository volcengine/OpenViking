# 服务端部署

OpenViking 可以作为独立的 HTTP 服务器运行，允许多个客户端通过网络连接。

## 快速开始

```bash
# 配置文件在默认路径 ~/.openviking/ov.conf 时，直接启动
python -m openviking serve

# 配置文件在其他位置时，通过 --config 指定
python -m openviking serve --config /path/to/ov.conf

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
python -m openviking serve

# 使用自定义端口
python -m openviking serve --port 8000

# 指定配置文件、主机地址和端口
python -m openviking serve --config /path/to/ov.conf --host 127.0.0.1 --port 8000
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

服务器管理本地 AGFS 和 VectorDB。在 `ov.conf` 中配置本地存储路径：

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

### 混合模式（远程存储）

服务器连接到远程 AGFS 和 VectorDB 服务。在 `ov.conf` 中配置远程地址：

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
WorkingDirectory=/home/your-username/openviking_workspace
# 以下两种启动方式二选一
ExecStart=/path/to/your/python/bin/openviking-server
Restart=always
RestartSec=5
# 配置文件路径
Environment="OPENVIKING_CONFIG_FILE=/home/your-username/.openviking/ov.conf"

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

## 云上部署

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
- [监控](05-monitoring.md) - 健康检查与可观测性
- [API 概览](../api/01-overview.md) - 完整 API 参考
