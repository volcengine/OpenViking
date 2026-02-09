# 服务端部署

OpenViking 可以作为独立的 HTTP 服务器运行，允许多个客户端通过网络连接。

## 快速开始

```bash
# 使用本地存储启动服务器
python -m openviking serve --path ./data

# 验证服务器是否运行
curl http://localhost:1933/health
# {"status": "ok"}
```

## 命令行选项

| 选项 | 描述 | 默认值 |
|------|------|--------|
| `--host` | 绑定的主机地址 | `0.0.0.0` |
| `--port` | 绑定的端口 | `1933` |
| `--path` | 本地存储路径（嵌入模式） | 无 |
| `--vectordb-url` | 远程 VectorDB URL（服务模式） | 无 |
| `--agfs-url` | 远程 AGFS URL（服务模式） | 无 |
| `--api-key` | 用于认证的 API Key | 无（禁用认证） |
| `--config` | 配置文件路径 | `OPENVIKING_CONFIG_FILE` 环境变量 |

**示例**

```bash
# 嵌入模式，使用自定义端口
python -m openviking serve --path ./data --port 8000

# 启用认证
python -m openviking serve --path ./data --api-key "your-secret-key"

# 服务模式（远程存储）
python -m openviking serve \
  --vectordb-url http://vectordb:8000 \
  --agfs-url http://agfs:1833
```

## 配置

### 配置文件

服务端配置从 `--config` 或 `OPENVIKING_CONFIG_FILE` 环境变量指定的 JSON 配置文件中读取（与 `OpenVikingConfig` 共用同一个文件）：

```bash
python -m openviking serve --config ./ov.conf
# 或
export OPENVIKING_CONFIG_FILE=./ov.conf
python -m openviking serve
```

配置文件中的 `server` 段：

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

### 环境变量

| 变量 | 描述 | 示例 |
|------|------|------|
| `OPENVIKING_HOST` | 服务器主机地址 | `0.0.0.0` |
| `OPENVIKING_PORT` | 服务器端口 | `1933` |
| `OPENVIKING_API_KEY` | API Key | `sk-xxx` |
| `OPENVIKING_PATH` | 存储路径 | `./data` |
| `OPENVIKING_VECTORDB_URL` | 远程 VectorDB URL | `http://vectordb:8000` |
| `OPENVIKING_AGFS_URL` | 远程 AGFS URL | `http://agfs:1833` |

### 配置优先级

从高到低：

1. **命令行参数** (`--port 8000`)
2. **环境变量** (`OPENVIKING_PORT=8000`)
3. **配置文件** (`OPENVIKING_CONFIG_FILE`)

## 部署模式

### 独立模式（嵌入存储）

服务器管理本地 AGFS 和 VectorDB：

```bash
python -m openviking serve --path ./data
```

### 混合模式（远程存储）

服务器连接到远程 AGFS 和 VectorDB 服务：

```bash
python -m openviking serve \
  --vectordb-url http://vectordb:8000 \
  --agfs-url http://agfs:1833
```

## 连接客户端

### Python SDK

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933", api_key="your-key")
client.initialize()

results = client.find("how to use openviking")
client.close()
```

或使用环境变量：

```bash
export OPENVIKING_URL="http://localhost:1933"
export OPENVIKING_API_KEY="your-key"
```

```python
import openviking as ov

# url 和 api_key 自动从环境变量读取
client = ov.OpenViking()
client.initialize()
```

### curl

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## 相关文档

- [认证](04-authentication.md) - API Key 设置
- [监控](05-monitoring.md) - 健康检查与可观测性
- [API 概览](../api/01-overview.md) - 完整 API 参考
