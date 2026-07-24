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
    timeout=120.0,
)
client.initialize()
```

#### Go SDK Client

The Go SDK is an HTTP-only client for Client-Server mode. It is published from
the main repository as the `sdk/go` module.

```bash
go get github.com/volcengine/OpenViking/sdk/go
```

```go
client, err := openviking.NewClient(openviking.Config{
    BaseURL: "http://localhost:1933",
    APIKey:  "your-key",
})
if err != nil {
    return err
}
defer client.CloseIdleConnections()
```

The Go SDK sends the same identity headers as the Python HTTP client:

| Config field | HTTP header |
|--------------|-------------|
| `APIKey` | `X-API-Key` |
| `Account` | `X-OpenViking-Account` |
| `User` | `X-OpenViking-User` |
| `ActorPeerID` | `X-OpenViking-Actor-Peer` |

For normal `api_key` deployments, `APIKey` is enough because the server derives
tenant identity from the key. Set `Account` and `User` only for trusted
deployments or gateways that explicitly forward tenant identity.

It does not implement Python embedded mode or legacy `agent_id` compatibility.
See [`sdk/go/README.md`](../../../sdk/go/README.md) for package-level examples.

#### JavaScript/TypeScript SDK Client

The JavaScript/TypeScript SDK is an HTTP-only client for Node.js 18+. It ships
ESM, CommonJS and TypeScript declarations.

```bash
npm install @openviking/sdk
```

```ts
import { OpenVikingClient } from "@openviking/sdk";

const client = new OpenVikingClient({
  baseUrl: "http://localhost:1933",
  apiKey: "your-key",
});

const results = await client.search("deployment guide", {
  targetUri: "viking://resources",
});
```

It uses the same identity headers and response envelope as the Python and Go
HTTP clients. See [`sdk/typescript/README.md`](../../../sdk/typescript/README.md)
for package-level examples.

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
  "user": "alice"
}
```

Configuration field description:

| Field | Description | Default |
|-------|-------------|---------|
| `url` | Server address | (required) |
| `api_key` | API Key | `null` (no auth) |
| `account` | Default account header for tenant-scoped requests | `null` |
| `user` | Default user header for tenant-scoped requests | `null` |
| `timeout` | HTTP request timeout in seconds | `600.0` |
| `output` | Default output format: `"table"` or `"json"` | `"table"` |

See the [Configuration Guide](../guides/01-configuration.md#ovcliconf) for details.

#### Using Python SDK Client Without Configuration File

`SyncHTTPClient` and `AsyncHTTPClient` support operating completely without relying on the `ovcli.conf` configuration file, by **explicitly passing all parameters** during initialization:

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",          # Explicitly provided
    api_key="your-key",                    # Explicitly provided (api_key usually identifies user identity)
    timeout=30.0,                          # Don't use default 600.0
    extra_headers={}                       # Pass empty dict instead of None, useful for gateway auth in some scenarios
)
client.initialize()
```

⚠️ **Note**: The client will attempt to load the configuration file if any of the following conditions are met:
- `url` is `None`
- `api_key` is `None`
- `timeout` equals `600.0` (default value)
- `extra_headers` is `None`

#### HTTP Call Examples

- CLI, `SyncHTTPClient`, and `AsyncHTTPClient` automatically upload local files or directories before calling the server API.
- Python HTTP client and CLI can also opt into shared temporary uploads via client config (`ovcli.conf` -> `upload.mode = "shared"`).
- Raw HTTP calls don't get this convenience layer. When using `curl` or other HTTP clients, you need to first call `POST /api/v1/resources/temp_upload`, then pass the returned `temp_file_id` to the target API.
- `temp_upload` defaults to `upload_mode=local`. Use `upload_mode=shared` only when you explicitly want distributed shared temporary uploads.
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

This catalog follows the routes actually mounted by the server. Each group heading links to its detailed reference. Detail pages show an HTTP, Python SDK, TypeScript SDK, Go SDK, or CLI tab only when that surface is genuinely available; a raw HTTP workaround is not presented as an SDK.

### [System Status](07-system.md)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Basic health check (no authentication) |
| GET | `/ready` | AGFS, VectorDB, and API key manager readiness (no authentication) |
| GET | `/api/v1/system/status` | System status |
| POST | `/api/v1/system/wait` | Wait for background processing |
| POST | `/api/v1/system/consistency` | Check filesystem and vector-index consistency |
| POST | `/api/v1/system/backend/sync-status` | Query backend synchronization status |
| POST | `/api/v1/system/backend/sync-retry` | Retry backend synchronization |
| GET | `/api/v1/system/sync/{sync_path}` | Path-form compatibility endpoint for synchronization status |
| POST | `/api/v1/system/sync/{sync_path}/retry` | Path-form compatibility endpoint for synchronization retry |

### [Resources](02-resources.md) and [Filesystem](03-filesystem.md)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/resources/temp_upload` | Upload a temporary file for a later import |
| POST | `/api/v1/resources` | Add a resource from a URL or temporary upload |
| GET | `/api/v1/fs/ls` | List a directory |
| GET | `/api/v1/fs/tree` | Get a directory tree |
| GET | `/api/v1/fs/stat` | Get resource status |
| GET | `/api/v1/fs/attrs` | Get logical extended attributes |
| POST | `/api/v1/fs/attrs/set_tags` | Set retrieval tags (compatibility alias) |
| POST | `/api/v1/fs/mkdir` | Create a directory |
| DELETE | `/api/v1/fs` | Delete a resource |
| POST | `/api/v1/fs/mv` | Move or rename a resource |

