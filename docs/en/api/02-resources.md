# Resources

Resources are external knowledge that agents can reference. This guide covers how to add, manage, and retrieve resources.

## Supported Formats

| Format | Extensions | Processing |
|--------|------------|------------|
| PDF | `.pdf` | Text and image extraction |
| Markdown | `.md` | Native support |
| HTML | `.html`, `.htm` | Cleaned text extraction |
| Plain Text | `.txt` | Direct import |
| JSON/YAML | `.json`, `.yaml`, `.yml` | Structured parsing |
| Code | `.py`, `.js`, `.ts`, `.go`, `.java`, etc. | Syntax-aware parsing |
| Images | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | VLM description |
| Video | `.mp4`, `.mov`, `.avi` | Frame extraction + VLM |
| Audio | `.mp3`, `.wav`, `.m4a` | Transcription |
| Documents | `.docx` | Text extraction |
| Feishu/Lark | URL (`*.feishu.cn`, `*.larksuite.com`) | Cloud document parsing via `lark-oapi` |

## Processing Pipeline

```
Input -> Parser -> TreeBuilder -> AGFS -> SemanticQueue -> Vector Index
```

1. **Parser**: Extracts content based on file type
2. **TreeBuilder**: Creates directory structure
3. **AGFS**: Stores files in virtual file system
4. **SemanticQueue**: Generates L0/L1 asynchronously
5. **Vector Index**: Indexes for semantic search

## API Reference

### add_resource()

Add a resource to the knowledge base.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| path | str | Yes | - | SDK/CLI: local path, directory path, or URL. Raw HTTP: remote URL only |
| temp_file_id | str | No | None | Upload ID returned by `POST /api/v1/resources/temp_upload` for raw HTTP local file ingestion |
| to | str | No | None | Target Viking URI (must be in `resources` scope; cannot be used with `parent`) |
| parent | str | No | None | Target parent URI (must exist and be a directory; cannot be used with `to`) |
| reason | str | No | "" | Why this resource is being added (improves search relevance) |
| instruction | str | No | "" | Special processing instructions |
| wait | bool | No | False | Wait for semantic processing to complete |
| timeout | float | No | None | Timeout in seconds (only used when wait=True) |
| watch_interval | float | No | 0 | Watch interval (minutes). >0 enables/updates watch; <=0 disables watch. Only takes effect when `to` is provided |

**Trailing slash semantics of `to` (applies after the resource is fetched; archives are extracted first)**

- If the resource is a **file**:
  - `to` ends with `/`: treat `to` as a directory; final location is `to/<source filename>`
  - `to` does not end with `/`: treat `to` as a file; final location is `to`
- If the resource is a **directory**:
  - `to` ends with `/`: treat `to` as a directory; final location is `to/<source directory name>/`
  - `to` does not end with `/`: treat `to` as a directory; map the directory contents into `to` (no extra directory layer)
- If `to == viking://resources` and the request would map a file to a file or map directory contents into `viking://resources` (no trailing slash), the server returns an error and asks you to change `to` (e.g. add `/` or use a more specific path).

**How local files and directories work**

- Python SDK and CLI accept local file and directory paths directly. In HTTP mode they automatically upload local files before calling the server API.
- Raw HTTP callers should think in two categories:
  - Remote source: pass `path` directly, for example `https://example.com/doc.pdf`
  - Local file: call `POST /api/v1/resources/temp_upload` first, then pass the returned `temp_file_id`
  - Local directory: zip it first, upload the `.zip` file, then pass the returned `temp_file_id`
- `POST /api/v1/resources` does not accept direct host filesystem paths such as `./guide.md`, `/tmp/guide.md`, or `/tmp/my-dir/`.

**Incremental Updates**

When you call `add_resource()` repeatedly for the same resource URI, the system performs an incremental update instead of rebuilding everything from scratch:

