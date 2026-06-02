# OpenViking API Reference (v0.3.16)

Key facts confirmed from production use. Supplements the official docs.

---

## Required Headers (ALL tenant-scoped API requests)

```
Authorization: Bearer YOUR_API_KEY
x-api-key: YOUR_API_KEY
x-openviking-user: default
x-openviking-account: default
```

Missing `x-openviking-user` or `x-openviking-account` returns:
```json
{
  "status": "error",
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "ROOT requests to tenant-scoped APIs must include X-OpenViking-Account and X-OpenViking-User headers."
  }
}
```

---

## Endpoints

### Health
```
GET /health
Response: {"status":"ok","healthy":true,"version":"0.3.16",...}
```

### System Status
```
GET /api/v1/system/status
```

### Search — find
```
POST /api/v1/search/find
Body:
{
  "query": "your search query",
  "limit": 6,
  "target_uri": "viking://resources/OPTIONAL_SCOPE"   // optional
}

Response:
{
  "result": {
    "resources": [...],   // stored files and documents
    "memories":  [...],   // extracted knowledge from sessions
    "skills":    [...],
    "total": N
  }
}

NOTE: items live at result.resources[] and result.memories[]
      NOT at result.items[] — common mistake
```

### Search — search
```
POST /api/v1/search/search
Body: {"query": "...", "limit": 6}
Same response structure as /find
```

### Create Session
```
POST /api/v1/sessions
Body: {}
Response: {"result": {"session_id": "uuid-string"}}
```

### Add Message to Session
```
POST /api/v1/sessions/{session_id}/messages
Body: {"role": "user", "content": "message text"}

IMPORTANT: use {role, content} format
NOT {role, parts:[{type:"text", text:"..."}]}  <- wrong, causes error
```

### Commit Session
```
POST /api/v1/sessions/{session_id}/commit
Body: {}
Response:
{
  "result": {
    "memories_extracted": N,
    "active_count_updated": N,
    "archived": true/false
  }
}
```

### List Resources
```
GET /api/v1/fs/ls?uri=viking://
Response: {"result": {"entries": [...]}}
```

### Read Resource
```
GET /api/v1/content/read?uri=viking://resources/DIR/FILE.md
```

### Add Resource
```
POST /api/v1/resources
Body: {"path": "/local/path/to/file.md", "reason": "optional", "wait": true}
```

---

## ov.conf — Valid Fields Only

OpenViking v0.3.16 validates config strictly. Any unrecognised top-level key
causes the server to fail on startup with `Unknown config field '...'`.

Only these keys are valid:

```json
{
  "storage":   { ... },
  "log":       { ... },
  "embedding": { ... },
  "vlm":       { ... },
  "server":    { ... }
}
```

---

## Embedding Configuration

### Jina (cloud)
```json
"embedding": {
  "dense": {
    "provider":       "jina",
    "api_key":        "YOUR_JINA_API_KEY",
    "api_base":       "https://api.jina.ai/v1",
    "model":          "jina-embeddings-v3",
    "dimension":      768,
    "query_param":    "retrieval.query",
    "document_param": "retrieval.passage"
  },
  "max_concurrent": 1
}
```

### Ollama (local)
```json
"embedding": {
  "dense": {
    "provider": "openai",
    "api_key":  "ollama",
    "api_base": "http://localhost:11434/v1",
    "model":    "nomic-embed-text",
    "dimension": 768
  },
  "max_concurrent": 1
}
```

**Note:** When switching to Ollama, remove `query_param` and `document_param`
from the config. These are Jina-specific and cause errors with the
OpenAI-compatible provider tag.

---

## Server Entry Point

```python
# CORRECT:
python -m openviking_cli.server_bootstrap

# WRONG (ModuleNotFoundError):
python -m uvicorn openviking.app:app
python -m uvicorn openviking.server.app:app
```

---

## Resource URI Format

```
viking://                               root
viking://resources/                     all resources
viking://resources/MY_DIR/              a directory
viking://resources/MY_DIR/file.md       a specific file
```

---

## Session State File

`%USERPROFILE%\.claude-memory\.session_state.json`

```json
{
  "current_session": "uuid or null",
  "started_at": "ISO datetime or null",
  "last_commit": {
    "session_id": "uuid",
    "committed_at": "ISO datetime",
    "memories_extracted": 0
  }
}
```

---

## MCP Tools (openviking-bridge.py)

| Tool | Method | Endpoint |
|------|--------|----------|
| `ov_health` | GET | `/health` |
| `ov_status` | GET | `/api/v1/system/status` |
| `ov_search` | POST | `/api/v1/search/search` |
| `ov_find` | POST | `/api/v1/search/find` |
| `ov_ls` | GET | `/api/v1/fs/ls` |
| `ov_read` | GET | `/api/v1/content/read` |
| `ov_add_resource` | POST | `/api/v1/resources` |
| `ov_mkdir` | POST | `/api/v1/fs/mkdir` |
| `ov_create_session` | POST | `/api/v1/sessions` |
| `ov_add_message` | POST | `/api/v1/sessions/{id}/messages` |
| `ov_commit_session` | POST | `/api/v1/sessions/{id}/commit` |
| `ov_grep` | POST | `/api/v1/search/grep` |
