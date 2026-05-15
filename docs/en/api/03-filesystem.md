# File System

OpenViking provides Unix-like file system operations for managing context.

## WebDAV (Phase 1)

OpenViking Server also exposes a minimal WebDAV adapter for resource files:

```text
/webdav/resources
```

Phase 1 intentionally keeps the scope narrow:

- Resources only. Memories, skills, sessions, and other namespaces are not exposed.
- Text-first writes. `PUT` currently accepts UTF-8 text content only.
- WebDAV subset only. `OPTIONS`, `PROPFIND`, `GET`, `HEAD`, `PUT`, `DELETE`, `MKCOL`, and `MOVE` are supported.
- Semantic sidecars stay internal. Derived files such as `.abstract.md`, `.overview.md`, `.relations.json`, and `.path.ovlock` are hidden from listings and cannot be accessed directly through WebDAV.

Behavior notes:

- Creating a new file through WebDAV triggers OpenViking semantic generation for that file path.
- Replacing an existing file through WebDAV refreshes related semantics and vectors, same as `write()`.
- User-created dot-directories and dot-files remain visible unless they match one of the reserved internal filenames above.

## API Reference

### abstract()

Read L0 abstract (~100 tokens summary).

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI (must be a directory) |

**Python SDK (Embedded / HTTP)**

```python
abstract = client.abstract("viking://resources/docs/")
print(f"Abstract: {abstract}")
# Output: "Documentation for the project API, covering authentication, endpoints..."
```

**HTTP API**

```
GET /api/v1/content/abstract?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/content/abstract?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking abstract viking://resources/docs/
```

**Response**

```json
{
  "status": "ok",
  "result": "Documentation for the project API, covering authentication, endpoints...",
  "time": 0.1
}
```

---

### overview()

Read L1 overview, applies to directories.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI (must be a directory) |

**Python SDK (Embedded / HTTP)**

```python
overview = client.overview("viking://resources/docs/")
print(f"Overview:\n{overview}")
```

**HTTP API**

```
GET /api/v1/content/overview?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking overview viking://resources/docs/
```

**Response**

```json
{
  "status": "ok",
  "result": "## docs/\n\nContains API documentation and guides...",
  "time": 0.1
}
```

---

### read()

Read L2 full content.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |
| offset | int | No | 0 | Starting line number (0-indexed) |
| limit | int | No | -1 | Number of lines to read, `-1` means read to end |

**Notes**

- `read()` accepts file URIs only. Passing an existing directory URI returns `INVALID_ARGUMENT` (`400`), not `NOT_FOUND`.
- Public URI parameters accept `resources`, `user`, `agent`, and `session` scopes. Internal scopes such as `temp` and `queue` return `INVALID_URI`.

**Python SDK (Embedded / HTTP)**

```python
content = client.read("viking://resources/docs/api.md")
print(f"Content:\n{content}")
```

**HTTP API**

```
GET /api/v1/content/read?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking read viking://resources/docs/api.md
```

**Response**

```json
{
  "status": "ok",
  "result": "# API Documentation\n\nFull content of the file...",
  "time": 0.1
}
```

---

### write()

Update an existing file, or create a new one when `mode="create"`, and automatically refresh related semantics and vectors.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Existing file URI |
| content | str | Yes | - | New content to write |
| mode | str | No | `replace` | `replace`, `append`, or `create` |
| wait | bool | No | `false` | Wait for background semantic/vector refresh |
| timeout | float | No | `null` | Timeout in seconds when `wait=true` |

**Notes**

- `replace` and `append` require the file to exist; `create` targets a new file and returns `409 Conflict` when the path already exists. Directories are always rejected.
- `create` only accepts text-writable extensions: `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, `.ts`. Parent directories are created automatically.
- Derived semantic files cannot be written directly: `.abstract.md`, `.overview.md`, `.relations.json`.
- File content is updated before the API returns. `wait` only controls whether the call waits for semantic/vector refresh to finish.
- The public API no longer accepts `regenerate_semantics` or `revectorize`; write always refreshes related semantics and vectors.

**Python SDK (Embedded / HTTP)**

```python
result = client.write(
    "viking://resources/docs/api.md",
    "# Updated API\n\nFresh content.",
    mode="replace",
    wait=True,
)
print(result["root_uri"])
```

**HTTP API**

```
POST /api/v1/content/write
```

```bash
curl -X POST "http://localhost:1933/api/v1/content/write" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/docs/api.md",
    "content": "# Updated API\n\nFresh content.",
    "mode": "replace",
    "wait": true
  }'
