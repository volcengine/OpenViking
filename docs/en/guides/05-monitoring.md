# Monitoring & Health Checks

OpenViking Server provides endpoints for monitoring system health and component status.

## Health Check

The `/health` endpoint provides a simple liveness check. It does not require authentication.

```bash
curl http://localhost:1933/health
```

```json
{"status": "ok"}
```

## System Status

### Overall System Health

**Python SDK (Embedded / HTTP)**

```python
status = client.get_status()
print(f"Healthy: {status['is_healthy']}")
print(f"Errors: {status['errors']}")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/observer/system \
  -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "result": {
    "is_healthy": true,
    "errors": [],
    "components": {
      "queue": {"name": "queue", "is_healthy": true, "has_errors": false},
      "vikingdb": {"name": "vikingdb", "is_healthy": true, "has_errors": false},
      "vlm": {"name": "vlm", "is_healthy": true, "has_errors": false}
    }
  }
}
```

### Component Status

Check individual components:

| Endpoint | Component | Description |
|----------|-----------|-------------|
| `GET /api/v1/observer/queue` | Queue | Processing queue status |
| `GET /api/v1/observer/vikingdb` | VikingDB | Vector database status |
| `GET /api/v1/observer/vlm` | VLM | Vision Language Model status |

### Quick Health Check

**Python SDK (Embedded / HTTP)**

```python
if client.is_healthy():
    print("System OK")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/debug/health \
  -H "X-API-Key: your-key"
```

```json
{"status": "ok", "result": {"healthy": true}}
```

## Response Time

Every API response includes an `X-Process-Time` header with the server-side processing time in seconds:

```bash
curl -v http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key" 2>&1 | grep X-Process-Time
# < X-Process-Time: 0.0023
```

## Related Documentation

- [Deployment](03-deployment.md) - Server setup
- [System API](../api/07-system.md) - System API reference
