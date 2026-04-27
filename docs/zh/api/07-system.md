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

### reindex()

对已经存储在 OpenViking 中的现有内容，重新构建语义产物和/或向量索引。这是一个运维维护接口，适用于 embedding 模型更换、VLM 更换、向量库重刷、版本升级后修复历史索引等场景。

这个接口面向已有的 `viking://...` 内容，不负责导入新文件。常规导入请使用 [Resources](02-resources.md)。

**认证**

- HTTP 端点：在开启认证时要求 root/admin 权限
- Python embedded 模式：使用当前 service context
- Python HTTP client / CLI：使用当前认证身份发起请求

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| uri | str | 是 | - | 要重新索引的 Viking URI |
| mode | str | 否 | `vectors_only` | 重建模式：`vectors_only` 或 `semantic_and_vectors` |
| wait | bool | 否 | `true` | 是否等待任务完成 |

**支持的 URI 范围**

- `viking://`
- `viking://user`
- `viking://user/<user_id>`
- `viking://agent`
- `viking://agent/<agent_id>`
- `viking://resources/...`
- `viking://user/<user_id>/memories/...`
- `viking://agent/<agent_id>/memories/...`
- `viking://agent/<agent_id>/skills/...`

`reindex()` 不支持 `viking://session/...`。

**模式说明**

- `vectors_only`：基于当前仍可恢复的源数据重建向量库记录，不会重写 `.abstract.md` 和 `.overview.md`
- `semantic_and_vectors`：先重新生成语义产物，再基于新的语义结果重建向量

对于 `resource` 和 `skill`，`semantic_and_vectors` 会刷新目录/文件语义产物，包括 `.abstract.md` 和 `.overview.md`。对于 `memory`，它会重建当前已持久化 memory 子树的语义和向量，但不会回放历史记忆抽取顺序。

**Python SDK (Embedded / HTTP)**

```python
result = client.reindex(
    uri="viking://resources",
    mode="vectors_only",
    wait=True,
)
print(result)
```

```python
result = client.reindex(
    uri="viking://agent/default/skills",
    mode="semantic_and_vectors",
    wait=False,
)
print(result["status"])
```

**HTTP API**

```
POST /api/v1/content/reindex
```

```bash
curl -X POST http://localhost:1933/api/v1/content/reindex \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources",
    "mode": "vectors_only",
    "wait": true
  }'
```

**CLI**

```bash
openviking reindex viking://resources --mode vectors_only
```

```bash
openviking reindex viking://agent/default/skills --mode semantic_and_vectors --wait false
```

**同步响应（`wait=true`）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "status": "completed",
    "stats": {
      "visited": 120,
      "rebuilt": 118,
      "skipped": 2,
      "failed": 0
    }
  },
  "time": 0.1
}
```

**异步响应（`wait=false`）**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "status": "accepted",
    "task_id": "task_xxx"
  },
  "time": 0.1
}
```

**行为说明**

- Reindex 是非破坏式的，采用重建/覆盖写入，不需要先 drop 向量集合。
- 对 `viking://` 发起 reindex 时，会向下分发到支持的顶层命名空间，并显式排除 `session`。
- 命名空间级 reindex，例如 `viking://user` 或 `viking://agent/default`，会继续传播到其支持的子内容类型。
- 如果只是 embedding 模型或向量索引需要刷新，应使用 `vectors_only`。
- 如果语义产物本身也需要重建，再做重向量化，应使用 `semantic_and_vectors`。

**当前限制**

- Reindex 会使用当前系统中“尽可能可恢复”的输入进行重建，不保证所有场景都能逐字节回放历史当时的 embedding 输入。
- Memory 的 semantic reindex 基于当前已持久化的 memory 树，不会重建最初按时间顺序执行的记忆抽取流水线。

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
