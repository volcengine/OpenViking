# Resource Management

Resources are external knowledge that agents can reference. This module provides functionality for adding, importing/exporting, and uploading temporary files for resources.

## Core Concepts

### Resource Types

OpenViking supports various resource types, categorized by functionality:

**Documents**

| Type | Extensions | Description |
|------|------------|-------------|
| PDF | `.pdf` | Supports local parsing and MinerU API conversion |
| Markdown | `.md`, `.markdown`, `.mdown`, `.mkd` | Native support, extracts structure and stores in segments |
| HTML | `.html`, `.htm` | Cleans navigation/ads and extracts content, converts to Markdown |
| Word | `.docx` | Extracts text, headings, tables and converts to Markdown |
| Plain Text | `.txt`, `.text` | Direct import and processing |
| EPUB | `.epub` | E-book format, supports ebooklib or manual extraction |

**Spreadsheets & Presentations**

| Type | Extensions | Description |
|------|------------|-------------|
| Excel | `.xlsx`, `.xls`, `.xlsm` | Supports new and legacy Excel formats, converts to Markdown tables by worksheet |
| PowerPoint | `.pptx` | Extracts content by slide, supports extracting notes |

**Code**

| Type | Resource Name | Description |
|------|---------------|-------------|
| Code Files | `*.py`, `*.js`, ... | Supports common programming languages (Python, JavaScript, Go, Rust, Java, etc.) |
| Git Protocol Repository | `git://...` | Git URL, local directory, `.zip` package, respects `.gitignore` and automatically filters `.git`, `node_modules` and other directories |
| Git Code Hosting Platform | `https://github.com/{org}/{repo}` | URLs from GitHub, GitLab, Bitbucket and other code hosting platforms |
| Raw Files from Git Hosting | `https://github.com/{org}/{repo}/raw/{branch}/{path}` | Raw file download URLs from GitHub, GitLab, Bitbucket and other platforms |

**Media**

| Type | Resource Name | Description |
|------|---------------|-------------|
| Images | `*.jpg`, `*.jpeg`, `*.png`, `*.gif` ... | Various image formats, descriptions generated via VLM (Experimental) |
| Video | `*.mp4`, `*.avi`, `*.mov` ... | Extracts keyframes and analyzes with VLM (Planning) |
| Audio | `*.mp3`, `*.wav`, `*.m4a` ... | Performs speech transcription (Planning) |

**Cloud Documents**

| Type | Description |
|------|-------------|
| Feishu/Lark | URL-based, supports docx, wiki, sheets, bitable. By default uses app credentials from FEISHU_APP_ID and FEISHU_APP_SECRET; user-token imports can pass `args.feishu_access_token`, and user-token watches also pass `args.feishu_refresh_token` |

### Resource Processing Pipeline

Resources go through the following processing stages when added:

```
Source Input -> Parse -> Resource Tree Build -> Persistence -> Semantic Processing
    ↓           ↓            ↓                 ↓               ↓
  URL/File    Parser    TreeBuilder        AGFS       Summarizer/Vector
```

#### Stage 1: Parse
- Uses `UnifiedResourceProcessor` to parse content based on resource type
- Supports multiple formats: documents (PDF/Markdown/Word), spreadsheets (Excel/PPT), code, media files, etc.
- Parsed results are written to a temporary VikingFS directory
- Media files have descriptions generated via VLM (Vision Language Model)

#### Stage 2: Resource Tree Build (TreeBuilder)
- `TreeBuilder.finalize_from_temp()` scans the temporary directory structure
- Builds resource tree nodes, handles URI conflicts (auto-renames)
- Establishes relationships between directories and resources

#### Stage 3: Persistence
- Checks if target URI already exists
- New resources: moves temporary files to permanent AGFS location
- Existing resources: retains temporary tree for subsequent diff comparison
- Acquires lifecycle lock to prevent concurrent modifications
- Cleans up temporary directory

#### Stage 4: Semantic Processing
- **Summary Generation**: `Summarizer` generates L0 (abstract) and L1 (overview)
- **Vector Index**: Vectorizes content for semantic search
- Processed asynchronously via `SemanticQueue`, can wait for completion with `wait=True`

