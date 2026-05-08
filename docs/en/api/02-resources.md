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
| Feishu/Lark | URL-based, supports docx, wiki, sheets, bitable, requires FEISHU_APP_ID and FEISHU_APP_SECRET configuration |

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

### Incremental Updates for Resources

Resource incremental updates are implemented via the **Watch Task** mechanism:

#### Watch Task Creation
- Set `watch_interval > 0` (in minutes) when calling `add_resource` to create a watch task
- Must specify the `to` parameter to define the target URI
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

Add a resource to the knowledge base. Supports local files/directories, URLs, and other sources.

#### 1. API Implementation Overview

This endpoint is the core entry point for resource management, supporting adding resources from various sources with optional waiting for semantic processing completion.

**Processing Flow**:
1. Identify resource source (URL or uploaded temporary file)
2. Call corresponding Parser to parse content
3. Build directory tree and write to AGFS
4. Set up scheduled update task if `watch_interval` is specified
5. Wait for semantic processing completion if `wait=true`

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
| to | string | No | - | Target Viking URI (exact location). Mutually exclusive with `parent` and `parent_auto_create` |
| parent | string | No | - | Parent Viking URI (resource placed under this directory). Mutually exclusive with `to` and `parent_auto_create` |
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
| watch_interval | float | No | 0 | Scheduled update interval (minutes). >0 creates task; <=0 cancels task |
| telemetry | TelemetryRequest | No | False | Whether to return telemetry data |

**Additional Notes**:
- `to`, `parent`, and `parent_auto_create` cannot be specified together
- `path` and `temp_file_id` cannot be specified together
- Raw HTTP calls for local files require first uploading via [temp_upload](#temp_upload) to obtain `temp_file_id`
- When `to` is specified and the target already exists, triggers incremental update
- `watch_interval` only takes effect when `to` is provided
- For local directory inputs, scanning respects `.gitignore` files (root and nested) with standard Git semantics; `ignore_dirs`, `include`, and `exclude` further refine what is ingested.

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

# Wait for processing to complete
client.wait_processed()

# Enable scheduled updates
client.add_resource(
    "./documents/guide.md",
    to="viking://resources/guide.md",
    watch_interval=60  # Update every 60 minutes
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

# Cancel scheduled updates
ov add-resource https://github.com/example/repo.git --to viking://resources/guide.md --watch-interval 0

# Add with parent directory (parent must exist)
ov add-resource ./documents/guide.md --parent viking://resources/docs

# Add with parent directory (auto-create parent if it doesn't exist)
ov add-resource ./documents/guide.md -p viking://resources/docs/2026/05/07
# Or using full flag
ov add-resource ./documents/guide.md --parent-auto-create viking://resources/docs/2026/05/07

# Using path variables with auto-create
ov add-resource ./documents/guide.md -p viking://resources/docs/{calendar:today}
```

**Response Example**

**HTTP API Response (JSON)**

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

**CLI Response (Default Table Format)**

```
Note: Resource is being processed in the background.
Use 'ov wait' to wait for completion, or 'ov observer queue' to check status.
status       success
errors       []
source_path  /Users/bytedance/workspace/github.com/OpenViking/docs/en/api/01-overview.md
meta         {}
root_uri     viking://resources/01-overview
temp_uri     viking://temp/shengmaojia/04291108_b62dc7/01-overview
```

**CLI Response (JSON Format, using -o json)**

```json
{
  "status": "success",
  "root_uri": "viking://resources/01-overview",
  "temp_uri": "viking://temp/shengmaojia/04291108_b62dc7/01-overview",
  "source_path": "/Users/bytedance/workspace/github.com/OpenViking/docs/en/api/01-overview.md",
  "meta": {},
  "errors": []
}
```

**Field Description**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Processing status: "success" or "error" |
| `root_uri` | string | Final URI of the resource in OpenViking |
| `temp_uri` | string | Temporary URI during processing (only valid during background processing) |
| `source_path` | string | Original source file path or URL |
| `meta` | object | Metadata from resource parsing (file type, size, etc.) |
| `errors` | array | List of errors encountered during processing |
| `warnings` | array | (Optional) List of warnings (only when `strict=False`) |
| `queue_status` | object | (Optional, only when `wait=true`) Queue processing status with `pending`, `processing`, `completed` counts |

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
    "root_uri": "viking://agent/skills/my-skill",
    "uri": "viking://agent/skills/my-skill",
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
root_uri        viking://agent/skills/my-skill
uri             viking://agent/skills/my-skill
name            my-skill
auxiliary_files 2
```

**CLI Response (JSON Format, using -o json)**

```json
{
  "status": "success",
  "root_uri": "viking://agent/skills/my-skill",
  "uri": "viking://agent/skills/my-skill",
  "name": "my-skill",
  "auxiliary_files": 2
}
```

**Field Description**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Processing status: "success" or "error" |
| `root_uri` | string | Final URI of the skill in OpenViking (same as `uri`) |
| `uri` | string | Final URI of the skill in OpenViking (same as `root_uri`) |
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