- **Trigger**: `to` is provided and already exists in the knowledge base.
- **High-level idea**: each ingestion first builds a temporary resource tree from the new input. During asynchronous semantic processing, the temporary tree is compared against the existing tree at `target`, and only the changed parts are re-processed and synchronized.
- **Incremental behavior in the semantic stage**:
  - **Unchanged files**: reuse existing L0 summaries and vector index records; skip vectorization.
  - **Changed files**: regenerate summaries and vector index entries.
  - **Directory-level L0/L1 (abstract/overview)**: if the child set and their change status are unchanged, reuse existing results and skip vectorization; otherwise recompute and update.
- **Filesystem + index sync**: after the semantic DAG finishes, a top-down diff is applied from the temporary tree to `target` to synchronize additions, deletions, and updates. Vector store records are kept consistent: deletions remove corresponding vectors, while moves/overwrites update vector records’ URI mapping, completing an incremental update of both the resource tree and the semantic index.

**Python SDK (Embedded / HTTP)**

```python
result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

client.wait_processed()
```

**HTTP API**

```
POST /api/v1/resources
```

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/guide.md",
    "reason": "User guide documentation"
  }'
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --reason "User guide documentation"
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/documents/guide.md",
    "source_path": "./documents/guide.md",
    "errors": []
  },
  "time": 0.1
}
```

**Example: Add from URL**

**Python SDK (Embedded / HTTP)**

```python
result = client.add_resource(
    "https://example.com/api-docs.md",
    to="viking://resources/external/",
    reason="External API documentation"
)
client.wait_processed()
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/api-docs.md",
    "to": "viking://resources/external/",
    "reason": "External API documentation",
    "wait": true
  }'
```

**CLI**

```bash
openviking add-resource https://example.com/api-docs.md --to viking://resources/external/ --reason "External API documentation"
```

**Example: Add a Local File with Raw HTTP**

When you call the HTTP API directly, upload local files first and then use `temp_file_id`.

```bash
# Step 1: upload the local file
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./documents/guide.md' \
  | jq -r '.result.temp_file_id'
)

# Step 2: add the uploaded file
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"reason\": \"User guide documentation\",
    \"wait\": true
  }"
```

**Example: Add a Local Directory with Raw HTTP**

When you call the HTTP API directly, zip the directory yourself first. CLI and SDK do this automatically for you.

```bash
# Step 1: zip the local directory
cd ./documents
zip -r /tmp/guide.zip ./guide

# Step 2: upload the zip file
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@/tmp/guide.zip' \
  | jq -r '.result.temp_file_id'
)

# Step 3: add the uploaded directory archive
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"reason\": \"Import local directory\",
    \"wait\": true
  }"
```

**Example: Add Feishu/Lark Cloud Documents**

[Feishu](https://www.feishu.cn) (飞书) and its international version [Lark](https://www.larksuite.com) are widely used for documentation in Chinese tech companies. OpenViking can directly import cloud documents by URL.

Supported document types:

| Type | URL Pattern |
|------|-------------|
| Documents | `https://*.feishu.cn/docx/{id}` |
| Wiki pages | `https://*.feishu.cn/wiki/{token}` |
| Spreadsheets | `https://*.feishu.cn/sheets/{token}` |
| Bitable | `https://*.feishu.cn/base/{token}` |

