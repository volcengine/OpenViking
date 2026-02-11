# API Overview

This page covers how to connect to OpenViking and the conventions shared across all API endpoints.

## Connecting to OpenViking

OpenViking supports three connection modes:

| Mode | Use Case | Description |
|------|----------|-------------|
| **Embedded** | Local development, single process | Runs locally with local data storage |
| **HTTP** | Connect to OpenViking Server | Connects to a remote server via HTTP API |
| **CLI** | Shell scripting, agent tool-use | Connects to server via CLI commands |

### Embedded Mode

```python
import openviking as ov

client = ov.OpenViking(path="./data")
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

### CLI Mode

The CLI connects to an OpenViking server and exposes all operations as shell commands.

**Configuration**

Create `~/.openviking/ovcli.conf` (or set `OPENVIKING_CLI_CONFIG_FILE` environment variable):

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

**Basic Usage**

```bash
openviking [global options] <command> [arguments] [command options]
```

**Global Options** (must be placed before the command name)

| Option | Description |
|--------|-------------|
| `--output`, `-o` | Output format: `table` (default), `json` |
| `--json` | Compact JSON with `{ok, result}` wrapper (for scripts) |
| `--version` | Show CLI version |

Example:

```bash
openviking --json ls viking://resources/
openviking -o json ls viking://resources/
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

## CLI Output Format

### Table Mode (default)

List data is rendered as tables; non-list data falls back to formatted JSON:

```bash
openviking ls viking://resources/
# name          size  mode  isDir  uri
# .abstract.md  100   420   False  viking://resources/.abstract.md
```

### JSON Mode (`--output json`)

All commands output formatted JSON matching the API response `result` structure:

```bash
openviking -o json ls viking://resources/
# [{ "name": "...", "size": 100, ... }, ...]
```

The default output format can be set in `ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "output": "json"
}
```

### Script Mode (`--json`)

Compact JSON with status wrapper, suitable for scripting. Overrides `--output`:

**Success**

```json
{"ok": true, "result": ...}
```

**Error**

```json
{"ok": false, "error": {"code": "NOT_FOUND", "message": "Resource not found", "details": {}}}
```

### Special Cases

- **String results** (`read`, `abstract`, `overview`): printed directly as plain text
- **None results** (`mkdir`, `rm`, `mv`): no output

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Configuration error |
| 3 | Connection error |

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
| POST | `/api/v1/sessions/{id}/commit` | Commit session |
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
