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

Update an existing file and automatically refresh related semantics and vectors.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Existing file URI |
| content | str | Yes | - | New content to write |
| mode | str | No | `replace` | `replace` or `append` |
| wait | bool | No | `false` | Wait for background semantic/vector refresh |
| timeout | float | No | `null` | Timeout in seconds when `wait=true` |

**Notes**

- Only existing files are supported; directories are rejected.
- Derived semantic files cannot be written directly: `.abstract.md`, `.overview.md`, `.relations.json`.
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
    "semantic_updated": true,
    "vector_updated": true,
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
| node_limit | int | No | 1000 | Maximum number of nodes to return |
| limit | int | No | None | Alias for `node_limit` |

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
| node_limit | int | No | 1000 | Maximum number of nodes to return |
| limit | int | No | None | Alias for `node_limit` |
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

Get file or directory status information.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |

**Python SDK (Embedded / HTTP)**

```python
info = client.stat("viking://resources/docs/api.md")
print(f"Size: {info['size']}")
print(f"Is directory: {info['isDir']}")
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
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "name": "api.md",
    "size": 1024,
    "mode": 33188,
    "modTime": "2024-01-01T00:00:00Z",
    "isDir": false,
    "uri": "viking://resources/docs/api.md"
  },
  "time": 0.1
}
```

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

Remove file or directory.

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
client.rm("viking://resources/old-project/", recursive=True)
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

**Response**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/old.md"
  },
  "time": 0.1
}
```

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
| node_limit | int | No | None | Maximum number of nodes to search |
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
| node_limit | int | No | None | Maximum number of matches to return |

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

## Related Documentation

- [Viking URI](../concepts/04-viking-uri.md) - URI specification
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
- [Resources](02-resources.md) - Resource management