```

**CLI**

```bash
openviking write viking://resources/docs/api.md \
  --content "# Updated API\n\nFresh content." \
  --wait
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/api.md",
    "root_uri": "viking://resources/docs",
    "context_type": "resource",
    "mode": "replace",
    "written_bytes": 29,
    "content_updated": true,
    "semantic_status": "complete",
    "vector_status": "complete",
    "queue_status": {
      "Semantic": {
        "processed": 1,
        "error_count": 0,
        "errors": []
      },
      "Embedding": {
        "processed": 2,
        "error_count": 0,
        "errors": []
      }
    }
  }
}
```

---

### ls()

List directory contents.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |
| simple | bool | No | False | Return only relative paths |
| recursive | bool | No | False | List all subdirectories recursively |
| output | str | No | `agent` | Output format: `agent` or `original` |
| abs_limit | int | No | 256 | Abstract length limit for `agent` output |
| show_all_hidden | bool | No | False | Include hidden files like `-a` |
| node_limit | int | No | 1000 | Maximum number of results |

**Entry Structure**

```python
{
    "name": "docs",           # File/directory name
    "size": 4096,             # Size in bytes
    "mode": 16877,            # File mode
    "modTime": "2024-01-01T00:00:00Z",  # ISO timestamp
    "isDir": True,            # True if directory
    "uri": "viking://resources/docs/",  # Viking URI
    "meta": {}                # Optional metadata
}
```

**Python SDK (Embedded / HTTP)**

```python
entries = client.ls("viking://resources/")
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['name']} - {type_str}")
```

**HTTP API**

```
GET /api/v1/fs/ls?uri={uri}&simple={bool}&recursive={bool}
```

```bash
# Basic listing
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/" \
  -H "X-API-Key: your-key"

# Simple path list
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/&simple=true" \
  -H "X-API-Key: your-key"

# Recursive listing
curl -X GET "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/&recursive=true" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking ls viking://resources/ [--simple] [--recursive]
```

**Response**

```json
{
  "status": "ok",
  "result": [
    {
      "name": "docs",
      "size": 4096,
      "mode": 16877,
      "modTime": "2024-01-01T00:00:00Z",
      "isDir": true,
      "uri": "viking://resources/docs/"
    }
  ],
  "time": 0.1
}
```

---

### tree()

Get directory tree structure.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |
| output | str | No | `agent` | Output format: `agent` or `original` |
| abs_limit | int | No | 256 | Abstract length limit for `agent` output |
| show_all_hidden | bool | No | False | Include hidden files like `-a` |
| node_limit | int | No | 1000 | Maximum number of results |
| level_limit | int | No | 3 | Maximum directory depth to traverse |

**Python SDK (Embedded / HTTP)**

```python
entries = client.tree("viking://resources/")
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['rel_path']} - {type_str}")
```

**HTTP API**

```
GET /api/v1/fs/tree?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/fs/tree?uri=viking://resources/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking tree viking://resources/my-project/
```

**Response**

```json
{
  "status": "ok",
  "result": [
    {
      "name": "docs",
      "size": 4096,
      "isDir": true,
      "rel_path": "docs/",
      "uri": "viking://resources/docs/"
    },
    {
      "name": "api.md",
      "size": 1024,
      "isDir": false,
      "rel_path": "docs/api.md",
      "uri": "viking://resources/docs/api.md"
    }
  ],
  "time": 0.1
}
```

---

### stat()

Get file or directory status information. For directories, returns the count of items under the directory.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |

**Python SDK (Embedded / HTTP)**

```python
info = client.stat("viking://resources/docs/api.md")
print(f"Size: {info['size']}")
print(f"Is directory: {info['isDir']}")

# For directories, returns item count
dir_info = client.stat("viking://resources/docs")
if dir_info.get('isDir'):
    print(f"Item count: {dir_info.get('count')}")