### [Content](12-content.md)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/content/read` | Read full content (L2) |
| GET | `/api/v1/content/abstract` | Read an abstract (L0) |
| GET | `/api/v1/content/overview` | Read an overview (L1) |
| GET | `/api/v1/content/download` | Download original file bytes |
| POST | `/api/v1/content/write` | Write content and refresh semantic indexes |
| POST | `/api/v1/content/set_tags` | Set retrieval tags |
| POST | `/api/v1/content/reindex` | Rebuild semantic or vector indexes |

### [Skills](04-skills.md)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/skills` | List skills |
| POST | `/api/v1/skills` | Add a skill |
| POST | `/api/v1/skills/find` | Search skills |
| POST | `/api/v1/skills/validate` | Validate skill data |
| GET | `/api/v1/skills/{skill_name}` | Get a skill |
| PUT | `/api/v1/skills/{skill_name}` | Update a skill |
| DELETE | `/api/v1/skills/{skill_name}` | Delete a skill |

### [Sessions](05-sessions.md) and [Memory](16-memory.md)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/sessions` | Create a session |
| GET | `/api/v1/sessions` | List sessions |
| GET | `/api/v1/sessions/{session_id}` | Get a session |
| GET | `/api/v1/sessions/{session_id}/tool-results` | List tool results |
| GET | `/api/v1/sessions/{session_id}/tool-results/{tool_result_id}` | Read a tool result |
| GET | `/api/v1/sessions/{session_id}/tool-results/{tool_result_id}/search` | Search within a tool result |
| GET | `/api/v1/sessions/{session_id}/context` | Get assembled context |
| GET | `/api/v1/sessions/{session_id}/archives/{archive_id}` | Get a session archive |
| DELETE | `/api/v1/sessions/{session_id}` | Delete a session |
| POST | `/api/v1/sessions/{session_id}/commit` | Archive a session and extract memory |
| POST | `/api/v1/sessions/{session_id}/extract` | Extract memory |
| POST | `/api/v1/sessions/{session_id}/messages` | Add one message |
| POST | `/api/v1/sessions/{session_id}/messages/batch` | Add messages in a batch |
| POST | `/api/v1/sessions/{session_id}/used` | Record context or skills actually used |
| POST | `/api/v1/search/recall` | Recall memory as injection-ready context |

### [Retrieval](06-retrieval.md), [Code Retrieval](21-code.md), and [Relations](13-relations.md)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/search/find` | Semantic search |
| POST | `/api/v1/search/search` | Context-aware search |
| POST | `/api/v1/search/grep` | Content pattern search |
| POST | `/api/v1/search/glob` | File pattern matching |
| POST | `/api/v1/code/outline` | Extract code structure |
| POST | `/api/v1/code/search` | Search code |
| POST | `/api/v1/code/expand` | Expand code context |
| GET | `/api/v1/relations` | Get resource relations |
| POST | `/api/v1/relations/link` | Create a resource link |
| DELETE | `/api/v1/relations/link` | Delete a resource link |
| POST | `/api/v1/relations/build_graph` | Build a relation graph |

