# API Overview

This page covers how to connect to OpenViking and the conventions shared across all API endpoints.

## Connection Modes

OpenViking supports two usage modes: **Embedded Mode** (direct Python API calls) and **Client-Server Mode** (via HTTP API).

This API documentation primarily focuses on the HTTP API usage in **Client-Server Mode**. Embedded mode is available but will not be covered separately in subsequent documentation.

| Mode | Use Case | Description |
|------|----------|-------------|
| **Embedded** | Local development, single process | Runs locally with local data storage |
| **HTTP** | Connect to OpenViking Server | Connects to a remote server via HTTP API |
| **CLI** | Shell scripting, agent tool-use | Connects to server via CLI commands |

### Embedded Mode (Brief Overview)

Embedded mode allows direct OpenViking API calls within a Python process without starting a separate server process.

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()
```

Embedded mode uses `ov.conf` to configure embedding, vlm, storage, and other modules. Default configuration path: `~/.openviking/ov.conf`. You can also specify the path via environment variable:

```bash
export OPENVIKING_CONFIG_FILE=/path/to/ov.conf
```

Minimal configuration example:

```json
{
  "embedding": {
    "dense": {
      "api_base": "<api-endpoint>",
      "api_key": "<your-api-key>",
      "provider": "<volcengine|openai|jina|...>",
      "dimension": 1024,
      "model": "<model-name>"
    }
  },
  "vlm": {
    "api_base": "<api-endpoint>",
    "api_key": "<your-api-key>",
    "provider": "<volcengine|openai|openai-codex|kimi|glm>",
    "model": "<model-name>"
  }
}
```

For `provider: "openai-codex"`, `vlm.api_key` is optional once Codex OAuth is available through `openviking-server init`.

For full configuration options and provider-specific examples, see the [Configuration Guide](../guides/01-configuration.md).

### Client-Server Mode (Main Focus)

Client-Server mode connects to an OpenViking server via HTTP API, supporting multi-tenancy, remote access, and other features. See the deployment documentation for how to start the OpenViking server.

#### Python SDK Client

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-key",
    agent_id="my-agent",
    timeout=120.0,
)
client.initialize()
```

When `url` is not explicitly provided, the HTTP client automatically reads connection information from `ovcli.conf`. `ovcli.conf` is a configuration file shared between the HTTP client and CLI. Default path: `~/.openviking/ovcli.conf`. You can also specify the path via environment variable:

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

Configuration file example:

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
}
```

Configuration field description:

| Field | Description | Default |
|-------|-------------|---------|
| `url` | Server address | (required) |
| `api_key` | API Key | `null` (no auth) |
| `account` | Default account header for tenant-scoped requests | `null` |
| `user` | Default user header for tenant-scoped requests | `null` |
| `agent_id` | Agent identifier header | `null` |
| `timeout` | HTTP request timeout in seconds | `60.0` |
| `output` | Default output format: `"table"` or `"json"` | `"table"` |

See the [Configuration Guide](../guides/01-configuration.md#ovcliconf) for details.

#### Using Python SDK Client Without Configuration File

`SyncHTTPClient` and `AsyncHTTPClient` support operating completely without relying on the `ovcli.conf` configuration file, by **explicitly passing all parameters** during initialization:

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",          # Explicitly provided
    api_key="your-key",                    # Explicitly provided (api_key usually identifies user identity)
    timeout=30.0,                          # Don't use default 60.0
    extra_headers={}                       # Pass empty dict instead of None, useful for gateway auth in some scenarios
)
client.initialize()
```

⚠️ **Note**: The client will attempt to load the configuration file if any of the following conditions are met:
- `url` is `None`
- `api_key` is `None`
- `timeout` equals `60.0` (default value)
- `extra_headers` is `None`

#### HTTP Call Examples