#### Non-Wait Git Repository Imports
- For Git repository sources with `wait=false`, OpenViking validates the repository, resolves the target URI, reserves the final `root_uri`, and returns before clone/parse/finalize completes.
- The immediate response contains `status`, `root_uri`, and `task_id`; fetching, parsing, finalizing, and queue waiting continue in a persistent background task.
- Poll `GET /api/v1/tasks/{task_id}` to inspect task state. Git resource import tasks use stages such as `queued`, `fetching`, `parsing`, `finalizing`, and `processing_queue`.
- Other resource sources with `wait=false` finish fetching/parsing/finalizing before the response; their returned `task_id` tracks semantic and embedding queue completion only.

### Incremental Updates for Resources

Resource incremental updates are implemented via the **Watch Task** mechanism:

#### Watch Task Creation
- Set `watch_interval > 0` (in minutes) when calling `add_resource` to create a watch task
- You may specify `to` to define the target URI; if omitted, the task binds to the `root_uri` returned by this import
- `WatchManager` handles task persistence
- Supports multi-tenant permission control (ROOT/ADMIN/USER permission levels)

#### Task Scheduling & Execution
- `WatchScheduler` checks for expired tasks every 60 seconds
- Default concurrency control prevents duplicate execution
- Expired tasks automatically re-invoke `add_resource`
- Updates task's last execution time and next execution time

#### Task Management Operations
- **Create**: Creates new task or reactivates disabled task when `watch_interval > 0`
- **Update**: Re-sets parameters for the same target URI
- **Cancel**: Disables task when `watch_interval <= 0` for the same target URI
- **Query**: Queries task status by task ID or target URI

## API Reference

### add_resource

Add a resource to the knowledge base. The SDK supports local files/directories, URLs, and other sources. Raw HTTP calls accept remote URLs through `path` or uploaded local files through `temp_file_id`.

#### 1. API Implementation Overview

This endpoint is the core entry point for resource management, supporting adding resources from various sources with optional waiting for semantic processing completion.

**Processing Flow**:
1. Identify and validate the resource source (URL or uploaded temporary file)
2. Resolve the target URI
3. Call the corresponding Parser to parse content
4. Build the directory tree and write to AGFS
5. Wait for semantic processing completion when `wait=true`; with `wait=false`, return a `task_id` for queue tracking
6. Set up scheduled update task if `watch_interval` is specified

**Code Entry Points**:
- `openviking/client/local.py:LocalClient.add_resource` - SDK entry (embedded)
- `openviking_cli/client/http.py:AsyncHTTPClient.add_resource` - SDK entry (HTTP)
- `openviking/server/routers/resources.py:add_resource` - HTTP router
- `openviking/service/resource_service.py` - Core service implementation
- `crates/ov_cli/src/handlers.rs:handle_add_resource` - CLI handler

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| path | string | No | - | Remote resource URL (HTTP/HTTPS/Git). Mutually exclusive with `temp_file_id` |
| temp_file_id | string | No | - | Temporary upload file ID. Mutually exclusive with `path` |
| to | string | No | - | Target Viking URI (exact location). Mutually exclusive with `parent` |
| parent | string | No | - | Parent Viking URI (resource placed under this directory). Mutually exclusive with `to` |
| create_parent | bool | No | False | Automatically create parent directory if it does not exist (server-side flag) |
| reason | string | No | "" | Reason for adding the resource (for documentation and relevance improvement, experimental feature) |
| instruction | string | No | "" | Processing instructions for semantic extraction (experimental feature) |
| wait | bool | No | False | Whether to wait for semantic processing and vectorization to complete before returning |
| timeout | float | No | None | Timeout in seconds, only effective when `wait=True` |
| strict | bool | No | False | Whether to use strict mode |
| ignore_dirs | string | No | None | Directory names to ignore (comma-separated) |
| include | string | No | None | File patterns to include (glob) |
| exclude | string | No | None | File patterns to exclude (glob) |
| directly_upload_media | bool | No | True | Whether to directly upload media files |
| preserve_structure | bool | No | None | Whether to preserve directory structure |
| args | object | No | `{}` | Parser-specific import options forwarded to the source parser/accessor. Core `add_resource` fields such as `path`, `to`, `watch_interval`, `include`, and `exclude` are not allowed inside `args` |
| watch_interval | float | No | 0 | Scheduled update interval (minutes). >0 creates task; <=0 cancels task; explicit `to` wins, otherwise binds to the imported `root_uri` |
| telemetry | TelemetryRequest | No | False | Whether to return telemetry data |