```

**HTTP API**

```
GET /api/v1/fs/stat?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/fs/stat?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking stat viking://resources/my-project/docs/api.md
openviking stat viking://resources/my-project/docs
```

**Response (File)**

```json
{
  "status": "ok",
  "result": {
    "name": "api.md",
    "size": 1024,
    "mode": 33188,
    "modTime": "2024-01-01T00:00:00Z",
    "isDir": false,
    "isLocked": false,
    "uri": "viking://resources/docs/api.md"
  },
  "time": 0.1
}
```

**Response (Directory)**

```json
{
  "status": "ok",
  "result": {
    "name": "docs",
    "size": 4096,
    "mode": 16877,
    "modTime": "2024-01-01T00:00:00Z",
    "isDir": true,
    "isLocked": false,
    "uri": "viking://resources/docs",
    "count": 42
  },
  "time": 0.1
}
```

The `isLocked` field reports whether the path is currently held by a path lock: the path itself has a valid lock (including an exact-path lock for the target), or any ancestor directory holds a TreeLock. Returns `false` when the LockManager is unavailable or the lookup fails, so callers can avoid attempting a write only to observe `ResourceBusyError`.

The `count` field (directories only) contains the estimated number of items (files and subdirectories) under this directory (from vector index).

---

### mkdir()

Create a directory.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI for the new directory |
| description | str | No | `null` | Initial directory description. When provided, it is written to `.abstract.md` and queued for L0 vectorization. |

**Python SDK (Embedded / HTTP)**

```python
client.mkdir("viking://resources/new-project/")
client.mkdir("viking://resources/new-project/", description="API docs directory")
```

**HTTP API**

```
POST /api/v1/fs/mkdir
```

```bash
curl -X POST http://localhost:1933/api/v1/fs/mkdir \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/new-project/",
    "description": "API docs directory"
  }'
```

**CLI**

```bash
openviking mkdir viking://resources/new-project/
openviking mkdir viking://resources/new-project/ --description "API docs directory"
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/new-project/"
  },
  "time": 0.1
}
```

---

### rm()

Remove file or directory. When removing directories recursively, returns the estimated number of items deleted.

`rm` is idempotent: removing a valid URI that does not exist still succeeds.
Invalid URI formats, unsupported schemes, and non-public scopes return `INVALID_URI`.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to remove |
| recursive | bool | No | False | Remove directory recursively |

**Python SDK (Embedded / HTTP)**

```python
# Remove single file
client.rm("viking://resources/docs/old.md")

# Remove directory recursively
result = client.rm("viking://resources/old-project/", recursive=True)
if 'estimated_deleted_count' in result:
    print(f"Deleted {result['estimated_deleted_count']} items")
```

**HTTP API**

```
DELETE /api/v1/fs?uri={uri}&recursive={bool}
```

```bash
# Remove single file
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://resources/docs/old.md" \
  -H "X-API-Key: your-key"

# Remove directory recursively
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://resources/old-project/&recursive=true" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking rm viking://resources/old.md [--recursive]
```

**Response (Single file)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/old.md"
  },
  "time": 0.1
}
```

**Response (Recursive delete)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/old-project/",
    "estimated_deleted_count": 42
  },
  "time": 0.1
}
```

The `estimated_deleted_count` field (for recursive deletes) contains the estimated number of items (files and directories) deleted (from vector index). The CLI will display this information in output.

---

### mv()

Move file or directory.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| from_uri | str | Yes | - | Source Viking URI |
| to_uri | str | Yes | - | Destination Viking URI |

**Python SDK (Embedded / HTTP)**

```python
client.mv(
    "viking://resources/old-name/",
    "viking://resources/new-name/"
)
```

**HTTP API**

```
POST /api/v1/fs/mv
```

```bash
curl -X POST http://localhost:1933/api/v1/fs/mv \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/old-name/",
    "to_uri": "viking://resources/new-name/"
  }'
```

**CLI**

```bash
openviking mv viking://resources/old-name/ viking://resources/new-name/
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/old-name/",
    "to": "viking://resources/new-name/"
  },
  "time": 0.1
}
```

---

### grep()

Search content by pattern.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to search in |
| pattern | str | Yes | - | Search pattern (regex) |
| case_insensitive | bool | No | False | Ignore case |
| exclude_uri | str | No | None | URI prefix to exclude from search |
| node_limit | int | No | None | Maximum number of results |
| level_limit | int | No | 5 | Maximum directory depth to traverse |

**Python SDK (Embedded / HTTP)**

```python
results = client.grep(
    "viking://resources/",
    "authentication",
    case_insensitive=True
)

