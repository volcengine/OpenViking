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
  "status": "ok"
}
```

---

### status()

Get system status including initialization state and user info.

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.system)
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
    "pending": 0,
    "in_progress": 0,
    "processed": 20,
    "errors": 0
  },
  "time": 0.1
}
```

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
print(client.observer.vikingdb)
# Output:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

# Access specific properties
print(client.observer.vikingdb.is_healthy)  # True
print(client.observer.vikingdb.status)      # Status table string
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

### observer.vlm

Get VLM (Vision Language Model) token usage status.

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.vlm)
# Output:
# [vlm] (healthy)
# Model                          Provider      Prompt  Completion  Total  Last Updated
# doubao-1-5-vision-pro-32k      volcengine    1000    500         1500   2024-01-01 12:00:00
# TOTAL                                        1000    500         1500
```

**HTTP API**

```
GET /api/v1/observer/vlm
```

```bash
curl -X GET http://localhost:1933/api/v1/observer/vlm \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking observer vlm
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "name": "vlm",
    "is_healthy": true,
    "has_errors": false,
    "status": "Model  Provider  Prompt  Completion  Total  Last Updated\ndoubao-1-5-vision-pro-32k  volcengine  1000  500  1500  2024-01-01 12:00:00\nTOTAL  1000  500  1500"
  },
  "time": 0.1
}
```

---

### observer.system

Get overall system status including all components.

**Python SDK (Embedded / HTTP)**

```python
print(client.observer.system)
# Output:
# [queue] (healthy)
# ...
#
# [vikingdb] (healthy)
# ...
#
# [vlm] (healthy)
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
    print(client.observer.system)
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
