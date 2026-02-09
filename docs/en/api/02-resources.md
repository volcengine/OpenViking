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
| path | str | Yes | - | Local file path, directory path, or URL |
| target | str | No | None | Target Viking URI (must be in `resources` scope) |
| reason | str | No | "" | Why this resource is being added (improves search relevance) |
| instruction | str | No | "" | Special processing instructions |
| wait | bool | No | False | Wait for semantic processing to complete |
| timeout | float | No | None | Timeout in seconds (only used when wait=True) |

**Python SDK**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

client.wait_processed()
client.close()
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
    "path": "./documents/guide.md",
    "reason": "User guide documentation"
  }'
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

**Python SDK**

```python
result = client.add_resource(
    "https://example.com/api-docs.md",
    target="viking://resources/external/",
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
    "target": "viking://resources/external/",
    "reason": "External API documentation",
    "wait": true
  }'
```

**Example: Wait for Processing**

**Python SDK**

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
  -d '{"path": "./documents/guide.md", "wait": true}'

# Wait separately after batch
curl -X POST http://localhost:1933/api/v1/system/wait \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{}'
```

---

### export_ovpack()

Export a resource tree as a `.ovpack` file.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to export |
| to | str | Yes | - | Target file path |

**Python SDK**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

path = client.export_ovpack(
    "viking://resources/my-project/",
    "./exports/my-project.ovpack"
)
print(f"Exported to: {path}")

client.close()
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

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| file_path | str | Yes | - | Local `.ovpack` file path |
| parent | str | Yes | - | Target parent URI |
| force | bool | No | False | Overwrite existing resources |
| vectorize | bool | No | True | Trigger vectorization after import |

**Python SDK**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

uri = client.import_ovpack(
    "./exports/my-project.ovpack",
    "viking://resources/imported/",
    force=True,
    vectorize=True
)
print(f"Imported to: {uri}")

client.wait_processed()
client.close()
```

**HTTP API**

```
POST /api/v1/pack/import
```

```bash
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "file_path": "./exports/my-project.ovpack",
    "parent": "viking://resources/imported/",
    "force": true,
    "vectorize": true
  }'
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

**Python SDK**

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

**Python SDK**

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

**Python SDK**

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

**Python SDK**

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

**Python SDK**

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

**Python SDK**

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

**Python SDK**

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
