# API Overview

This page covers how to connect to OpenViking and the conventions shared across all API endpoints.

## Connecting to OpenViking

OpenViking supports three connection modes:

| Mode | Use Case | Singleton |
|------|----------|-----------|
| **Embedded** | Local development, single process | Yes |
| **Service** | Remote VectorDB + AGFS infrastructure | No |
| **HTTP** | Connect to OpenViking Server | No |

### Embedded Mode

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

### Service Mode

```python
client = ov.OpenViking(
    vectordb_url="http://vectordb.example.com:8000",
    agfs_url="http://agfs.example.com:1833",
)
client.initialize()
```

### HTTP Mode

```python
client = ov.OpenViking(
    url="http://localhost:1933",
    api_key="your-key",
)
client.initialize()
```

### Direct HTTP (curl)

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

## Client Lifecycle

```python
client = ov.OpenViking(path="./data")  # or url="http://..."
client.initialize()  # Required before any operations

# ... use client ...

client.close()  # Release resources
```

## Authentication

See [Authentication Guide](../guides/04-authentication.md) for full details.

- **X-API-Key** header: `X-API-Key: your-key`
- **Bearer** header: `Authorization: Bearer your-key`
- If no API key is configured on the server, authentication is skipped.
- The `/health` endpoint never requires authentication.

## Response Format

All HTTP API responses follow a unified format:

**Success**

```json
{
  "status": "ok",
  "result": { ... },
  "time": 0.123
}
```

**Error**

```json
{
  "status": "error",
  "error": {
    "code": "NOT_FOUND",
    "message": "Resource not found: viking://resources/nonexistent/"
  },
  "time": 0.01
}
```

## Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `OK` | 200 | Success |
| `INVALID_ARGUMENT` | 400 | Invalid parameter |
| `INVALID_URI` | 400 | Invalid Viking URI format |
| `NOT_FOUND` | 404 | Resource not found |
| `ALREADY_EXISTS` | 409 | Resource already exists |
| `UNAUTHENTICATED` | 401 | Missing or invalid API key |
| `PERMISSION_DENIED` | 403 | Insufficient permissions |
| `RESOURCE_EXHAUSTED` | 429 | Rate limit exceeded |
| `FAILED_PRECONDITION` | 412 | Precondition failed |
| `DEADLINE_EXCEEDED` | 504 | Operation timed out |
| `UNAVAILABLE` | 503 | Service unavailable |
| `INTERNAL` | 500 | Internal server error |
| `UNIMPLEMENTED` | 501 | Feature not implemented |
| `EMBEDDING_FAILED` | 500 | Embedding generation failed |
| `VLM_FAILED` | 500 | VLM call failed |
| `SESSION_EXPIRED` | 410 | Session no longer exists |

## API Endpoints

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (no auth) |
| GET | `/api/v1/system/status` | System status |
| POST | `/api/v1/system/wait` | Wait for processing |

### Resources

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/resources` | Add resource |
| POST | `/api/v1/skills` | Add skill |
| POST | `/api/v1/pack/export` | Export .ovpack |
| POST | `/api/v1/pack/import` | Import .ovpack |

### File System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/fs/ls` | List directory |
| GET | `/api/v1/fs/tree` | Directory tree |
| GET | `/api/v1/fs/stat` | Resource status |
| POST | `/api/v1/fs/mkdir` | Create directory |
| DELETE | `/api/v1/fs` | Delete resource |
| POST | `/api/v1/fs/mv` | Move resource |

### Content

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/content/read` | Read full content (L2) |
| GET | `/api/v1/content/abstract` | Read abstract (L0) |
| GET | `/api/v1/content/overview` | Read overview (L1) |

### Search

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/search/find` | Semantic search |
| POST | `/api/v1/search/search` | Context-aware search |
| POST | `/api/v1/search/grep` | Pattern search |
| POST | `/api/v1/search/glob` | File pattern matching |

### Relations

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/relations` | Get relations |
| POST | `/api/v1/relations/link` | Create link |
| DELETE | `/api/v1/relations/link` | Remove link |

### Sessions

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/sessions` | Create session |
| GET | `/api/v1/sessions` | List sessions |
| GET | `/api/v1/sessions/{id}` | Get session |
| DELETE | `/api/v1/sessions/{id}` | Delete session |
| POST | `/api/v1/sessions/{id}/compress` | Compress session |
| POST | `/api/v1/sessions/{id}/extract` | Extract memories |
| POST | `/api/v1/sessions/{id}/messages` | Add message |

### Observer

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/observer/queue` | Queue status |
| GET | `/api/v1/observer/vikingdb` | VikingDB status |
| GET | `/api/v1/observer/vlm` | VLM status |
| GET | `/api/v1/observer/system` | System status |
| GET | `/api/v1/debug/health` | Quick health check |

## Related Documentation

- [Resources](02-resources.md) - Resource management API
- [Retrieval](06-retrieval.md) - Search API
- [File System](03-filesystem.md) - File system operations
- [Sessions](05-sessions.md) - Session management
- [Skills](04-skills.md) - Skill management
- [System](07-system.md) - System and monitoring API
