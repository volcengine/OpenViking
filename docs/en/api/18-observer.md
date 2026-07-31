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
  },
  "time": 0.1
}
```

---

### observer.vikingdb

#### 1. API Implementation Overview

Get VikingDB status (collections, indexes, vector counts).

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_vikingdb` - HTTP route
- `openviking/service/debug_service.py:ObserverService.vikingdb` - Core implementation
- `openviking/storage/observers/vikingdb_observer.py` - VikingDB observer
- `crates/ov_cli/src/commands/observer.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/observer/vikingdb
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/vikingdb \
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
    "status": "Collection  Index Count  Vector Count  Status\ncontext     1            55            OK\nTOTAL       1            55"
  },
  "time": 0.1
}
```

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
  },
  "time": 0.1
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
  },
  "time": 0.1
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
  },
  "time": 0.1
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
  },
  "time": 0.1
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
  },
  "time": 0.1
}
```

---

## Related Documentation

- [Metrics](09-metrics.md) - Prometheus scraping
- [System Status](07-system.md) - health and consistency checks