> **Setup**: Install the optional dependency: `pip install 'openviking[bot-feishu]'`
>
> Configure credentials via `ov.conf` (see [Configuration](../../guides/01-configuration.md#feishu)) or environment variables:
> ```bash
> export FEISHU_APP_ID="cli_xxx"
> export FEISHU_APP_SECRET="xxx"
> ```

**Python SDK (Embedded / HTTP)**

```python
# Import a Feishu document
result = client.add_resource(
    "https://example.feishu.cn/docx/doxcnABC123",
    reason="Project design document"
)
client.wait_processed()

# Import a wiki page (auto-resolves to underlying type)
client.add_resource("https://example.feishu.cn/wiki/wikiXYZ")

# Import a spreadsheet
client.add_resource("https://example.feishu.cn/sheets/shtcn456")
```

**CLI**

```bash
# Import a Feishu document
openviking add-resource "https://example.feishu.cn/docx/doxcnABC123" --reason "Project design document"

# Import a wiki page
openviking add-resource "https://example.feishu.cn/wiki/wikiXYZ"

# Incremental update to an existing target
openviking add-resource "https://example.feishu.cn/docx/doxcnABC123" --to viking://resources/design-doc
```

**Example: Wait for Processing**

**Python SDK (Embedded / HTTP)**

```python
# Option 1: Wait inline
result = client.add_resource("./documents/guide.md", wait=True)
print(f"Queue status: {result['queue_status']}")

# Option 2: Wait separately (for batch processing)
client.add_resource("./file1.md")
client.add_resource("./file2.md")
client.add_resource("./file3.md")

status = client.wait_processed()
print(f"All processed: {status}")
```

**HTTP API**

```bash
# Wait inline
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"path": "https://example.com/guide.md", "wait": true}'

# Wait separately after batch
curl -X POST http://localhost:1933/api/v1/system/wait \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{}'
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --wait
```

**Example: Watch for Updates (watch_interval)**

`watch_interval` is in minutes and periodically triggers re-processing for the specified target URI:

- `watch_interval > 0`: create (or reactivate and update) a watch task for the `target`
- `watch_interval <= 0`: disable (deactivate) the watch task for the `target`
- watch tasks are only managed when `target` / CLI `--to` is provided

If there is already an active watch task for the same `target`, submitting another request with `watch_interval > 0` returns a conflict error. Disable it first (`watch_interval = 0`) and then set a new interval.

**Python SDK (Embedded / HTTP)**

```python
client.add_resource(
    "./documents/guide.md",
    target="viking://resources/documents/guide.md",
    watch_interval=60,
)
```

**HTTP API**

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/guide.md",
    "target": "viking://resources/documents/guide.md",
    "watch_interval": 60
  }'
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --to viking://resources/documents/guide.md --watch-interval 60

# Disable watch
openviking add-resource ./documents/guide.md --to viking://resources/documents/guide.md --watch-interval 0
```

---

### export_ovpack()

Export a resource tree as a `.ovpack` file.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to export |
| to | str | Yes | - | Target file path |

**Python SDK (Embedded / HTTP)**

```python
path = client.export_ovpack(
    "viking://resources/my-project/",
    "./exports/my-project.ovpack"
)
print(f"Exported to: {path}")
```

**HTTP API**

```
POST /api/v1/pack/export
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/my-project/",
    "to": "./exports/my-project.ovpack"
  }'
```

**CLI**

```bash
openviking export viking://resources/my-project/ ./exports/my-project.ovpack
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "file": "./exports/my-project.ovpack"
  },
  "time": 0.1
}
```

---

### import_ovpack()

Import a `.ovpack` file.

**SDK / CLI parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| file_path | str | Yes | - | Local `.ovpack` file path |
| parent | str | Yes | - | Target parent URI |
| force | bool | No | False | Overwrite existing resources |
| vectorize | bool | No | True | Trigger vectorization after import |

**Raw HTTP request body**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| temp_file_id | str | Yes | - | Upload ID returned by `POST /api/v1/resources/temp_upload` |
| parent | str | Yes | - | Target parent URI |
| force | bool | No | False | Overwrite existing resources |
| vectorize | bool | No | True | Trigger vectorization after import |

**Python SDK (Embedded / HTTP)**

```python
uri = client.import_ovpack(
    "./exports/my-project.ovpack",
    "viking://resources/imported/",
    force=True,
    vectorize=True
)
print(f"Imported to: {uri}")

client.wait_processed()
```

**HTTP API**

```
POST /api/v1/pack/import
```

```bash
# Step 1: upload the local ovpack file
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/my-project.ovpack' \
  | jq -r '.result.temp_file_id'
)

# Step 2: import using temp_file_id
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"force\": true,
    \"vectorize\": true
  }"
```

**CLI**

```bash
openviking import ./exports/my-project.ovpack viking://resources/imported/ --force
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/imported/my-project/"
  },
  "time": 0.1
}
```

---

## Managing Resources

### List Resources

**Python SDK (Embedded / HTTP)**

```python
# List all resources
entries = client.ls("viking://resources/")

