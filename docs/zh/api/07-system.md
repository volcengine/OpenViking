# 系统与监控

OpenViking 提供系统健康检查、可观测性和调试 API，用于监控各组件状态。

## API 参考

### health()

基础健康检查端点。无需认证。

**Python SDK (Embedded / HTTP)**

```python
# 检查系统是否健康
if client.observer.is_healthy():
    print("System OK")
```

**HTTP API**

```
GET /health
```

```bash
curl -X GET http://localhost:1933/health
```

**CLI**

```bash
openviking health
```

**响应**

```json
{
  "status": "ok",
  "healthy": true,
  "version": "0.1.x"
}
```

---

### ready()

部署环境使用的就绪探针。当核心子系统都准备完成时返回 `200`，否则返回 `503`。

**HTTP API**

```
GET /ready
```

```bash
curl -X GET http://localhost:1933/ready
```

**响应**

```json
{
  "status": "ready",
  "checks": {
    "agfs": "ok",
    "vectordb": "ok",
    "api_key_manager": "ok",
    "ollama": "not_configured"
  }
}
```

---

### status()

获取系统状态，包括初始化状态和用户信息。

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.system())
```

**HTTP API**

```
GET /api/v1/system/status
```

```bash
curl -X GET http://localhost:1933/api/v1/system/status \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking status
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "initialized": true,
    "user": "alice"
  },
  "time": 0.1
}
```

---

### wait_processed()

等待所有异步处理（embedding、语义生成）完成。

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| timeout | float | 否 | None | 超时时间（秒） |

**Python SDK (Embedded / HTTP)**

```python
# 添加资源
client.add_resource("./docs/")

# 等待所有处理完成
status = client.wait_processed()
print(f"Processing complete: {status}")
```

**HTTP API**

```
POST /api/v1/system/wait
```

```bash
curl -X POST http://localhost:1933/api/v1/system/wait \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "timeout": 60.0
  }'
```

**CLI**

```bash
openviking wait [--timeout 60]
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "Embedding": {
      "processed": 10,
      "requeue_count": 0,
      "error_count": 0,
      "errors": []
    },
    "Semantic": {
      "processed": 10,
      "requeue_count": 0,
      "error_count": 0,
      "errors": []
    }
  },
  "time": 0.1
}
```

---

## Observer API

Observer API 提供详细的组件级监控。

### observer.queue

获取队列系统状态（embedding 和语义处理队列）。

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.queue)
# Output:
# [queue] (healthy)
# Queue                 Pending  In Progress  Processed  Errors  Total
# Embedding             0        0            10         0       10
# Semantic              0        0            10         0       10
# TOTAL                 0        0            20         0       20
```

**HTTP API**

```
GET /api/v1/observer/queue
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/queue \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking observer queue
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "name": "queue",
    "is_healthy": true,
    "has_errors": false,
    "status": "Queue  Pending  In Progress  Processed  Errors  Total\nEmbedding  0  0  10  0  10\nSemantic  0  0  10  0  10\nTOTAL  0  0  20  0  20"
  },
  "time": 0.1
}
```

---

### observer.vikingdb

获取 VikingDB 状态（集合、索引、向量数量）。

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.vikingdb())
# Output:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

# 访问特定属性
print(client.observer.vikingdb().is_healthy)  # True
print(client.observer.vikingdb().status)      # Status table string
```

**HTTP API**

```
GET /api/v1/observer/vikingdb
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/vikingdb \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking observer vikingdb
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "name": "vikingdb",
    "is_healthy": true,
    "has_errors": false,
    "status": "Collection  Index Count  Vector Count  Status\ncontext  1  55  OK\nTOTAL  1  55"
  },
  "time": 0.1
}
```

---

### observer.models

获取模型子系统的聚合状态（VLM、embedding、rerank）。

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.models)
# Output:
# [models] (healthy)
# provider_model         healthy  detail
# dense_embedding        yes      ...
# rerank                 yes      ...
# vlm                    yes      ...
```

**HTTP API**

```
GET /api/v1/observer/models
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/models \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking observer models
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "name": "models",
    "is_healthy": true,
    "has_errors": false,
    "status": "provider_model  healthy  detail\ndense_embedding  yes  ...\nrerank  yes  ...\nvlm  yes  ..."
  },
  "time": 0.1
}
```

---

另外还有两个仅 HTTP 暴露的 Observer 端点：

- `GET /api/v1/observer/lock`
- `GET /api/v1/observer/retrieval`

### observer.system

获取整体系统状态，包括所有组件。

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.system())
# Output:
# [queue] (healthy)
# ...
#
# [vikingdb] (healthy)
# ...
#
# [models] (healthy)
# ...
#
# [system] (healthy)
```

**HTTP API**

```
GET /api/v1/observer/system
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/system \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking observer system
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "is_healthy": true,
    "errors": [],
    "components": {
      "queue": {
        "name": "queue",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "vikingdb": {
        "name": "vikingdb",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "vlm": {
        "name": "vlm",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      }
    }
  },
  "time": 0.1
}
```

---

### is_healthy()

快速检查整个系统的健康状态。

**Python SDK (Embedded / HTTP)**

```python
if client.observer.is_healthy():
    print("System OK")
else:
    print(client.observer.system())
```

**HTTP API**

```
GET /api/v1/debug/health
```

```bash
curl -X GET http://localhost:1933/api/v1/debug/health \
  -H "X-API-Key: your-key"
```

**响应**

```json
{
  "status": "ok",
  "result": {
    "healthy": true
  },
  "time": 0.1
}
```

---

## 数据结构

### ComponentStatus

单个组件的状态信息。

| 字段 | 类型 | 说明 |
|------|------|------|
| name | str | 组件名称 |
| is_healthy | bool | 组件是否健康 |
| has_errors | bool | 组件是否存在错误 |
| status | str | 状态表格字符串 |

### SystemStatus

整体系统状态，包括所有组件。

| 字段 | 类型 | 说明 |
|------|------|------|
| is_healthy | bool | 整个系统是否健康 |
| components | Dict[str, ComponentStatus] | 各组件的状态 |
| errors | List[str] | 错误信息列表 |

---

## 相关文档

- [Resources](02-resources.md) - 资源管理
- [Retrieval](06-retrieval.md) - 搜索与检索
- [Sessions](05-sessions.md) - 会话管理
