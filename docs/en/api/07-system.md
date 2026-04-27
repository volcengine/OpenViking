# System and Monitoring

OpenViking provides system health, observability, and debug APIs for monitoring component status.

## API Reference

### health()

Basic health check endpoint. No authentication required.

**Python SDK (Embedded / HTTP)**

```python
# Check if system is healthy
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

**Response**

```json
{
  "status": "ok",
  "healthy": true,
  "version": "0.1.x"
}
```

---

### ready()

Readiness probe for deployment environments. Returns `200` when core subsystems are ready and `503` otherwise.

**HTTP API**

```
GET /ready
```

```bash
curl -X GET http://localhost:1933/ready
```

**Response**

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

Get system status including initialization state and user info.

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

**Response**

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

Wait for all asynchronous processing (embedding, semantic generation) to complete.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| timeout | float | No | None | Timeout in seconds |

**Python SDK (Embedded / HTTP)**

```python
# Add resources
client.add_resource("./docs/")

# Wait for all processing to complete
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

**Response**

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

- HTTP endpoint: requires root/admin access when authentication is enabled
- Python embedded mode: uses the current service context
- Python HTTP client / CLI: sends the current authenticated identity

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to reindex |
| mode | str | No | `vectors_only` | Reindex mode: `vectors_only` or `semantic_and_vectors` |
| wait | bool | No | `true` | Whether to wait for completion |

**Supported URI scopes**

- `viking://`
- `viking://user`
- `viking://user/<user_id>`
- `viking://agent`
- `viking://agent/<agent_id>`
- `viking://resources/...`
- `viking://user/<user_id>/memories/...`
- `viking://agent/<agent_id>/memories/...`
- `viking://agent/<agent_id>/skills/...`

`viking://session/...` is not supported by `reindex()`.

**Modes**

- `vectors_only`: rebuilds vector-store records from currently recoverable source data without rewriting `.abstract.md` or `.overview.md`
- `semantic_and_vectors`: regenerates semantic artifacts first, then rebuilds vectors from the refreshed semantic outputs

For `resource` and `skill`, `semantic_and_vectors` refreshes directory/file semantic artifacts, including `.abstract.md` and `.overview.md`. For `memory`, it rebuilds the current persisted memory subtree semantics and vectors, but it does not replay historical extraction order.

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

**Synchronous response (`wait=true`)**

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

**Asynchronous response (`wait=false`)**

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

**Behavior notes**

- Reindex is non-destructive. It uses rebuild/upsert behavior and does not require dropping the vector collection first.
- `viking://` reindex fans out to supported top-level namespaces and excludes `session`.
- Namespace reindex operations such as `viking://user` or `viking://agent/default` propagate to their supported child content types.
- `vectors_only` is the right mode when only the embedding model or vector index needs to be refreshed.
- `semantic_and_vectors` is the right mode when semantic artifacts themselves must be regenerated before re-vectorization.

**Current limitations**

- Reindex uses the best currently recoverable source inputs. It is not guaranteed to replay the exact historical embedding input byte-for-byte in every case.
- Memory semantic reindex is based on the currently persisted memory tree. It does not reconstruct the original chronological memory-extraction pipeline.

---

## Observer API

The observer API provides detailed component-level monitoring.

### observer.queue

Get queue system status (embedding and semantic processing queues).

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

**Response**

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

Get VikingDB status (collections, indexes, vector counts).

**Python SDK (Embedded / HTTP)**

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

**Response**

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

Get aggregated model subsystem status (VLM, embedding, rerank).

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

**Response**

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

Additional HTTP observer endpoints are also available:

- `GET /api/v1/observer/lock`
- `GET /api/v1/observer/retrieval`

### observer.system

Get overall system status including all components.

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

**Response**

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

Quick health check for the entire system.

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

**Response**

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

## Data Structures

### ComponentStatus

Status information for a single component.

| Field | Type | Description |
|-------|------|-------------|
| name | str | Component name |
| is_healthy | bool | Whether the component is healthy |
| has_errors | bool | Whether the component has errors |
| status | str | Status table string |

### SystemStatus

Overall system status including all components.

| Field | Type | Description |
|-------|------|-------------|
| is_healthy | bool | Whether the entire system is healthy |
| components | Dict[str, ComponentStatus] | Status of each component |
| errors | List[str] | List of error messages |

---

## Related Documentation

- [Resources](02-resources.md) - Resource management
- [Retrieval](06-retrieval.md) - Search and retrieval
- [Sessions](05-sessions.md) - Session management
