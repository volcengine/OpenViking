# File System

OpenViking provides Unix-like file system operations for managing context.

<a id="webdav"></a><a id="webdav-phase-1"></a>

## API Reference

<a id="abstract"></a><a id="overview"></a><a id="read"></a><a id="write"></a>

### ls()

List directory contents.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |
| simple | bool | No | False | Return only relative paths |
| recursive | bool | No | False | List all subdirectories recursively |
| output | str | No | HTTP: `agent`; SDKs: `original` | Output format: `agent` or `original` |
| abs_limit | int | No | 256 | Abstract length limit for `agent` output |
| show_all_hidden | bool | No | False | Include hidden files like `-a` |
| node_limit | int | No | 1000 | Maximum number of results |
| sort_by | str | No | None | Sort directories and files within their groups by `name` or `mtime` before applying `node_limit`; directories remain first |
| sort_order | str | No | `asc` | Sort direction: `asc` or `desc` |
| extra_fields | list[str] | No | None | Extra fields to include: `locked`, `id`, `count` |
| tags | string[] | No | Unset | Return only entries matching every supplied `k=v` retrieval tag |

`tags` uses AND semantics and is applied before `node_limit`. Tags are included for filtered responses; for an unfiltered response, request `include_tags=true` (CLI: `-f tags`).

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

If the caller can read the parent directory but cannot read one of its direct
children, `ls` still returns a name placeholder without size, modification
time, abstract, or storage metadata:

```python
{
    "name": "restricted",
    "isDir": True,
    "uri": "viking://resources/restricted",
    "access": "denied"
}
```

Content operations such as `stat` and `read` return HTTP 403 `PermissionDenied` for
that URI. Recursive listing retains the inaccessible directory itself but does
not descend into it, and search results omit unreadable content. This
discoverable-name behavior applies only to the shared
`viking://resources` namespace; private user and peer namespaces retain their
existing hiding rules.


**Python HTTP SDK**

```python
entries = client.ls(
    uri="viking://resources/",
    node_limit=200,
    sort_by="mtime",
    sort_order="desc",
)
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['name']} - {type_str}")
```

**TypeScript SDK**

```typescript
const entries = await client.list("viking://resources/docs/", { simple: true });
console.log(entries);
```

**Go SDK**

```go
entries, err := client.List(ctx, "viking://resources/", nil)
if err != nil {
    return err
}
for _, entry := range entries {
    fmt.Println(entry)
}
```

**HTTP API**

```
GET /api/v1/fs/ls?uri={uri}&simple={bool}&recursive={bool}&tags={k=v}&include_tags={bool}
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
openviking ls viking://resources/ [--simple] [--recursive] [--tags team=search,env=prod] [-f FIELDS]
openviking tree viking://resources/my-project/ [--simple] [--tags team=search,env=prod] [-f FIELDS]
openviking glob "**/*.md" [--uri viking://resources/] [--simple] [--tags team=search,env=prod] [-f FIELDS]
```

`-f`/`--fields` accepts a comma-separated list of columns to display (ps `-o` style), producing a column-aligned table with a header row. Available fields: `name`, `uri`, `path`, `type`, `size`, `mode`, `mtime`, `locked`, `id`, `count`, `abstract`, `tags`. Combining `--simple` with `-f` outputs comma-separated values (no header, no tree indentation), one entry per line — suitable for scripting pipelines. When `--simple` is used without `-f`, the previous behavior (bare URI per line) is preserved.


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
| output | str | No | HTTP: `agent`; SDKs: `original` | Output format: `agent` or `original` |
| abs_limit | int | No | HTTP: 256; SDKs: 128 | Abstract length limit for `agent` output |
| show_all_hidden | bool | No | False | Include hidden files like `-a` |
| node_limit | int | No | 1000 | Maximum number of results |
| level_limit | int | No | 3 | Maximum directory depth to traverse |
| extra_fields | list[str] | No | None | Extra fields to include: `locked`, `id`, `count` |
| tags | string[] | No | Unset | Retain only nodes matching every supplied `k=v` retrieval tag |

`tags` uses AND semantics and is applied before `node_limit`. Tags are included for filtered responses; for an unfiltered response, request `include_tags=true` (CLI: `-f tags`).


**Python HTTP SDK**

```python
entries = client.tree(uri="viking://resources/")
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['rel_path']} - {type_str}")
```

**TypeScript SDK**

```typescript
const tree = await client.tree("viking://resources/docs/", { nodeLimit: 100 });
console.log(tree);
```

