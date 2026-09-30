# Runtime Observability

The Observer API reports immediate status for queues, the vector database, models, locks, retrieval, and the file system.

## Observer API

The observer API provides detailed component-level monitoring.

### observer.queue

#### 1. API Implementation Overview

Get queue system status (embedding and semantic processing queues). Shows pending, in-progress, completed, and error counts for each queue.

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_queue` - HTTP route
- `openviking/service/debug_service.py:ObserverService.queue` - Core implementation
- `openviking/storage/observers/queue_observer.py` - Queue observer

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/queue
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/queue \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
print(client.observer.queue())
# Output:
# [queue] (healthy)
# Queue                 Pending  In Progress  Processed  Errors  Total
# Embedding             0        0            10         0       10
# Semantic              0        0            10         0       10
# TOTAL                 0        0            20         0       20
```

**TypeScript SDK**

```typescript
console.log(await client.queueStatus());
```

**Go SDK**

```go
status, err := client.QueueStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer queue
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "name": "queue",
    "is_healthy": true,
    "has_errors": false,
    "status": "Queue                 Pending  In Progress  Processed  Errors  Total\nEmbedding             0        0            10         0       10\nSemantic              0        0            10         0       10\nTOTAL                 0        0            20         0       20"
  }
}
```

---

### observer.vikingdb

#### 1. API Implementation Overview

Get VikingDB status. The structured representation also reports the effective vector metric and pure-dense score scale when the backend can determine them from the loaded index.

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_vikingdb` - HTTP route
- `openviking/service/debug_service.py:ObserverService.account_vikingdb` - Core implementation
- `crates/ov_cli/src/commands/observer.rs` - CLI command

#### 2. Interface and Parameters

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `format` | string | No | `table` | `table` returns the existing human-readable status; `json` returns structured runtime details. |

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/vikingdb?format=json
```

```bash
curl -X GET 'http://localhost:1933/api/v1/observer/vikingdb?format=json' \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
print(client.observer.vikingdb())
# Output:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

# Access specific attributes
print(client.observer.vikingdb().is_healthy)  # True
print(client.observer.vikingdb().status)      # Status table string
```

**TypeScript SDK**

```typescript
console.log(await client.vikingDBStatus());
```

**Go SDK**

```go
status, err := client.VikingDBStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer vikingdb
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "name": "vikingdb",
    "is_healthy": true,
    "has_errors": false,
    "status": {
      "backend": "local",
      "collection": "context",
      "index": "default",
      "dimension": 1024,
      "vector_count": 55,
      "distance_metric": "cosine",
      "pure_dense_score_scale": "cosine_affine_0_1"
    }
  }
}
```

For the local backend, `pure_dense_score_scale` is `cosine_affine_0_1`, `inner_product`, or `one_minus_squared_l2` for cosine, IP, or L2 respectively. Other backends report `backend_defined`; `distance_metric` is `null` when their loaded metadata does not expose it.

The field describes only a pure-dense vector score. Sparse fusion, time decay, reranking, and other retrieval stages can produce a different final `score` scale. Since v0.4.22, local pure-dense cosine uses `clamp((cosine_similarity + 1) / 2, 0, 1)`, including for existing indexes.

---

### observer.models

#### 1. API Implementation Overview

Get aggregated model subsystem status (VLM, embedding, rerank). Checks if each model provider is healthy and available.

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_models` - HTTP route
- `openviking/service/debug_service.py:ObserverService.models` - Core implementation
- `openviking/storage/observers/models_observer.py` - Models observer
- `crates/ov_cli/src/commands/observer.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/models
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/models \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
print(client.observer.models())
# Output:
# [models] (healthy)
# provider_model         healthy  detail
# dense_embedding        yes      ...
# rerank                 yes      ...
# vlm                    yes      ...
```

**TypeScript SDK**

```typescript
console.log(await client.modelsStatus());
```

**Go SDK**

```go
status, err := client.ModelsStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer models
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "name": "models",
    "is_healthy": true,
    "has_errors": false,
    "status": "provider_model         healthy  detail\ndense_embedding        yes      ...\nrerank                 yes      ...\nvlm                    yes      ..."
  }
}
```

---

### observer.lock

#### 1. API Implementation Overview

Get distributed lock system status.

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_lock` - HTTP route
- `openviking/service/debug_service.py:ObserverService.lock` - Core implementation
- `openviking/storage/observers/lock_observer.py` - Lock observer
- `crates/ov_cli/src/commands/observer.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/lock
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/lock \
  -H "X-API-Key: your-key"
```

The public SDKs and CLI do not currently expose a lock-specific observer method. Use the HTTP API for this component; `ov observer system` includes it in the aggregate status.

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "name": "lock",
    "is_healthy": true,
    "has_errors": false,
    "status": "..."
  }
}
```

---

### observer.retrieval

#### 1. API Implementation Overview

Get retrieval quality metrics.

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_retrieval` - HTTP route
- `openviking/service/debug_service.py:ObserverService.retrieval` - Core implementation
- `openviking/storage/observers/retrieval_observer.py` - Retrieval observer
- `crates/ov_cli/src/commands/observer.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/retrieval
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/retrieval \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov observer retrieval
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "name": "retrieval",
    "is_healthy": true,
    "has_errors": false,
    "status": "..."
  }
}
```

---

### observer.filesystem

#### 1. API Implementation Overview

Get filesystem operation metrics.

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_filesystem` - HTTP route
- `openviking/service/debug_service.py:ObserverService.filesystem` - Core implementation
- `openviking/storage/observers/filesystem_observer.py` - Filesystem observer
- `crates/ov_cli/src/commands/observer.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/filesystem
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/filesystem \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
ov observer filesystem
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "name": "filesystem",
    "is_healthy": true,
    "has_errors": false,
    "status": "..."
  }
}
```

---

### observer.system

#### 1. API Implementation Overview

Get overall system status, including all components (queue, vikingdb, models, lock, retrieval).

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_system` - HTTP route
- `openviking/service/debug_service.py:ObserverService.system` - Core implementation
- `crates/ov_cli/src/commands/observer.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/system
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/system \
  -H "X-API-Key: your-key"
```

**Python SDK**

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

**TypeScript SDK**

```typescript
console.log(await client.getStatus());
```

**Go SDK**

```go
status, err := client.GetStatus(ctx)
if err != nil {
    return err
}
fmt.Println(status["is_healthy"])
```

**CLI**

```bash
ov observer system
```

**Response Example**

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
      "models": {
        "name": "models",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "lock": {
        "name": "lock",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      },
      "retrieval": {
        "name": "retrieval",
        "is_healthy": true,
        "has_errors": false,
        "status": "..."
      }
    }
  }
}
```

---

## Related Documentation

- [Metrics](09-metrics.md) - Prometheus scraping
- [System Status](07-system.md) - health and consistency checks
