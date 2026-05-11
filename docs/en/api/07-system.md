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

### consistency

#### 1. API Implementation Overview

Check filesystem/vector-index consistency for a URI subtree. This is a general
data consistency API for debugging missing index records, failed vector snapshot
exports, and related issues. It is not an OVPack-private API;
`ov export --include-vectors` and `ov backup --include-vectors` reuse the same
check.

The response returns only a summary and missing records. It does not return the
full expected-record list. `missing_records` includes at most the first 20
records; `missing_records_truncated` is `true` when more missing records exist.

**Code Entry Points**:
- `openviking/server/routers/system.py:check_consistency` - HTTP route
- `openviking_cli/client/sync_http.py:SyncHTTPClient.check_consistency` - SDK entry
- `crates/ov_cli/src/commands/system.rs:consistency` - CLI command

#### 2. Interface and Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | string | Yes | - | Viking URI subtree to check |

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/system/consistency
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/system/consistency \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"uri":"viking://resources/my-project"}'
```

**Python SDK**

```python
report = client.check_consistency("viking://resources/my-project")
print(report["ok"])
print(report["missing_records"])
```

**CLI**

```bash
ov system consistency viking://resources/my-project
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
	    "ok": false,
	    "expected_count": 3,
	    "missing_record_count": 1,
	    "missing_records_truncated": false,
	    "missing_records": [
      {
        "uri": "viking://resources/my-project/README.md",
        "path": "README.md",
        "level": 2,
        "key": "README.md#level=2"
      }
    ]
  }
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

### reindex()

Reindex semantic and/or vector artifacts for existing content already stored in OpenViking. This is an operational maintenance API intended for scenarios such as embedding model changes, VLM changes, vector store rebuild, or post-upgrade repair of existing indexes.

This API operates on existing `viking://...` content. It does not import new files. For normal ingestion, use [Resources](02-resources.md).

**Authentication**

- HTTP endpoint: requires root/admin access when authentication is enabled; root-key requests must include `X-OpenViking-Account`
- Python embedded mode: uses the current service context
- Python HTTP client / CLI: sends the current authenticated identity

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to reindex |
| mode | str | No | `vectors_only` | Reindex mode: `vectors_only` or `semantic_and_vectors` |
| wait | bool | No | `true` | Whether to wait for completion |

The HTTP request body rejects unknown fields. `uri` may use OpenViking path variables accepted by other content APIs; it is resolved before validation.

**Supported URI scopes**

- `viking://`
- `viking://user`
- `viking://user/<user_id>`
- `viking://agent`
- `viking://agent/<agent_id>`
- `viking://resources`
- `viking://resources/...`
- `viking://user/<user_id>/memories/...`
- `viking://agent/<agent_id>/memories/...`
- `viking://agent/<agent_id>/skills`
- `viking://agent/<agent_id>/skills/<skill_name>`

`viking://session/...` is not supported by `reindex()`.

**Modes**

- `vectors_only`: rebuilds vector-store records from currently recoverable source data without rewriting `.abstract.md` or `.overview.md`
- `semantic_and_vectors`: regenerates semantic artifacts first, then rebuilds vectors from the refreshed semantic outputs

For `resource` and `skill`, `semantic_and_vectors` refreshes directory/file semantic artifacts, including `.abstract.md` and `.overview.md`. For `memory`, it rebuilds the current persisted memory subtree semantics and vectors, but it does not replay historical extraction order.

For `semantic_and_vectors`, semantic generation and vector rebuilding are sequenced by the reindex executor. The semantic refresh step does not enqueue its own background vectorization work; vectors are rebuilt by the reindex step so `wait=true` reflects the reindex operation itself.

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

There is no `/api/v1/maintenance/reindex` endpoint. Use `/api/v1/content/reindex`.

```bash
curl -X POST http://localhost:1933/api/v1/content/reindex \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
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

**Synchronous response (`wait=true`)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "status": "completed",
    "object_type": "resource",
    "scanned_records": 120,
    "rebuilt_records": 118,
    "unsupported_records": 2,
    "failed_records": 0,
    "duration_ms": 1284,
    "warnings": []
  },
  "time": 0.1
}
```

**Asynchronous response (`wait=false`)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "object_type": "resource",
    "status": "accepted",
    "task_id": "task_xxx"
  },
  "time": 0.1
}
```

Poll the returned task through the task API:

```bash
curl -X GET http://localhost:1933/api/v1/tasks/task_xxx \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default"
```

Reindex background tasks use `task_type="admin_reindex"` and `resource_id` equal to the requested `uri`, so they can also be listed with:

```text
GET /api/v1/tasks?task_type=admin_reindex&resource_id=viking://resources
```

Task records are kept in memory and can expire or be lost on server restart.

**Result fields**

| Field | Description |
|-------|-------------|
| status | `completed` for synchronous completion, `accepted` for background execution |
| uri | Requested URI after path-variable resolution |
| object_type | Inferred target type, such as `resource`, `skill`, `memory`, `user_namespace`, `agent_namespace`, `skill_namespace`, or `global_namespace` |
| mode | Effective reindex mode |
| scanned_records | Number of records or semantic sources considered |
| rebuilt_records | Number of vector records successfully rebuilt |
| unsupported_records | Number of records skipped because no usable vector source was available |
| failed_records | Number of records that failed while rebuilding |
| duration_ms | Synchronous run duration in milliseconds |
| warnings | Recoverable per-record warnings |
| task_id | Background task ID, present only when `wait=false` |

**Behavior notes**

- Reindex is non-destructive. It uses rebuild/upsert behavior and does not require dropping the vector collection first.
- `viking://` reindex fans out to supported top-level namespaces and excludes `session`.
- Namespace reindex operations such as `viking://user` or `viking://agent/default` propagate to their supported child content types.
- `vectors_only` is the right mode when only the embedding model or vector index needs to be refreshed.
- `semantic_and_vectors` is the right mode when semantic artifacts themselves must be regenerated before re-vectorization.
- Only one reindex task can run for the same URI and owner at a time. A concurrent request for the same target returns a conflict.
- For resource files, text files can use file content when no summary is available. Non-text files require a generated summary or existing vector record fallback; otherwise they are counted as unsupported.

**Current limitations**

- Reindex uses the best currently recoverable source inputs. It is not guaranteed to replay the exact historical embedding input byte-for-byte in every case.
- Memory semantic reindex is based on the currently persisted memory tree. It does not reconstruct the original chronological memory-extraction pipeline.

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