### [Watches](15-watches.md), [Snapshots](11-snapshot.md), and [OVPack](14-ovpack.md)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/watches` | List watches or query by `to_uri` |
| GET | `/api/v1/watches/{task_id}` | Get a watch by task ID |
| PATCH | `/api/v1/watches` | Update a watch by `to_uri` |
| PATCH | `/api/v1/watches/{task_id}` | Update a watch by task ID |
| DELETE | `/api/v1/watches` | Delete a watch by `to_uri` |
| DELETE | `/api/v1/watches/{task_id}` | Delete a watch by task ID |
| POST | `/api/v1/watches/trigger` | Trigger a watch by `to_uri` |
| POST | `/api/v1/watches/{task_id}/trigger` | Trigger a watch by task ID |
| POST | `/api/v1/snapshot/commit` | Create a snapshot |
| GET | `/api/v1/snapshot/log` | Read snapshot history |
| POST | `/api/v1/snapshot/restore` | Restore a historical snapshot |
| GET | `/api/v1/snapshot/show` | Inspect a snapshot or one of its files |
| GET | `/api/v1/snapshot/diff` | Compare snapshots |
| GET | `/api/v1/snapshot/ignore` | Read snapshot ignore rules |
| PUT | `/api/v1/snapshot/ignore` | Replace snapshot ignore rules |
| DELETE | `/api/v1/snapshot/ignore` | Clear snapshot ignore rules |
| POST | `/api/v1/pack/export` | Export an `.ovpack` |
| POST | `/api/v1/pack/import` | Import an `.ovpack` |
| POST | `/api/v1/pack/backup` | Back up public scopes |
| POST | `/api/v1/pack/restore` | Restore a backup package |

### [Background Tasks](17-tasks.md), [Runtime Observer](18-observer.md), and [Metrics](09-metrics.md)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/tasks/{task_id}` | Get a background task |
| GET | `/api/v1/tasks` | List background tasks |
| GET | `/api/v1/observer/queue` | Queue status |
| GET | `/api/v1/observer/vikingdb` | VikingDB status |
| GET | `/api/v1/observer/models` | Model status |
| GET | `/api/v1/observer/lock` | Lock status |
| GET | `/api/v1/observer/retrieval` | Retrieval status |
| GET | `/api/v1/observer/filesystem` | Filesystem status |
| GET | `/api/v1/observer/system` | Aggregate runtime status |
| GET | `/metrics` | Prometheus metrics |

### [Administration](08-admin.md) and [Privacy Configuration](10-privacy.md)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/admin/accounts` | Create an account and its first administrator |
| GET | `/api/v1/admin/accounts` | List accounts |
| POST | `/api/v1/admin/migrate` | Migrate legacy identity data |
| DELETE | `/api/v1/admin/accounts/{account_id}` | Delete an account |
| POST | `/api/v1/admin/accounts/{account_id}/users` | Register a user |
| GET | `/api/v1/admin/accounts/{account_id}/users` | List users |
| DELETE | `/api/v1/admin/accounts/{account_id}/users/{user_id}` | Remove a user |
| PUT | `/api/v1/admin/accounts/{account_id}/users/{user_id}/role` | Change a user role |
| POST | `/api/v1/admin/accounts/{account_id}/users/{user_id}/key` | Regenerate a user key |
| GET | `/api/v1/privacy-configs` | List privacy configuration categories |
| GET | `/api/v1/privacy-configs/{category}` | List category targets |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | Get the active configuration |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions` | List configuration versions |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions/{version}` | Get one version |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | Write and activate a new version |
| POST | `/api/v1/privacy-configs/{category}/{target_key}/activate` | Activate a version |

### [WebDAV](20-webdav.md) and [VikingBot API](24-vikingbot.md)

| Method | Path | Description |
|--------|------|-------------|
| OPTIONS | `/webdav/resources`, `/webdav/resources/{resource_path}` | Query WebDAV capabilities |
| PROPFIND | `/webdav/resources`, `/webdav/resources/{resource_path}` | Query resource properties |
| GET / HEAD | `/webdav/resources`, `/webdav/resources/{resource_path}` | Read a file or directory |
| PUT | `/webdav/resources`, `/webdav/resources/{resource_path}` | Write a UTF-8 text file |
| DELETE | `/webdav/resources`, `/webdav/resources/{resource_path}` | Delete a file or directory |
| MKCOL | `/webdav/resources`, `/webdav/resources/{resource_path}` | Create a directory |
| MOVE | `/webdav/resources`, `/webdav/resources/{resource_path}` | Move or rename a resource |
| GET | `/bot/v1/health` | VikingBot health check |
| POST | `/bot/v1/chat` | Non-streaming VikingBot chat |
| POST | `/bot/v1/chat/stream` | Streaming VikingBot chat |
| POST | `/bot/v1/feedback` | Submit feedback for a VikingBot answer |

---

## Documentation Reading Plan

The sidebar is organized by responsibility rather than historical file size:

| Group | What to look for |
|-------|------------------|
| Core Data | Resources, content, filesystem, skills, sessions, and memory |
| Retrieval & Relations | Semantic retrieval, code retrieval, and resource relations |
| Data Lifecycle | Watches, snapshots, and OVPack |
| Operations & Observability | System, tasks, Observer, and Metrics |
| Identity & Governance | Administration and privacy configuration |
| Protocols & Extensions | WebDAV and VikingBot API |