- CLI, `SyncHTTPClient`, and `AsyncHTTPClient` automatically upload local files or directories before calling the server API.
- Raw HTTP calls don't get this convenience layer. When using `curl` or other HTTP clients, you need to first call `POST /api/v1/resources/temp_upload`, then pass the returned `temp_file_id` to the target API.
- For raw HTTP imports of local directories, you need to first zip them into a `.zip` file and upload using the above method; the server does not accept direct host directory paths.
- `POST /api/v1/resources` can directly accept remote URLs, but does not accept host local paths like `./doc.md` or `/tmp/doc.md`.

Direct HTTP (curl) call example:

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key"
```

#### CLI Mode

The OpenViking CLI (can be abbreviated as `ov` command) connects to an OpenViking server and exposes all operations as shell commands. The CLI also reads connection information from `ovcli.conf` (shared with the HTTP client).

Basic usage:

```bash
openviking [global options] <command> [arguments] [command options]
```

Global options (must be placed before the command name):

| Option | Description |
|--------|-------------|
| `--output`, `-o` | Output format: `table` (default), `json` |
| `--version` | Show CLI version |

Example:

```bash
openviking -o json ls viking://resources/
```

## Lifecycle

### Embedded Mode

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# ... use client ...

client.close()
```

### Client-Server Mode

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
client.initialize()

# ... use client ...

client.close()
```

The CLI is called directly via the command line, requiring the `ovcli.conf` file to be configured first, with no additional client initialization needed:

```
openviking -o json ls viking://resources/
```

## Authentication

See the [Authentication Guide](../guides/04-authentication.md) for full details.

- **Authorization Bearer** header: `Authorization: Bearer your-key` (recommended)
- **X-API-Key** header: `X-API-Key: your-key`
- If the server doesn't have an API Key configured, authentication is skipped.
- The `/health` and `/ready` endpoints never require authentication.

## Response Format

All HTTP API responses follow a unified format:

### Success Response

```json
{
  "status": "ok",
  "result": { ... },
  "time": 0.123
}
```

The top-level `status` describes whether the HTTP API request succeeded. Some successful operations return domain-level status fields inside `result`, such as `"status": "success"`, `"status": "accepted"`, or task states. Those fields are not API transport errors.

### Error Response

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

HTTP errors always use the top-level error envelope. Synchronous processing failures, such as resource parsing or synchronous reindex failures, are returned as non-2xx responses with `status="error"` and an `error` object. Clients should not look for `result.status="error"` to detect request failure.

Request validation failures, including malformed JSON, missing required fields, and invalid parameter values, return HTTP `400` with `error.code="INVALID_ARGUMENT"`. The response never uses FastAPI's raw `{"detail": ...}` error format; when field-level validation information is available, it is exposed under `error.details.validation_errors`.

Python HTTP SDKs (`SyncHTTPClient` and `AsyncHTTPClient`) raise the corresponding `OpenVikingError` subclass for this envelope. For example, `PROCESSING_ERROR` is raised as `ProcessingError`.

## CLI Output Format

### Table Mode (Default)

List data is rendered as tables; non-list data falls back to formatted JSON:

```bash
openviking ls viking://resources/
# name          size  mode  isDir  uri
# .abstract.md  100   420   False  viking://resources/.abstract.md
```

### JSON Mode (`--output json`)

All commands output formatted JSON, matching the `result` structure of API responses:

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

### Compact Mode (`--compact`, `-c`)

- When `--output=json`: Compact JSON format + `{ok, result}` wrapper, suitable for scripts
- When `--output=table`: Simplified representation for table output (e.g., removing empty columns)

JSON output - success:

```json
{"ok": true, "result": ...}
```

JSON output - error:

```json
{"ok": false, "error": {"code": "NOT_FOUND", "message": "Resource not found", "details": {}}}
```

### Special Cases

- **String results** (`read`, `abstract`, `overview`): printed directly as plain text
- **None results** (`mkdir`, `rm`, `mv`): no output

### Exit Codes

**Note**: Exit codes are return codes from the CLI (command line tool), not HTTP API status codes.

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
| `CONFLICT` | 409 | Operation conflicts with an in-progress task or existing state |
| `DEADLINE_EXCEEDED` | 504 | Operation timed out |
| `UNAVAILABLE` | 503 | Service unavailable |
| `PROCESSING_ERROR` | 500 | Resource or semantic processing failed |
| `INTERNAL` | 500 | Internal server error |
| `UNIMPLEMENTED` | 501 | Feature not implemented |
| `EMBEDDING_FAILED` | 500 | Embedding generation failed |
| `VLM_FAILED` | 500 | VLM call failed |
| `SESSION_EXPIRED` | 410 | Session no longer exists |
| `NOT_INITIALIZED` | - | Service or component not initialized (need to call initialize() first) |

---

## API Endpoints

Below are all HTTP API endpoints provided by OpenViking, grouped by functional module:

### System

| Method | Path | Description | Authentication |
|--------|------|-------------|----------------|
| GET | `/health` | Health check (no auth) | No auth required |
| GET | `/ready` | Readiness probe (no auth) | No auth required |
| GET | `/metrics` | Prometheus metrics export | Optional |
| GET | `/api/v1/system/status` | System status | Required |
| POST | `/api/v1/system/wait` | Wait for processing | Required |

### Resources

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/resources/temp_upload` | Upload local file for raw HTTP resource / pack import |
| POST | `/api/v1/resources` | Add resource (supports URL or temp_file_id) |
| POST | `/api/v1/skills` | Add skill |