# List with details
for entry in entries:
    type_str = "dir" if entry['isDir'] else "file"
    print(f"{entry['name']} - {type_str}")

# Simple path list
paths = client.ls("viking://resources/", simple=True)
# Returns: ["project-a/", "project-b/", "shared/"]

# Recursive listing
all_entries = client.ls("viking://resources/", recursive=True)
```

**HTTP API**

```
GET /api/v1/fs/ls?uri={uri}&simple={bool}&recursive={bool}
```

```bash
# List all resources
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
# List all resources
openviking ls viking://resources/

# Simple path list
openviking ls viking://resources/ --simple

# Recursive listing
openviking ls viking://resources/ --recursive
```

**Response**

```json
{
  "status": "ok",
  "result": [
    {
      "name": "project-a",
      "size": 4096,
      "isDir": true,
      "uri": "viking://resources/project-a/"
    }
  ],
  "time": 0.1
}
```

---

### Read Resource Content

**Python SDK (Embedded / HTTP)**

```python
# L0: Abstract
abstract = client.abstract("viking://resources/docs/")

# L1: Overview
overview = client.overview("viking://resources/docs/")

# L2: Full content
content = client.read("viking://resources/docs/api.md")
```

**HTTP API**

```bash
# L0: Abstract
curl -X GET "http://localhost:1933/api/v1/content/abstract?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"

# L1: Overview
curl -X GET "http://localhost:1933/api/v1/content/overview?uri=viking://resources/docs/" \
  -H "X-API-Key: your-key"

# L2: Full content
curl -X GET "http://localhost:1933/api/v1/content/read?uri=viking://resources/docs/api.md" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
# L0: Abstract
openviking abstract viking://resources/docs/

# L1: Overview
openviking overview viking://resources/docs/

# L2: Full content
openviking read viking://resources/docs/api.md
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

### Move Resources

**Python SDK (Embedded / HTTP)**

```python
client.mv(
    "viking://resources/old-project/",
    "viking://resources/new-project/"
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
    "from_uri": "viking://resources/old-project/",
    "to_uri": "viking://resources/new-project/"
  }'
```

**CLI**

```bash
openviking mv viking://resources/old-project/ viking://resources/new-project/
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "from": "viking://resources/old-project/",
    "to": "viking://resources/new-project/"
  },
  "time": 0.1
}
```

---

### Delete Resources

**Python SDK (Embedded / HTTP)**

```python
# Delete single file
client.rm("viking://resources/docs/old.md")

# Delete directory recursively
client.rm("viking://resources/old-project/", recursive=True)
```

**HTTP API**

```
DELETE /api/v1/fs?uri={uri}&recursive={bool}
```

```bash
# Delete single file
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://resources/docs/old.md" \
  -H "X-API-Key: your-key"

# Delete directory recursively
curl -X DELETE "http://localhost:1933/api/v1/fs?uri=viking://resources/old-project/&recursive=true" \
  -H "X-API-Key: your-key"
```

**CLI**

```bash
# Delete single file
openviking rm viking://resources/docs/old.md

# Delete directory recursively
openviking rm viking://resources/old-project/ --recursive
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

### Create Links

**Python SDK (Embedded / HTTP)**

```python
# Link related resources
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

### Get Relations

**Python SDK (Embedded / HTTP)**

```python
relations = client.relations("viking://resources/docs/auth/")
for rel in relations:
    print(f"{rel['uri']}: {rel['reason']}")
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

### Remove Links

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

## Best Practices

### Organize by Project

```
viking://resources/
+-- project-a/
|   +-- docs/
|   +-- specs/
|   +-- references/
+-- project-b/
|   +-- ...
+-- shared/
    +-- common-docs/
```

## Related Documentation

- [Retrieval](06-retrieval.md) - Search resources
- [File System](03-filesystem.md) - File operations
- [Context Types](../concepts/02-context-types.md) - Resource concept