**Additional Notes**:
- `to` and `parent` cannot be specified together. Use `create_parent=true` with `parent` when the parent directory should be created automatically.
- Resource targets may use public `viking://resources/...`, current-user shorthand `viking://user/resources/...`, explicit user `viking://user/{user_id}/resources/...`, or peer `viking://user/{user_id}/peers/{peer_id}/resources/...` paths. Current-user shorthand is canonicalized with the authenticated request identity.
- `user_id` and `peer_id` path segments must be safe single-segment identifiers, for example `alice` or `web-visitor-alice`. Values with path separators, `.`, `..`, `:`, or `+` are rejected.
- `path` and `temp_file_id` cannot be specified together
- Raw HTTP calls for local files require first uploading via [temp_upload](#temp_upload) to obtain `temp_file_id`
- When `to` is specified and the target already exists, triggers incremental update
- Only Git repository sources use full background import when `wait=false`; OpenViking performs repository preflight and target planning before returning the `task_id`.
- Other sources with `wait=false` finish source parsing, target resolution, and AGFS writes before returning. Only semantic and embedding queues continue asynchronously.
- When `watch_interval > 0`, the watch task binds to `to` if provided; otherwise it binds to the `root_uri` returned by this import. If no stable `root_uri` is available, the request fails and asks for an explicit `to`.
- Feishu/Lark app-token imports do not pass `args.feishu_access_token`. OpenViking keeps the existing app credential flow and the SDK obtains an app/tenant token from `app_id` and `app_secret`. This mode supports both one-time imports and `watch_interval > 0`.
- Feishu/Lark one-time user-token imports pass `args={"feishu_access_token": "u-..."}` with `watch_interval <= 0`. OpenViking uses that user token only for the current import and does not store it.
- Feishu/Lark user-token watches pass `args={"feishu_access_token": "u-...", "feishu_refresh_token": "r-..."}` with `watch_interval > 0`. OpenViking stores the token state in the private watch task state, refreshes it with the configured Feishu app credentials, and uses the refreshed user token for later watch runs.
- Feishu/Lark user-token watches require `FEISHU_APP_ID` and `FEISHU_APP_SECRET` (or `feishu.app_id` and `feishu.app_secret` in `ov.conf`) because Feishu refresh tokens are bound to the app that issued them. The supplied user token must come from the same Feishu app configured in OpenViking.
- Watch task token state is stored in the internal `viking://resources/.watch_tasks.json` control file and is hidden from watch API/MCP/CLI responses. If VikingFS file encryption is enabled, this control file is encrypted at rest; otherwise the server-side control file contains plaintext token state.
- For local directory inputs, scanning respects `.gitignore` files (root and nested) with standard Git semantics; `ignore_dirs`, `include`, and `exclude` further refine what is ingested.
- To create or update plain text directly, use [content/write](03-filesystem.md#write) instead of `add_resource`. Semantic processing and embeddings are refreshed automatically after resource ingestion and content writes.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/resources
Content-Type: application/json
```

```bash
# Add resource from URL
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/guide.md",
    "reason": "User guide documentation",
    "wait": true
  }'

# Add from local file (requires temp_upload first)
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F "file=@./documents/guide.md" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"to\": \"viking://resources/guide.md\",
    \"reason\": \"User guide\"
  }"

# Add to the current user's private resource root
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://user/resources/docs\",
    \"create_parent\": true
  }"

# Add a Feishu document with a one-time user access token
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.feishu.cn/docx/doc_token",
    "args": {
      "feishu_access_token": "u-..."
    }
  }'

