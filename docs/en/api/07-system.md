# System and Monitoring

OpenViking provides system health, observability, and debug APIs for monitoring component status.

## API Reference

### health

#### 1. API Implementation Overview

Basic health check endpoint. No authentication required. Returns service version and health status. If authentication is provided, also returns auth mode and identity information.

**Code Entry Points**:
- `openviking/server/routers/system.py:health_check` - HTTP route
- `openviking_cli/client/sync_http.py:SyncHTTPClient.health` - SDK entry
- `crates/ov_cli/src/commands/system.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /health
```

```bash
curl -X GET http://localhost:1933/health
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()

healthy = client.health()
print(f"Healthy: {healthy}")
```

**CLI**

```bash
ov system health
```

**Response Example**

```json
{
  "status": "ok",
  "healthy": true,
  "version": "0.1.x",
  "auth_mode": "api_key"
}
```

---

### ready

#### 1. API Implementation Overview

Readiness probe for deployment environments. Checks AGFS, VectorDB, APIKeyManager, and Ollama (if configured) status. Returns 200 when all configured subsystems are ready and 503 otherwise. No authentication required (designed for Kubernetes probes).

**Code Entry Points**:
- `openviking/server/routers/system.py:readiness_check` - HTTP route

#### 2. Interface and Parameters

No parameters.

**Check Item Descriptions**:
- `agfs`: Whether Viking filesystem is accessible
- `vectordb`: Whether vector database is healthy
- `api_key_manager`: Whether API key manager is loaded
- `ollama`: Whether Ollama service is reachable (only if configured)

#### 3. Usage Examples

**HTTP API**

```
GET /ready
```

```bash
curl -X GET http://localhost:1933/ready
```

**Response Example**

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

### status

#### 1. API Implementation Overview

Get system status including initialization state and authenticated user info. `result.user` is the authenticated request's `user_id` (from API key or headers), not the process-level service default - clients can use this to resolve multi-tenant paths.

**Code Entry Points**:
- `openviking/server/routers/system.py:system_status` - HTTP route
- `openviking_cli/client/sync_http.py:SyncHTTPClient.get_status` - SDK entry
- `crates/ov_cli/src/commands/system.rs` - CLI command

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/system/status
```

```bash
curl -X GET http://localhost:1933/api/v1/system/status \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
status = client.get_status()
print(status)
```

**CLI**

```bash
ov system status
```

**Response Example**

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

### wait_processed

#### 1. API Implementation Overview

Wait for all asynchronous processing (embedding, semantic generation) to complete. This method blocks until all queued tasks are processed or timeout occurs.

**Code Entry Points**:
- `openviking/server/routers/system.py:wait_processed` - HTTP route
- `openviking_cli/client/sync_http.py:SyncHTTPClient.wait_processed` - SDK entry
- `crates/ov_cli/src/commands/system.rs` - CLI command

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| timeout | float | No | None | Timeout in seconds. None means wait indefinitely |

#### 3. Usage Examples

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

**Python SDK**

```python
# Add resources
client.add_resource("./docs/")

# Wait for all processing to complete
status = client.wait_processed(timeout=60.0)
print(f"Processing complete: {status}")
```

**CLI**

```bash
ov system wait --timeout 60
```

**Response Example**

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

The observer API provides detailed component-level monitoring.

### observer.queue

#### 1. API Implementation Overview

Get queue system status (embedding and semantic processing queues). Shows pending, in-progress, completed, and error counts for each queue.

**Code Entry Points**:
- `openviking/server/routers/observer.py:observer_queue` - HTTP route
- `openviking/service/debug_service.py:ObserverService.queue` - Core implementation
- `openviking/storage/observers/queue_observer.py` - Queue observer
- `crates/ov_cli/src/commands/observer.rs` - CLI command

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
print(client.observer.queue)
# Output:
# [queue] (healthy)
# Queue                 Pending  In Progress  Processed  Errors  Total
# Embedding             0        0            10         0       10
# Semantic              0        0            10         0       10
# TOTAL                 0        0            20         0       20
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
print(client.observer.models)
# Output:
# [models] (healthy)
# provider_model         healthy  detail
# dense_embedding        yes      ...
# rerank                 yes      ...
# vlm                    yes      ...
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

**Python SDK**

```python
print(client.observer.lock)
```

**CLI**

```bash
ov observer transaction
```

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

**Python SDK**

```python
print(client.observer.retrieval)
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

- [Resources](02-resources.md) - Resource management
- [Retrieval](06-retrieval.md) - Search and retrieval
- [Sessions](05-sessions.md) - Session management