**Go SDK**

```go
entries, err := client.Tree(ctx, "viking://resources/", nil)
if err != nil {
    return err
}
for _, entry := range entries {
    fmt.Println(entry["rel_path"], entry["isDir"])
}
```

**HTTP API**

```
GET /api/v1/fs/tree?uri={uri}&tags={k=v}&include_tags={bool}
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
| uri | str | Yes | - | Viking URI (e.g. `viking://resources/docs/api.md`) or a 32-character hex vector record `id` |


**Python HTTP SDK**

```python
info = client.stat(uri="viking://resources/docs/api.md")
print(f"Size: {info['size']}")
print(f"Is directory: {info['isDir']}")

# For directories, returns item count
dir_info = client.stat(uri="viking://resources/docs")
if dir_info.get('isDir'):
    print(f"Item count: {dir_info.get('count')}")
```

**TypeScript SDK**

```typescript
const metadata = await client.stat("viking://resources/docs/api.md");
console.log(metadata);
```

**Go SDK**

```go
info, err := client.Stat(ctx, "viking://resources/docs/api.md")
if err != nil {
    return err
}
fmt.Println(info["size"], info["isDir"])
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
    "id": "a1b2c3d4e5f678901234567890abcdef",
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

The `id` field (files only) is the deterministic vector record primary key in VikingDB, computed as `md5(f"{account_id}:{uri}")` for level 2 (regular file) records. This value matches the `id` field in the vector collection schema and can be used to cross-reference vector records without an additional lookup. The field is omitted for directories because a directory may have multiple vector records across semantic levels (L0 abstract, L1 overview). Because indexing is asynchronous, a newly returned ID might not be resolvable immediately; lookup by ID can also fail after its vector record is deleted. In either case, `stat(id)` returns `NOT_FOUND` with a reason indicating that the data may not have been indexed yet or may have been deleted.

The `count` field (directories only) contains the estimated number of items (files and subdirectories) under this directory (from vector index).

---

### attrs()

Get logical extended attributes for a file or directory.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI |


**Python SDK (HTTP)**

```python
attrs = client.attrs(uri="viking://resources/docs/api.md")
print(attrs["attrs"]["tags"])
```

**TypeScript SDK**

```typescript
const attributes = await client.attrs("viking://resources/docs/api.md");
console.log(attributes);
```

**Go SDK**

```go
attrs, err := client.Attrs(ctx, "viking://resources/docs/api.md")
if err != nil {
    return err
}
metadata := attrs["attrs"].(map[string]any)
fmt.Println(metadata["tags"])
```

**HTTP API**

```
GET /api/v1/fs/attrs?uri={uri}
POST /api/v1/fs/attrs/set_tags
```

```bash
curl -X GET "http://localhost:1933/api/v1/fs/attrs?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"

curl -X POST "http://localhost:1933/api/v1/fs/attrs/set_tags" \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"uri":"viking://resources/docs","tags":["team=search"],"mode":"append","recursive":true}'
```

**CLI**

```bash
openviking attrs get viking://resources/docs/api.md
openviking attrs get viking://resources/docs/api.md tags
openviking attrs get viking://user/alice/memories/experiences/foo.md memory.resource_refs
openviking attrs set-tags viking://resources/docs/api.md --tags team=search,env=prod
openviking attrs set-tags viking://resources/docs --tags team=search --mode append --recursive
```

Directory targets update the directory semantic records; `recursive=true` also updates existing descendant files and directory semantic records.


**Response (Resource)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/docs/api.md",
    "context_type": "resource",
    "attrs": {
      "tags": ["team=search", "env=prod"]
    }
  }
}
```

**Response (Memory)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://user/alice/memories/experiences/foo.md",
    "context_type": "memory",
    "attrs": {
      "memory": {
        "memory_type": "experiences",
        "name": "foo",
        "tags": ["ui"],
        "resource_refs": ["viking://resources/docs/api.md"]
      },
      "tags": ["team=search"]
    }
  }
}
```

`attrs.memory` is parsed from `MEMORY_FIELDS` metadata with content removed. `attrs.tags` is the explicit retrieval tag list used by `attrs set-tags` and search filters.

---

### mkdir()

Create a directory.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI for the new directory |
| description | str | No | `null` | Initial directory description. When omitted, the directory name is used as the default L0; when provided, this description is used. Both forms write `.abstract.md` and queue L0 vectorization. |


**Python HTTP SDK**

```python
client.mkdir(uri="viking://resources/new-project/")
client.mkdir(uri="viking://resources/new-project/", description="API docs directory")
```

**TypeScript SDK**

```typescript
await client.mkdir("viking://resources/docs/guides/", "Project guides");
```

**Go SDK**

```go
if err := client.Mkdir(ctx, "viking://resources/new-project/", "API docs directory"); err != nil {
    return err
}
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