# Add a Feishu document with scheduled user-token refresh
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.feishu.cn/docx/doc_token",
    "to": "viking://resources/feishu/doc",
    "watch_interval": 1440,
    "args": {
      "feishu_access_token": "u-...",
      "feishu_refresh_token": "r-..."
    }
  }'
```

**Python SDK**

```python
import openviking as ov

# Using embedded mode
client = ov.OpenViking(path="./data")
client.initialize()

# Or using HTTP client
client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Add local file
result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

# Add from URL to specific location
result = client.add_resource(
    "https://example.com/api-docs.md",
    to="viking://resources/external/api-docs.md",
    reason="External API documentation"
)

# Add to the current user's private resource root
result = client.add_resource(
    "./documents/guide.md",
    parent="viking://user/resources/docs",
    create_parent=True,
)

# Wait for processing to complete
client.wait_processed()

# Enable scheduled updates
client.add_resource(
    "./documents/guide.md",
    to="viking://resources/guide.md",
    watch_interval=60  # Update every 60 minutes
)

# Add a Feishu document with a one-time user access token
client.add_resource(
    "https://example.feishu.cn/docx/doc_token",
    args={"feishu_access_token": "u-..."},
)

# Add a Feishu document with scheduled user-token refresh
client.add_resource(
    "https://example.feishu.cn/docx/doc_token",
    to="viking://resources/feishu/doc",
    watch_interval=1440,
    args={
        "feishu_access_token": "u-...",
        "feishu_refresh_token": "r-...",
    },
)
```

**CLI**

```bash
# Add local file
ov add-resource ./documents/guide.md --reason "User guide"

# Add from URL
ov add-resource https://example.com/guide.md --to viking://resources/guide.md

# Wait for processing to complete
ov add-resource ./documents/guide.md --wait

# Enable scheduled updates (check every 60 minutes)
ov add-resource https://github.com/example/repo.git --to viking://resources/guide.md --watch-interval 60

# Enable scheduled updates and bind to the URI created by this import
ov add-resource https://github.com/example/repo.git --watch-interval 60

# Cancel scheduled updates
ov add-resource https://github.com/example/repo.git --to viking://resources/guide.md --watch-interval 0

# Add a Feishu document with a one-time user access token
ov add-resource https://example.feishu.cn/docx/doc_token --args feishu_access_token:u-...

# Add a Feishu document with scheduled user-token refresh
ov add-resource https://example.feishu.cn/docx/doc_token \
  --to viking://resources/feishu/doc \
  --watch-interval 1440 \
  --args feishu_access_token:u-... \
  --args feishu_refresh_token:r-...

# Add with parent directory (parent must exist)
ov add-resource ./documents/guide.md --parent viking://resources/docs

# Add under the current user's private resource root
ov add-resource ./documents/guide.md --parent viking://user/resources/docs

# Add under a specific peer's private resource root
ov add-resource ./documents/guide.md \
  --parent viking://user/alice/peers/web-visitor-alice/resources/docs

# Add with parent directory (auto-create parent if it doesn't exist)
ov add-resource ./documents/guide.md -p viking://resources/docs/2026/05/07
# Or using full flag
ov add-resource ./documents/guide.md --parent-auto-create viking://resources/docs/2026/05/07

# Using path variables with auto-create
ov add-resource ./documents/guide.md -p viking://resources/docs/{calendar:today}
```

**Response Example**

**HTTP API Response (JSON, `wait=true`)**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/guide.md",
    "temp_uri": "viking://temp/username/04291108_b62dc7/guide.md",
    "source_path": "./documents/guide.md",
    "meta": {},
    "errors": [],
    "queue_status": {
      "pending": 5,
      "processing": 2,
      "completed": 10
    }
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**HTTP API Response (JSON, non-Git `wait=false`)**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/guide",
    "temp_uri": "viking://temp/username/04291108_b62dc7/guide",
    "source_path": "./documents/guide.md",
    "meta": {},
    "errors": [],
    "task_id": "uuid-xxx"
  }
}
```

Use the returned `task_id` to poll `/api/v1/tasks/{task_id}` for queue completion. For Git repository sources with `wait=false`, the same endpoint tracks the full background import and the completed task result contains the full import result, including `queue_status`.