### Pack

| Method | Path | Description | Permission |
|--------|------|-------------|------------|
| POST | `/api/v1/pack/export` | Export .ovpack | ROOT/ADMIN |
| POST | `/api/v1/pack/import` | Import .ovpack | ROOT/ADMIN |

### File System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/fs/ls` | List directory |
| GET | `/api/v1/fs/tree` | Directory tree |
| GET | `/api/v1/fs/stat` | Resource status |
| POST | `/api/v1/fs/mkdir` | Create directory |
| DELETE | `/api/v1/fs` | Delete resource |
| POST | `/api/v1/fs/mv` | Move/rename resource |

### Content

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/content/read` | Read full content (L2) |
| GET | `/api/v1/content/abstract` | Read abstract (L0) |
| GET | `/api/v1/content/overview` | Read overview (L1) |
| GET | `/api/v1/content/download` | Download raw file bytes |
| POST | `/api/v1/content/write` | Update an existing file and refresh semantics/vectors |
| POST | `/api/v1/content/reindex` | Rebuild semantic/vector index for existing content (deprecated, use maintenance) |

### Search

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/search/find` | Semantic search (no session context) |
| POST | `/api/v1/search/search` | Context-aware search (supports sessions) |
| POST | `/api/v1/search/grep` | Content pattern search |
| POST | `/api/v1/search/glob` | File pattern matching |

### Relations (Experimental, may change in future versions)

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
| GET | `/api/v1/sessions/{session_id}` | Get session |
| GET | `/api/v1/sessions/{session_id}/context` | Get assembled session context |
| GET | `/api/v1/sessions/{session_id}/archives/{archive_id}` | Get a specific session archive |
| DELETE | `/api/v1/sessions/{session_id}` | Delete session |
| POST | `/api/v1/sessions/{session_id}/commit` | Commit session (archive and extract memories) |
| POST | `/api/v1/sessions/{session_id}/extract` | Extract memories from a session |
| POST | `/api/v1/sessions/{session_id}/messages` | Add message |
| POST | `/api/v1/sessions/{session_id}/used` | Record contexts / skills actually used |

### Privacy Configs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/privacy-configs` | List privacy config categories |
| GET | `/api/v1/privacy-configs/{category}` | List targets under a category |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | Get active config (`meta + current`) |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | Upsert and activate |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions` | List version numbers |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions/{version}` | Get version snapshot |
| POST | `/api/v1/privacy-configs/{category}/{target_key}/activate` | Activate a version |