print(f"Found {results['count']} matches")
for match in results['matches']:
    print(f"  {match['uri']}:{match['line']}")
    print(f"    {match['content']}")
```

**HTTP API**

```
POST /api/v1/search/grep
```

```bash
curl -X POST http://localhost:1933/api/v1/search/grep \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/",
    "pattern": "authentication",
    "case_insensitive": true
  }'
```

**CLI**

```bash
openviking grep viking://resources/ "authentication" [--ignore-case]
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "matches": [
      {
        "uri": "viking://resources/docs/auth.md",
        "line": 15,
        "content": "User authentication is handled by..."
      }
    ],
    "count": 1
  },
  "time": 0.1
}
```

---

### glob()

Match files by pattern.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| pattern | str | Yes | - | Glob pattern (e.g., `**/*.md`) |
| uri | str | No | "viking://" | Starting URI |
| node_limit | int | No | None | Maximum number of results |

**Python SDK (Embedded / HTTP)**

```python
# Find all markdown files
results = client.glob("**/*.md", "viking://resources/")
print(f"Found {results['count']} markdown files:")
for uri in results['matches']:
    print(f"  {uri}")

# Find all Python files
results = client.glob("**/*.py", "viking://resources/")
print(f"Found {results['count']} Python files")
```

**HTTP API**

```
POST /api/v1/search/glob
```

```bash
curl -X POST http://localhost:1933/api/v1/search/glob \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "pattern": "**/*.md",
    "uri": "viking://resources/"
  }'
```

**CLI**

```bash
openviking glob "**/*.md" [--uri viking://resources/]
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "matches": [
      "viking://resources/docs/api.md",
      "viking://resources/docs/guide.md"
    ],
    "count": 2
  },
  "time": 0.1
}
```

---

### link()

Create relations between resources.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| from_uri | str | Yes | - | Source URI |
| to_uris | str or List[str] | Yes | - | Target URI(s) |
| reason | str | No | "" | Reason for the link |

**Python SDK (Embedded / HTTP)**

```python
# Single link
client.link(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/",
    reason="Security best practices for authentication"
)

# Multiple links
client.link(
    "viking://resources/docs/api/",
    [
        "viking://resources/docs/auth/",
        "viking://resources/docs/errors/"
    ],
    reason="Related documentation"
)
```

**HTTP API**

```
POST /api/v1/relations/link
```

```bash
# Single link
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uris": "viking://resources/docs/security/",
    "reason": "Security best practices for authentication"
  }'

# Multiple links
curl -X POST http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/api/",
    "to_uris": ["viking://resources/docs/auth/", "viking://resources/docs/errors/"],
    "reason": "Related documentation"
  }'
```

**CLI**

```bash
openviking link viking://resources/docs/auth/ viking://resources/docs/security/ --reason "Security best practices"
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### relations()

Get relations for a resource.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |

**Python SDK (Embedded / HTTP)**

```python
relations = client.relations("viking://resources/docs/auth/")
for rel in relations:
    print(f"Related: {rel['uri']}")
    print(f"  Reason: {rel['reason']}")
```

**HTTP API**

```
GET /api/v1/relations?uri={uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/relations?uri=viking://resources/docs/auth/" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
openviking relations viking://resources/docs/auth/
```

**Response**

```json
{
  "status": "ok",
  "result": [
    {"uri": "viking://resources/docs/security/", "reason": "Security best practices"},
    {"uri": "viking://resources/docs/errors/", "reason": "Error handling"}
  ],
  "time": 0.1
}
```

---

### unlink()

Remove a relation.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| from_uri | str | Yes | - | Source URI |
| to_uri | str | Yes | - | Target URI to unlink |

**Python SDK (Embedded / HTTP)**

```python
client.unlink(
    "viking://resources/docs/auth/",
    "viking://resources/docs/security/"
)
```

**HTTP API**