**CLI Response (Default Table Format)**

```
Note: Resource is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status       success
root_uri     viking://resources/01-overview
task_id      uuid-xxx
```

**CLI Response (JSON Format, using -o json)**

```json
{
  "status": "success",
  "root_uri": "viking://resources/01-overview",
  "task_id": "uuid-xxx"
}
```

**Field Description**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Processing status: "success" or "error" |
| `root_uri` | string | Final URI of the resource in OpenViking |
| `task_id` | string | (Optional, only when `wait=false`) Task ID for polling `/api/v1/tasks/{task_id}`. Non-Git imports use it for queue tracking; Git repository imports use it for full background import tracking. |
| `temp_uri` | string | Temporary URI produced during import |
| `source_path` | string | Original source file path or URL |
| `meta` | object | Metadata from resource parsing (file type, size, etc.) |
| `errors` | array | List of errors encountered during processing |
| `warnings` | array | (Optional) List of warnings (only when `strict=False`) |
| `queue_status` | object | (Optional, only when `wait=true`) Queue processing status with `pending`, `processing`, `completed` counts |

For Git repository sources with `wait=false`, the background task has `task_type="add_resource"` and `resource_id` equal to the returned `root_uri`. Running task records may include `stage`; completed task results include `queue_status` with the final semantic and embedding queue summary.

---

### Watch Management