**Python HTTP SDK**

```python
# Remove single file
client.rm(uri="viking://resources/docs/old.md")

# Remove directory recursively
client.rm(uri="viking://resources/old-project/", recursive=True)
```

**TypeScript SDK**

```typescript
await client.remove("viking://resources/docs/old.md", { wait: true });
```

**Go SDK**

```go
err := client.Remove(ctx, "viking://resources/old-project/", &openviking.RemoveOptions{
    Recursive: true,
})
if err != nil {
    return err
}
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

When deleting `viking://resources/...`, the response may include `memory_cleanup`, indicating that user memories referencing that resource URI were cleaned up before deletion.

---

### cp()

Copy a file or directory to a new Viking URI. The source remains unchanged. Existing vector records under the source URI are copied and rewritten for the destination, so the copied content does not need to be parsed, described by a VLM, or embedded again.

The destination parent directory must already exist, and the destination itself must not exist. Copying a directory requires `recursive=true` (or `-r` in the CLI). The destination cannot equal the source or be inside the source directory tree.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| from_uri | str | Yes | - | Source Viking URI |
| to_uri | str | Yes | - | Destination Viking URI, including the new file or directory name |
| recursive | bool | No | False | Required when the source is a directory |

**HTTP API**

```
POST /api/v1/fs/cp
```

```bash
# Copy one file
curl -X POST http://localhost:1933/api/v1/fs/cp \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs/guide.md",
    "to_uri": "viking://resources/archive/guide-copy.md",
    "recursive": false
  }'

# Copy a directory recursively
curl -X POST http://localhost:1933/api/v1/fs/cp \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "from_uri": "viking://resources/docs",
    "to_uri": "viking://resources/docs-backup",
    "recursive": true
  }'
```

**CLI**

```bash
# Copy one file
ov cp viking://resources/docs/guide.md viking://resources/archive/guide-copy.md

# Copy a directory recursively
ov cp -r viking://resources/docs viking://resources/docs-backup
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "operation_id": "61ec2a80bf5f46a28aa3497fbdcb56dd",
    "operation": "copy",
    "from": "viking://resources/docs/guide.md",
    "to": "viking://resources/archive/guide-copy.md",
    "recursive": false,
    "phase": "completed",
    "files_created": 1,
    "vectors": {
      "scanned": 3,
      "written": 3,
      "deleted": 0,
      "restored": 0,
      "batches": 1
    },
    "semantic_root_uri": "viking://resources/archive",
    "semantic_status": "queued"
  }
}
```

`semantic_status: "queued"` means the copy has already committed and the destination parent's overview and abstract will be rebuilt asynchronously from summaries available at the destination. The API does not wait for that refresh. A refresh enqueue failure may return `semantic_status: "failed"` and `semantic_error`; it does not roll back the completed file and vector copy.

Common errors include `NOT_FOUND` when the source or destination parent is missing, `CONFLICT` when the destination already exists or a path lock is busy, `FAILED_PRECONDITION` when a directory is copied without `recursive=true`, and `INVALID_ARGUMENT` for invalid source/destination relationships.

---

### mv()

Move file or directory.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| from_uri | str | Yes | - | Source Viking URI |
| to_uri | str | Yes | - | Destination Viking URI |


**Python HTTP SDK**

```python
client.mv(
    from_uri="viking://resources/old-name/",
    to_uri="viking://resources/new-name/",
)
```

**TypeScript SDK**

```typescript
await client.move(
  "viking://resources/docs/old.md",
  "viking://resources/docs/new.md",
);
```

**Go SDK**

```go
if err := client.Move(ctx, "viking://resources/old-name/", "viking://resources/new-name/"); err != nil {
    return err
}
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

<a id="grep"></a><a id="glob"></a>

<a id="export_ovpack"></a><a id="import_ovpack"></a><a id="backup_ovpack"></a><a id="restore_ovpack"></a>

## Related Documentation

- [Viking URI](../concepts/04-viking-uri.md) - URI specification
- [Context Layers](../concepts/03-context-layers.md) - L0/L1/L2
- [Resources](02-resources.md) - Resource management
