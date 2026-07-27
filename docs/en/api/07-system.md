# System Status

The OpenViking System API provides health, readiness, consistency, and multi-write backend synchronization status. Component observers and Prometheus metrics are documented separately.

## API Reference

### health

#### 1. API Implementation Overview

Basic health check endpoint. No authentication required. Returns service version and health status. If authentication is provided, also returns auth mode and identity information.

**Code Entry Points**:
- `openviking/server/routers/system.py:health_check` - HTTP route
- `openviking_cli/client/sync_http.py:SyncHTTPClient.health` - SDK entry
- `crates/ov_cli/src/commands/system.rs` - CLI command

#### 2. Interface and Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| profile | string | No | - | When set to `1`, `true`, `yes`, or `on`, enables request-scoped `cProfile` and appends a `profile` field to JSON responses |

**`profile` behavior**:
- `profile` is implemented at the HTTP middleware layer and works for any OpenViking endpoint that returns JSON, not just `/health`.
- The request flag only takes effect when the server enables `server.profile_enabled = true` in `ov.conf`; otherwise the server ignores `profile=1`.
- `profile` only applies to the current request and is automatically disabled when the request completes, so later requests do not inherit it.
- The middleware only injects a `profile` field into JSON responses; plain text, file, and streaming responses are left unchanged.
- The returned value is `list[string]`, where each element is one formatted `pstats` line. This makes browser JSON viewers and line-by-line UI rendering easier.
- The `ov` CLI displays the returned `profile`. The Python HTTP client can trigger server-side profiling via `ovcli.conf.profile = true`, but most SDK methods still return only the business `result` and do not expose the top-level `profile` field directly.

**`profile` column meanings**:
- `ncalls`: Number of calls. When shown as `total/primitive`, the first value is total calls and the second is primitive calls.
- `tottime`: Total time spent in the function body itself, excluding time in subcalls.
- `percall` (first): `tottime / ncalls`, the average self time per call.
- `cumtime`: Cumulative time including the current function and all of its subcalls.
- `percall` (second): `cumtime / primitive calls`, the average cumulative time per primitive call.
- `filename:lineno(function)`: Function location. Regular Python code shows the trimmed module path; entries like `~:0(...)` usually represent builtin or native-extension calls.

#### 3. Usage Examples

**HTTP API**

```
GET /health
```

```bash
curl -X GET http://localhost:1933/health
```

```bash
curl -G http://localhost:1933/health \
  --data-urlencode "profile=1"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()

healthy = client.health()
print(f"Healthy: {healthy}")
```

**TypeScript SDK**

```typescript
console.log(await client.health());
```

**Go SDK**

```go
healthy, err := client.Health(ctx)
if err != nil {
    return err
}
fmt.Println(healthy)
```

**CLI**

```bash
ov system health
```

```bash
ov --profile health
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

**Response Example With `profile`**

```json
{
  "status": "ok",
  "healthy": true,
  "version": "0.1.x",
  "profile": [
    "         325 function calls (310 primitive calls) in 0.004 seconds",
    "",
    "   Ordered by: cumulative time",
    "   List reduced from 87 to 87 due to restriction <100>",
    "",
    "   ncalls  tottime  percall  cumtime  percall filename:lineno(function)",
    "        1    0.000    0.000    0.003    0.003 starlette/middleware/base.py:112(call_next)",
    "        1    0.000    0.000    0.001    0.001 openviking/server/routers/system.py:39(health_check)",
    "        3    0.000    0.000    0.000    0.000 ~:0(<method 'read' of 'builtins.RAGFSBindingClient' objects>)"
  ]
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

**TypeScript SDK**

```typescript
console.log(await client.getStatus());
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

**TypeScript SDK**

```typescript
console.log(await client.checkConsistency("viking://resources/"));
```

**Go SDK**

```go
report, err := client.CheckConsistency(ctx, "viking://resources/my-project")
if err != nil {
    return err
}
fmt.Println(report["ok"])
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

**TypeScript SDK**

```typescript
console.log(await client.waitProcessed(60));
```

**Go SDK**

```go
status, err := client.WaitProcessed(ctx, &openviking.WaitProcessedOptions{
    Timeout: openviking.Float64(60),
})
if err != nil {
    return err
}
fmt.Println(status)
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

### backend_sync_status()

Return multi-write backend synchronization status for a Viking URI subtree. This endpoint requires ROOT or ADMIN permission.

**HTTP API**

```http
POST /api/v1/system/backend/sync-status
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/system/backend/sync-status \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources"}'
```

The URI-path form is also available:

```http
GET /api/v1/system/sync/{sync_path}
```

**CLI**

```bash
ov system backend sync-status viking://resources
```

**Response example**

```json
{
  "status": "ok",
  "result": {
    "path": "viking://resources",
    "entry_count": 12
  }
}
```

`result` is supplied by the active filesystem backend. `path` identifies the queried scope and `entry_count` is the number of sync records in that scope. A backend may add diagnostics such as pending or failed records.

### backend_sync_retry()

Retry incomplete multi-write backend synchronization work for a URI subtree. This endpoint requires ROOT or ADMIN permission.

**HTTP API**

```http
POST /api/v1/system/backend/sync-retry
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/system/backend/sync-retry \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources"}'
```

The URI-path form is:

```http
POST /api/v1/system/sync/{sync_path}/retry
```

**CLI**

```bash
ov system backend sync-retry viking://resources
```

**Response example**

```json
{
  "status": "ok",
  "result": {
    "path": "viking://resources",
    "retried": 2,
    "failed": 0
  }
}
```

`retried` is the number of records rescheduled by this request, and `failed` is the number that could not be scheduled. A backend may include additional diagnostic fields.

The public Python, TypeScript, and Go SDKs do not currently expose multi-write backend synchronization methods, so the sections above show only HTTP and CLI tabs.

---

<a id="reindex"></a><a id="observer-api"></a>

## Related Documentation

- [Resources](02-resources.md) - Resource management
- [Retrieval](06-retrieval.md) - Search and retrieval
- [Sessions](05-sessions.md) - Session management
- [Runtime Observability](18-observer.md) - immediate component status
- [Metrics](09-metrics.md) - Prometheus metrics