List, inspect, update, and trigger watch tasks created via [`add_resource`](#add_resource) with `watch_interval > 0`. The control plane is mirrored across REST (`/api/v1/watches`), the `ov task watch` CLI subcommand group, and a minimum-closure MCP surface (`list_watches` / `cancel_watch`) for agents.

#### 1. API Implementation Overview

This control plane wraps the `WatchManager` primitives without changing any server-side behavior. Every endpoint and CLI command resolves the target task by either its `task_id` (path) or its `to_uri` (query). The two keys are interchangeable; if both are supplied they must refer to the same task, otherwise the request is rejected with 400.

**Operations**:
- **List** (`GET /api/v1/watches`) — returns `{tasks, total}`; pass `?active_only=true` to filter; pass `?to_uri=...` to collapse to a single-task lookup
- **Show** (`GET /api/v1/watches/{task_id}`) — inspect one task; optional `?to_uri=` performs a cross-key sanity check
- **Update** (`PATCH /api/v1/watches/{task_id}` or `PATCH /api/v1/watches?to_uri=...`) — partial update of `watch_interval`, `is_active`, `reason`, `instruction`. `is_active` is orthogonal to `watch_interval`: flip `is_active` to pause/resume without losing the configured cadence.
- **Delete** (`DELETE /api/v1/watches/{task_id}` or `DELETE /api/v1/watches?to_uri=...`)
- **Trigger** (`POST /api/v1/watches/{task_id}/trigger` or `POST /api/v1/watches/trigger?to_uri=...`) — fire-and-forget refresh; returns immediately while the underlying re-ingest runs in the background

**Code Entry Points**:
- `openviking/server/routers/watches.py` — REST router for `/api/v1/watches`
- `crates/ov_cli/src/commands/watch.rs` — `ov task watch` CLI subcommand group
- `openviking/server/mcp_endpoint.py` — MCP `list_watches` / `cancel_watch` tools and the `watch_interval` / `to` parameters on `add_resource`
- `openviking/resource/watch_manager.py:WatchManager` — task persistence and scheduling primitives

#### 2. Interface and Parameter Description

For every single-task endpoint the path `{task_id}` can be replaced with a `?to_uri=` query argument. The CLI `<key>` argument is auto-classified: any value starting with `viking://` routes to the by-URI path, anything else is treated as a task ID (other URI schemes such as `http://` are rejected locally to avoid silent 404s).

**`PATCH /watches` body** (all fields optional; at least one is required)

| Field | Type | Description |
|-------|------|-------------|
| watch_interval | float | New cadence in minutes. Must be `> 0`; use `is_active=false` to pause without losing the cadence. |
| is_active | bool | Toggle activation without losing the cadence (pause / resume). |
| reason | string | Update the recorded reason for the watch. |
| instruction | string | Update the semantic processing instruction. |

Unrecognized fields are rejected with 422 (`extra="forbid"`). Fields left unset preserve their current values.

#### 3. Usage Examples

**HTTP API**

```bash
# List active watch tasks (drop ?active_only to include paused ones)
curl -s "http://localhost:1933/api/v1/watches?active_only=true" \
  -H "X-API-Key: your-key"

# Pause a watch without losing its cadence
curl -X PATCH "http://localhost:1933/api/v1/watches/<task_id>" \
  -H "X-API-Key: your-key" -H "Content-Type: application/json" \
  -d '{"is_active": false}'

# Trigger an immediate refresh (fire-and-forget; returns before the re-ingest finishes)
curl -X POST "http://localhost:1933/api/v1/watches/<task_id>/trigger" \
  -H "X-API-Key: your-key"

# Resolve by URI instead of task ID
curl -X DELETE "http://localhost:1933/api/v1/watches?to_uri=viking://resources/guide.md" \
  -H "X-API-Key: your-key"
```

**CLI** (subcommands of `ov task watch`)

```bash
# List active watches (drop --active-only to include paused ones)
ov task watch ls --active-only

# Inspect a single watch (key may be either a viking:// URI or a task_id)
ov task watch show viking://resources/guide.md

# Pause / resume without losing the cadence
ov task watch pause viking://resources/guide.md
ov task watch resume viking://resources/guide.md

# Update the cadence (or any combination of --active / --reason / --instruction)
ov task watch update viking://resources/guide.md --interval 30

# Trigger an immediate fire-and-forget refresh
ov task watch trigger viking://resources/guide.md

# Remove a watch task entirely
ov task watch rm viking://resources/guide.md
```

**MCP** (agent control plane — minimum closure only)

```text
list_watches()                                            # one line per task; URIs only, no task_ids surfaced
cancel_watch(to_uri="viking://resources/guide.md")        # idempotent removal by URI
```

Pause / resume / trigger / update are intentionally not exposed via MCP — those power-user operations live on the CLI/REST surface to keep the agent system prompt compact. Creating a watch or changing its cadence from the agent side still goes through [`add_resource`](#add_resource) with `watch_interval`; pass `to` explicitly or let the system bind to the `root_uri` returned by this import.

---

### add_skill

Add a skill to the knowledge base.

#### 1. API Implementation Overview

Skills are special resources used to define operations or tools that agents can execute.

**Processing Flow**:
1. Receive skill data or uploaded temporary file
2. Parse skill definition
3. Store to skill directory
4. Wait for skill processing completion if `wait=true`

**Code Entry Points**:
- `openviking/client/local.py:LocalClient.add_skill` - SDK entry (embedded)
- `openviking_cli/client/http.py:AsyncHTTPClient.add_skill` - SDK entry (HTTP)
- `openviking/server/routers/resources.py:add_skill` - HTTP router
- `openviking/service/resource_service.py` - Core service implementation
- `crates/ov_cli/src/handlers.rs:handle_add_skill` - CLI handler

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| data | Any | No | - | Inline skill content or structured data. Mutually exclusive with `temp_file_id` |
| temp_file_id | string | No | - | Temporary upload file ID (obtained via [temp_upload](#temp_upload)). Mutually exclusive with `data` |
| wait | bool | No | False | Whether to wait for skill processing to complete |
| timeout | float | No | None | Timeout in seconds, only effective when `wait=True` |
| telemetry | TelemetryRequest | No | False | Whether to return telemetry data |

Skills are always installed under the current user's skills root. The public short form
`viking://user/skills` is accepted for filesystem/search operations and resolves to
`viking://user/{user_id}/skills`; `add_skill` does not accept `to`, `parent`,
`root_uri`, or peer-scoped skill targets.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/skills
Content-Type: application/json
```

```bash
# Using inline data
curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "data": {
      "name": "my-skill",
      "description": "My custom skill",
      "steps": []
    }
  }'

# Using local file (requires temp_upload first)
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F "file=@./skills/my-skill.json" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/skills \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\"
  }"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
client.initialize()

# Add skill from local file
result = client.add_skill("./skills/my-skill.json")

# Wait for processing to complete
client.wait_processed()
```

**CLI**

```bash
# Add skill
ov add-skill ./skills/my-skill.json

# Wait for processing to complete
ov add-skill ./skills/my-skill.json --wait
```

#### 4. Response Example

**HTTP API Response (JSON)**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://user/alice/skills/my-skill",
    "uri": "viking://user/alice/skills/my-skill",
    "name": "my-skill",
    "auxiliary_files": 2,
    "queue_status": {
      "pending": 0,
      "processing": 0,
      "completed": 1
    }
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**CLI Response (Default Table Format)**

```
Note: Skill is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status          success
root_uri        viking://user/alice/skills/my-skill
uri             viking://user/alice/skills/my-skill
name            my-skill
auxiliary_files 2
```

**CLI Response (JSON Format, using -o json)**

```json
{
  "status": "success",
  "root_uri": "viking://user/alice/skills/my-skill",
  "uri": "viking://user/alice/skills/my-skill",
  "name": "my-skill",
  "auxiliary_files": 2
}
```

**Field Description**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Processing status: "success" or "error" |
| `root_uri` | string | Canonical final URI of the skill in OpenViking (same as `uri`) |
| `uri` | string | Canonical final URI of the skill in OpenViking (same as `root_uri`) |
| `name` | string | Skill name |
| `auxiliary_files` | number | Number of auxiliary files attached to the skill |
| `queue_status` | object | (Optional, only when `wait=true`) Queue processing status with `pending`, `processing`, `completed` counts |

---

### temp_upload

Upload a temporary file for subsequent importing of local files via [add_resource](#add_resource) or [add_skill](#add_skill).

#### 1. API Implementation Overview

This endpoint uploads a local file into temporary server-managed storage and returns a `temp_file_id` for subsequent API calls. This is a helper endpoint typically not called directly but used automatically via the SDK or CLI.

**Processing Flow**:
1. Receive uploaded file
2. Choose temporary upload backend based on `upload_mode`
3. Save the file and record original filename
4. Return temporary file ID

**Code Entry Points**:
- `openviking/server/routers/resources.py:temp_upload` - HTTP router
- `openviking/service/resource_service.py` - Service implementation

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| file | UploadFile | Yes | - | Uploaded file (multipart/form-data) |
| telemetry | bool | No | False | Whether to return telemetry data |
| upload_mode | string | No | `"local"` | Temporary upload mode. `local` keeps the existing single-node behavior. `shared` uploads to shared temporary storage for distributed deployments. |

Notes:

- The default is `local`, so existing clients keep the original behavior unless they explicitly opt into `shared`.
- Use `upload_mode=shared` only when you explicitly want distributed shared temporary uploads.
- `shared` mode returns a one-time `temp_file_id` in the `shared_<upload_id>` form.
- Shared upload objects live under the internal `viking://upload/...` namespace and are not part of the normal filesystem browsing surface.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/resources/temp_upload
Content-Type: multipart/form-data
```

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -H "X-API-Key: your-key" \
  -F "file=@./documents/guide.md"
```

Distributed / shared upload:

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -H "X-API-Key: your-key" \
  -F "file=@./documents/guide.md" \
  -F "upload_mode=shared"
```

**Python SDK**

The `add_resource`, `add_skill` and other endpoints in the Python SDK automatically handle local file uploads, no need to call this endpoint manually. To opt into distributed shared temporary uploads in HTTP client mode, set `upload.mode` to `"shared"` in `ovcli.conf`.

**CLI**

CLI commands also automatically handle local file uploads, no need to call this endpoint manually.

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "temp_file_id": "upload_abc123def456.md"
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

Possible shared response:

```json
{
  "status": "ok",
  "result": {
    "temp_file_id": "shared_7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123"
  }
}
```

---

## Related Documentation

- [File System](03-filesystem.md) - File and directory operations
- [Skills](04-skills.md) - Skill management APIs
- [Retrieval](06-retrieval.md) - Search and context acquisition
- [ovpack Guide](../guides/09-ovpack.md) - Detailed ovpack import/export documentation