```
DELETE /api/v1/relations/link
```

```bash
curl -X DELETE http://localhost:1933/api/v1/relations/link \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/auth/",
    "to_uri": "viking://resources/docs/security/"
  }'
```

**CLI**

```bash
openviking unlink viking://resources/docs/auth/ viking://resources/docs/security/
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/docs/auth/",
    "to": "viking://resources/docs/security/"
  },
  "time": 0.1
}
```

---

### export_ovpack

Export a resource tree as a `.ovpack` file.

#### 1. API Implementation Overview

Packages all resources under the specified URI into a `.ovpack` file for backup or migration. Requires ROOT or ADMIN permissions.

**Processing Flow**:
1. Verify user permissions
2. Traverse resources under the specified URI
3. Write content files and the OVPack manifest
4. Package into zip format (`.ovpack`)
5. Return as file stream

**Format Notes**:
- The exported ZIP stores user content unchanged under `<root>/files/` and internal metadata under `<root>/_ovpack/`.
- The manifest is stored at `<root>/_ovpack/manifest.json`.
- `entries[].path` is relative to the exported root; `""` means the root directory itself.
- File entries include `size` and `sha256`; `content_sha256` covers the sorted file list of `path`, `size`, and `sha256`.
- `_ovpack/index_records.jsonl` stores portable index scalar fields. With `include_vectors=true`, `_ovpack/dense.f32` stores a pure-dense float32 vector snapshot plus embedding metadata; vector indexes whose `VectorIndex.IndexType` is hybrid do not support vector snapshot export.
- Runtime fields such as `id`, `uri`, `account_id`, `created_at`, `updated_at`, and `active_count` are regenerated in the target environment and are not restored from the package.
- OVPack does not add package-size, file-count, or directory-depth limits; the practical limit comes from ZIP, the storage backend, and the runtime environment.

**Code Entry Points**:
- `openviking/server/routers/pack.py:export_ovpack` - HTTP router
- `openviking/service/pack_service.py` - Core service implementation
- `crates/ov_cli/src/handlers.rs:handle_export` - CLI handler

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | string | Yes | - | Viking URI to export |
| include_vectors | boolean | No | false | Include a pure-dense vector snapshot; hybrid index types are rejected |

**Permission Requirements**: ROOT or ADMIN

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/pack/export
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{
    "uri": "viking://resources/my-project/",
    "include_vectors": false
  }' \
  --output my-project.ovpack
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-admin-key")
client.initialize()

# Export to local file (HTTP SDK automatically handles download)
# Note: Export functionality is primarily used via CLI
```

**CLI**

```bash
# Export resource
ov export viking://resources/my-project/ ./exports/my-project.ovpack

# Export with a dense vector snapshot
ov export viking://resources/my-project/ ./exports/my-project.ovpack --include-vectors
```

**Response Example**

This endpoint directly returns a file stream (`Content-Type: application/zip`), does not return a JSON envelope.

---

### import_ovpack

Import a `.ovpack` file.

#### 1. API Implementation Overview

Imports a `.ovpack` file to a specified location for restoring or migrating data. Requires ROOT or ADMIN permissions.

**Processing Flow**:
1. Verify user permissions
2. Parse uploaded `.ovpack` file
3. Validate manifest metadata, paths, file and directory sets, file sizes, and checksums
4. Apply `on_conflict`
5. Import resources to target location and rebuild vectors

**Code Entry Points**:
- `openviking/server/routers/pack.py:import_ovpack` - HTTP router
- `openviking/service/pack_service.py` - Core service implementation
- `crates/ov_cli/src/handlers.rs:handle_import` - CLI handler

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| temp_file_id | string | Yes | - | Temporary upload file ID (obtained via [temp_upload](02-resources.md#temp_upload)) |
| parent | string | Yes | - | Target parent URI (import to this location) |
| on_conflict | string | No | fail | Conflict policy: `fail`, `overwrite`, or `skip` |
| vector_mode | string | No | auto | Vector handling: `auto`, `recompute`, or `require` |

**Permission Requirements**: ROOT or ADMIN

**Behavior Notes**:
- The API no longer accepts `vectorize` or `force`.
- `vector_mode=auto` restores a compatible dense snapshot when present, otherwise recomputes vectors. `recompute` always ignores package vectors. `require` fails unless a compatible dense snapshot is present.
- Dense snapshot compatibility checks compare embedding provider, model, input mode, query/document parameters, and dimensions.
- Session imports restore session files and do not trigger vectorization.
- `on_conflict=fail` returns a structured `409 CONFLICT` when the target root already exists.
- `on_conflict=overwrite` replaces the existing target root. `on_conflict=skip` keeps the existing target root and returns it without writing package contents. `skip` is root-level, not file-level.
- Packages without a manifest are rejected by default because they cannot provide content integrity guarantees.
- Packages with manifest entries are rejected if content files or directories are missing, extra files or directories are present, file sizes differ, per-file `sha256` differs, or `content_sha256` is missing or differs.
- Packages whose manifest `format_version` is not the current supported version are rejected.
- `.abstract.md` and `.overview.md` are restored as semantic sidecars. `.relations.json` and OVPack internals are excluded.
- Manifest `context_type`, when present in index scalar metadata, must match the final import path semantics.
- Top-level scope packages such as `viking://resources/` must be imported to `viking://`.
- OVPack does not add import package-size, file-count, or directory-depth limits; the practical limit comes from ZIP, the storage backend, and the runtime environment.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/pack/import
Content-Type: application/json
```

```bash
# Step 1: Upload .ovpack file
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./exports/my-project.ovpack" \
  | jq -r '.result.temp_file_id'
)