### Tasks

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/tasks/{task_id}` | Get background task status |
| GET | `/api/v1/tasks` | List background tasks (supports filtering by type, status, resource) |

### Observer

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/observer/queue` | Queue status |
| GET | `/api/v1/observer/vikingdb` | VikingDB status |
| GET | `/api/v1/observer/models` | Models status (VLM / embedding / rerank) |
| GET | `/api/v1/observer/lock` | Lock subsystem status |
| GET | `/api/v1/observer/retrieval` | Retrieval subsystem status |
| GET | `/api/v1/observer/system` | System status |

### Debug

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/debug/health` | Quick health check |
| GET | `/api/v1/debug/vector/scroll` | Paginated vector record inspection |
| GET | `/api/v1/debug/vector/count` | Count vector records |

### Maintenance

| Method | Path | Description | Permission |
|--------|------|-------------|------------|
| POST | `/api/v1/maintenance/reindex` | Reindex content (optional abstract regeneration) | ROOT/ADMIN |

### Statistics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/stats/memories` | Get memory health statistics (supports category filtering) |
| GET | `/api/v1/stats/sessions/{session_id}` | Get session extraction statistics |

### Admin (Multi-tenant)

| Method | Path | Description | Permission |
|--------|------|-------------|------------|
| POST | `/api/v1/admin/accounts` | Create workspace + first admin | ROOT |
| GET | `/api/v1/admin/accounts` | List workspaces | ROOT |
| DELETE | `/api/v1/admin/accounts/{account_id}` | Delete workspace (cascade data cleanup) | ROOT |
| POST | `/api/v1/admin/accounts/{account_id}/users` | Register user | ROOT/ADMIN |
| GET | `/api/v1/admin/accounts/{account_id}/users` | List users | ROOT/ADMIN |
| GET | `/api/v1/admin/accounts/{account_id}/agents` | List agent namespaces | ROOT/ADMIN |
| DELETE | `/api/v1/admin/accounts/{account_id}/users/{user_id}` | Remove user | ROOT/ADMIN |
| PUT | `/api/v1/admin/accounts/{account_id}/users/{user_id}/role` | Change user role | ROOT |
| POST | `/api/v1/admin/accounts/{account_id}/users/{user_id}/key` | Regenerate user key | ROOT/ADMIN |

### VikingBot Interaction Endpoints (Optional)

VikingBot API requires the server to be started with the `--with-bot` option:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Bot health check (reused with system /health) |
| POST | `/chat` | Send message to Bot |
| POST | `/chat/stream` | Bot streaming response |

### WebDAV Endpoints

| Method | Path | Description |
|--------|------|-------------|
| OPTIONS | `/webdav/resources`, `/webdav/resources/{path}` | WebDAV options query |
| PROPFIND | `/webdav/resources`, `/webdav/resources/{path}` | WebDAV property query |
| GET/HEAD | `/webdav/resources/{path}` | Read file |
| PUT | `/webdav/resources/{path}` | Upload/create file (UTF-8 text only) |
| DELETE | `/webdav/resources/{path}` | Delete file/directory |
| MKCOL | `/webdav/resources/{path}` | Create directory |
| MOVE | `/webdav/resources/{path}` | Move/rename resource |

---

## Documentation Reading Plan

Subsequent API documentation is organized by functional module as follows:

| Document | Content |
|----------|---------|
| [Resources](02-resources.md) - Resource management API | Adding, importing, exporting resources and skills |
| [Retrieval](06-retrieval.md) - Search API | Search, relations, context acquisition |
| [File System](03-filesystem.md) - File system operations | Directory operations, content reading and writing |
| [Sessions](05-sessions.md) - Session management | Session creation, message management, memory extraction |
| [Skills](04-skills.md) - Skill management API | Skill management |
| [System](07-system.md) - System and monitoring API | System status, monitoring, debug API |
| [Privacy Configs](10-privacy.md) - Privacy config version management and activation | Privacy configuration |
| [Metrics](09-metrics.md) - Prometheus metrics export and scraping guide | Metrics documentation |
| [Admin](08-admin.md) - Multi-tenant management API | Multi-tenant account and user management |
