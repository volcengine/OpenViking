# OpenViking Server-Client 示例

演示 OpenViking 的 Server/Client 架构：通过 HTTP Server 提供服务，Client 通过 HTTP API 访问。

## 架构

```
┌──────────────┐     HTTP/REST     ┌──────────────────┐
│   Client     │ ◄──────────────► │  OpenViking Server │
│  (HTTP mode) │   JSON API        │  (FastAPI + ASGI) │
└──────────────┘                   └──────────────────┘
```

## Quick Start

```bash
# 0. 安装依赖
uv sync

# 1. 启动 Server
uv run server.py

# 2. 另一个终端，运行 Client 示例
uv run client_sync.py                    # 同步客户端
uv run client_async.py                   # 异步客户端
```

## 文件说明

```
server.py           # Server 启动示例（含 API Key 认证）
client_sync.py      # 同步客户端示例（SyncOpenViking HTTP mode）
client_async.py     # 异步客户端示例（AsyncOpenViking HTTP mode）
ov.conf.example     # 配置文件模板
pyproject.toml      # 项目依赖
```

## Server 启动方式

### 方式一：CLI 命令

```bash
# 基本启动
python -m openviking serve --path ./data --port 1933

# 带 API Key 认证
python -m openviking serve --path ./data --port 1933 --api-key your-secret-key

# 指定配置文件
python -m openviking serve --path ./data --config ./ov.conf
```

### 方式二：Python 脚本

```python
from openviking.server.bootstrap import main
main()
```

### 方式三：环境变量

```bash
export OPENVIKING_CONFIG_FILE=./ov.conf
export OPENVIKING_PATH=./data
export OPENVIKING_PORT=1933
export OPENVIKING_API_KEY=your-secret-key
python -m openviking serve
```

## Client 使用方式

### 同步客户端

```python
import openviking as ov

client = ov.OpenViking(url="http://localhost:1933", api_key="your-key")
client.initialize()

client.add_resource(path="./document.md")
client.wait_processed()

results = client.find("search query")
client.close()
```

### 异步客户端

```python
import openviking as ov

client = ov.AsyncOpenViking(url="http://localhost:1933", api_key="your-key")
await client.initialize()

await client.add_resource(path="./document.md")
await client.wait_processed()

results = await client.find("search query")
await client.close()
```

## API 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（免认证） |
| GET | `/api/v1/system/status` | 系统状态 |
| POST | `/api/v1/resources` | 添加资源 |
| POST | `/api/v1/resources/skills` | 添加技能 |
| POST | `/api/v1/resources/wait` | 等待处理完成 |
| GET | `/api/v1/fs/ls` | 列出目录 |
| GET | `/api/v1/fs/tree` | 目录树 |
| GET | `/api/v1/fs/stat` | 资源状态 |
| POST | `/api/v1/fs/mkdir` | 创建目录 |
| DELETE | `/api/v1/fs/rm` | 删除资源 |
| POST | `/api/v1/fs/mv` | 移动资源 |
| GET | `/api/v1/content/read` | 读取内容 |
| GET | `/api/v1/content/abstract` | 获取摘要 |
| GET | `/api/v1/content/overview` | 获取概览 |
| POST | `/api/v1/search/find` | 语义搜索 |
| POST | `/api/v1/search/search` | 带 Session 搜索 |
| POST | `/api/v1/search/grep` | 内容搜索 |
| POST | `/api/v1/search/glob` | 文件匹配 |
| GET | `/api/v1/relations` | 获取关联 |
| POST | `/api/v1/relations/link` | 创建关联 |
| DELETE | `/api/v1/relations/unlink` | 删除关联 |
| POST | `/api/v1/sessions` | 创建 Session |
| GET | `/api/v1/sessions` | 列出 Sessions |
| GET | `/api/v1/sessions/{id}` | 获取 Session |
| DELETE | `/api/v1/sessions/{id}` | 删除 Session |
| POST | `/api/v1/sessions/{id}/messages` | 添加消息 |
| POST | `/api/v1/pack/export` | 导出 ovpack |
| POST | `/api/v1/pack/import` | 导入 ovpack |
| GET | `/api/v1/observer/system` | 系统监控 |
| GET | `/api/v1/observer/queue` | 队列状态 |
| GET | `/api/v1/observer/vikingdb` | VikingDB 状态 |
| GET | `/api/v1/observer/vlm` | VLM 状态 |
| GET | `/api/v1/debug/health` | 组件健康检查 |

## 认证

Server 支持可选的 API Key 认证。启动时通过 `--api-key` 或配置文件设置。

Client 请求时通过以下任一方式传递：

```
X-API-Key: your-secret-key
Authorization: Bearer your-secret-key
```

`/health` 端点始终免认证。