# Step 2: Import
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"on_conflict\": \"overwrite\",
    \"vector_mode\": \"auto\"
  }"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-admin-key")
client.initialize()

# Import .ovpack file (HTTP SDK automatically handles upload)
# Note: Import functionality is primarily used via CLI
```

**CLI**

```bash
# Import .ovpack file
ov import ./exports/my-project.ovpack viking://resources/imported/

# Explicit conflict policy
ov import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite

# Require restoring a compatible dense vector snapshot
ov import ./exports/my-project.ovpack viking://resources/imported/ --vector-mode require
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/imported/my-project/"
  },
  "telemetry": {
    "operation_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

**Conflict Error Example**

```json
{
  "status": "error",
  "error": {
    "code": "CONFLICT",
    "message": "Resource already exists at viking://resources/imported/my-project. Use on_conflict='overwrite' to replace it.",
    "details": {
      "resource": "viking://resources/imported/my-project"
    }
  }
}
```

---

### backup_ovpack

Back up public scope roots as a restore-only `.ovpack` file. The backup includes
`resources`, `user`, `agent`, and `session`; it excludes internal runtime data
such as `temp` and `queue`. Set `include_vectors=true` to include compatible
pure-dense vector snapshots; hybrid index types reject vector snapshot export.

```
POST /api/v1/pack/backup
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/backup \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"include_vectors":false}' \
  --output openviking-backup.ovpack
```

CLI:

```bash
ov backup ./backups/openviking.ovpack
ov backup ./backups/openviking.ovpack --include-vectors
```

---

### restore_ovpack

Restore a backup package created by `backup_ovpack` to the original public scope
roots. Regular import rejects backup packages. Vector handling follows
`vector_mode`; session files are restored without vectorization.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| temp_file_id | string | Yes | - | Temporary upload file ID |
| on_conflict | string | No | fail | Conflict policy: `fail`, `overwrite`, or `skip` |
| vector_mode | string | No | auto | Vector handling: `auto`, `recompute`, or `require` |

```
POST /api/v1/pack/restore
Content-Type: application/json
```

```bash
TEMP_FILE_ID=$(
  curl -s -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./backups/openviking.ovpack" \
  | jq -r '.result.temp_file_id'
)

curl -X POST http://localhost:1933/api/v1/pack/restore \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{\"temp_file_id\":\"$TEMP_FILE_ID\",\"on_conflict\":\"overwrite\",\"vector_mode\":\"auto\"}"
```

CLI:

```bash
ov restore ./backups/openviking.ovpack --on-conflict overwrite
ov restore ./backups/openviking.ovpack --on-conflict overwrite --vector-mode require
```

---

## Related Documentation

- [Viking URI](../concepts/04-viking-uri.md) - URI specification
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
- [Resources](02-resources.md) - Resource management
