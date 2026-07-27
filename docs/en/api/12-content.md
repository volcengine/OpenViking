# Content

The Content API reads L0/L1/L2 content, writes text, and maintains semantic and vector indexes for stored content.

## API Reference

### abstract()

Read L0 abstract (~100 tokens summary).

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI (must be a directory) |


**Python SDK**

```python
abstract = client.abstract("viking://resources/docs/")
print(f"Abstract: {abstract}")
# Output: "Documentation for the project API, covering authentication, endpoints..."
```

**TypeScript SDK**

```typescript
const abstract = await client.abstract("viking://resources/docs/");
console.log(abstract);
```

**Go SDK**

```go
abstract, err := client.Abstract(ctx, "viking://resources/docs/")
if err != nil {
    return err
}
fmt.Println(abstract)
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


**Python SDK**

```python
overview = client.overview("viking://resources/docs/")
print(f"Overview:\n{overview}")
```

**TypeScript SDK**

```typescript
const overview = await client.overview("viking://resources/docs/");
console.log(overview);
```

**Go SDK**

```go
overview, err := client.Overview(ctx, "viking://resources/docs/")
if err != nil {
    return err
}
fmt.Println(overview)
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
| raw | bool | No | false | Return raw stored content without memory-field cleanup. HTTP API only (Python SDK does not expose it yet). |

**Notes**

- `read()` accepts file URIs only. Passing an existing directory URI returns `INVALID_ARGUMENT` (`400`), not `NOT_FOUND`. This error carries a structured `details` payload — `details.expected` is `"file"`, `details.actual` is `"directory"`, and `details.resource` is the offending URI (present on the HTTP path) — so clients can detect a file-vs-directory mismatch programmatically (for example, fall back to `list`) instead of string-matching the message.
- Public URI parameters accept `resources` and `user` scopes. For session files, use `viking://user/{user_id}/sessions/{session_id}` or the backward-compatible `viking://session/{session_id}` alias. Internal scopes such as `temp` and `queue` return `INVALID_URI`.


**Python SDK**

```python
content = client.read("viking://resources/docs/api.md")
print(f"Content:\n{content}")
```

**TypeScript SDK**

```typescript
const content = await client.read("viking://resources/docs/api.md", 0, -1);
console.log(content);
```

**Go SDK**

```go
content, err := client.Read(ctx, "viking://resources/docs/api.md", 0, -1)
if err != nil {
    return err
}
fmt.Println(content)
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
| uri | str | Yes | - | File URI to write. For `mode="create"`, the file must not already exist |
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


**Python SDK**

```python
result = client.write(
    "viking://resources/docs/api.md",
    "# Updated API\n\nFresh content.",
    mode="replace",
    wait=True,
)
print(result["root_uri"])
```

**TypeScript SDK**

```typescript
await client.write("viking://resources/docs/new.md", "# New document\n", { wait: true });
```

**Go SDK**

```go
result, err := client.Write(
    ctx,
    "viking://resources/docs/api.md",
    "# Updated API\n\nFresh content.",
    &openviking.WriteOptions{
        Mode: "replace",
        Wait: true,
    },
)
if err != nil {
    return err
}
fmt.Println(result["root_uri"])
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

### download()

Download a file as raw bytes. This is intended for images, PDFs, and other non-text content. The response uses `application/octet-stream` and returns the filename through `Content-Disposition`.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uri` | string | Yes | File URI to download |

**HTTP API**

```http
GET /api/v1/content/download?uri={uri}
```

```bash
curl --get http://localhost:1933/api/v1/content/download \
  -H "X-API-Key: your-key" \
  --data-urlencode "uri=viking://resources/images/logo.png" \
  --output logo.png
```

**Response**

On success, the endpoint returns HTTP `200` with the raw file bytes instead of the standard JSON envelope:

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Disposition: attachment; filename*=UTF-8''logo.png

<binary body>
```

The public SDKs and CLI do not currently expose a dedicated raw-byte download method, so this section shows only the HTTP tab.

---

### set_tags()

Set explicit `k=v` tags used by retrieval filters. `replace` replaces existing tags, while `append` adds tags. When the target is a directory, `recursive=true` applies the update to files below it.

**Python SDK**

```python
result = client.set_tags(
    "viking://resources/project/",
    ["team=search", "env=prod"],
    mode="replace",
    recursive=True,
)
```

**TypeScript SDK**

```typescript
const result = await client.setTags(
  "viking://resources/project/",
  ["team=search", "env=prod"],
  { mode: "replace", recursive: true },
);
```

**Go SDK**

```go
result, err := client.SetTags(
    ctx,
    "viking://resources/project/",
    []string{"team=search", "env=prod"},
    &openviking.SetTagsOptions{Mode: "replace", Recursive: true},
)
```

**HTTP API**

```http
POST /api/v1/content/set_tags
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/content/set_tags \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri":"viking://resources/project/",
    "tags":["team=search","env=prod"],
    "mode":"replace",
    "recursive":true
  }'
```

`POST /api/v1/fs/attrs/set_tags` is an equivalent compatibility path currently used by the Python, TypeScript, and Go SDKs and the CLI.

**CLI**

```bash
ov set-tags viking://resources/project/ \
  --tags team=search,env=prod \
  --mode replace \
  --recursive
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/project/",
    "updated_uris": [
      "viking://resources/project/guide.md"
    ],
    "root_uri": "viking://resources/project/",
    "context_type": "resource",
    "tags": [
      "team=search",
      "env=prod"
    ],
    "mode": "replace",
    "success_count": 1,
    "skipped_count": 0,
    "failed_count": 0,
    "tags_updated": true
  }
}
```

`updated_uris` contains the semantic record URIs actually updated. For recursive directory updates, `success_count`, `skipped_count`, and `failed_count` summarize all targets.

---

### reindex()

Reindex semantic and/or vector artifacts for existing content already stored in OpenViking. This is an operational maintenance API intended for scenarios such as embedding model changes, VLM changes, vector store rebuild, or post-upgrade repair of existing indexes.

This API operates on existing `viking://...` content. It does not import new files. For normal ingestion, use [Resources](02-resources.md).

**Authentication**

- HTTP endpoint: requires admin/root role when authentication is enabled. In `api_key` mode, use an admin key for tenant content; a raw root key cannot access tenant-scoped data.
- Python embedded mode: uses the current service context
- Python HTTP client / CLI: sends the current authenticated identity

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to reindex |
| mode | str | No | `vectors_only` | Reindex mode: `vectors_only`, `semantic_and_vectors`, or `prune_orphans` |
| wait | bool | No | `true` | Whether to wait for completion |
| dry_run | bool | No | `false` | Only valid with `mode="prune_orphans"`; report orphan vector records without deleting them |

The HTTP request body rejects unknown fields. `uri` may use OpenViking path variables accepted by other content APIs; it is resolved before validation.

**Supported URI scopes**

- `viking://`
- `viking://user`
- `viking://user/<user_id>`
- `viking://resources`
- `viking://resources/...`
- `viking://user/<user_id>/memories/...`
- `viking://user/<user_id>/skills`
- `viking://user/<user_id>/skills/<skill_name>`

Session namespaces are not supported by `reindex()`. Requests for
`viking://session/...` or `viking://user/<user_id>/sessions/...` are rejected;
when reindexing a broader user namespace, session subtrees are skipped.

**Modes**

- `vectors_only`: rebuilds vector-store records from currently recoverable source data without rewriting `.abstract.md` or `.overview.md`
- `semantic_and_vectors`: regenerates semantic artifacts first, then rebuilds vectors from the refreshed semantic outputs
- `prune_orphans`: deletes vector-store records under the requested URI whose source files no longer exist in the filesystem. With `dry_run=true`, it only reports how many records would be deleted.

For `resource` and `skill`, `semantic_and_vectors` refreshes directory/file semantic artifacts, including `.abstract.md` and `.overview.md`. For `memory`, it rebuilds the current persisted memory subtree semantics and vectors, but it does not replay historical extraction order.

For `semantic_and_vectors`, semantic generation and vector rebuilding are sequenced by the reindex executor. The semantic refresh step does not enqueue its own background vectorization work; vectors are rebuilt by the reindex step so `wait=true` reflects the reindex operation itself.

For `prune_orphans`, source existence is checked against the filesystem. If an entire directory is missing, vector records for files and semantic sidecars below that directory, such as `.abstract.md` and `.overview.md`, are pruned together. `dry_run` is rejected for other modes.

**Python SDK**

```python
result = client.reindex(
    uri="viking://resources",
    mode="vectors_only",
    wait=True,
)
print(result)
```

```python
result = client.reindex(
    uri="viking://user/default/skills",
    mode="semantic_and_vectors",
    wait=False,
)
print(result["status"])
```

```python
result = client.reindex(
    uri="viking://resources",
    mode="prune_orphans",
    dry_run=True,
)
print(result["would_delete_records"])
```

**TypeScript SDK**

```typescript
console.log(await client.reindex("viking://resources/docs/"));
```

**Go SDK**

```go
result, err := client.Reindex(ctx, "viking://resources", &openviking.ReindexOptions{
    Mode: "vectors_only",
    Wait: true,
})
if err != nil {
    return err
}
fmt.Println(result["status"])
```

```go
result, err := client.Reindex(ctx, "viking://resources", &openviking.ReindexOptions{
    Mode: "prune_orphans",
    Wait: true,
    DryRun: true,
})
if err != nil {
    return err
}
fmt.Println(result["would_delete_records"])
```

**HTTP API**

```
POST /api/v1/content/reindex
```

There is no `/api/v1/maintenance/reindex` endpoint. Use `/api/v1/content/reindex`.

```bash
curl -X POST http://localhost:1933/api/v1/content/reindex \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -d '{
    "uri": "viking://resources",
    "mode": "prune_orphans",
    "wait": true,
    "dry_run": true
  }'
```

**CLI**

```bash
openviking reindex viking://resources --mode vectors_only
```

```bash
openviking reindex viking://user/default/skills --mode semantic_and_vectors --wait false
```

```bash
openviking reindex viking://resources --mode prune_orphans --dry-run
```

**Synchronous response (`wait=true`)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "status": "completed",
    "object_type": "resource",
    "scanned_records": 120,
    "rebuilt_records": 118,
    "deleted_records": 0,
    "would_delete_records": 0,
    "unsupported_records": 2,
    "failed_records": 0,
    "duration_ms": 1284,
    "warnings": []
  },
  "time": 0.1
}
```

**Asynchronous response (`wait=false`)**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources",
    "mode": "vectors_only",
    "object_type": "resource",
    "status": "accepted",
    "task_id": "task_xxx"
  },
  "time": 0.1
}
```

Poll the returned task through the task API:

```bash
curl -X GET http://localhost:1933/api/v1/tasks/task_xxx \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default"
```

Reindex background tasks use `task_type="admin_reindex"` and `resource_id` equal to the requested `uri`, so they can also be listed with:

```text
GET /api/v1/tasks?task_type=admin_reindex&resource_id=viking://resources
```

Task records are persisted under `/local/{account_id}/_system/tasks/{user_id}/{task_id}.json` and can be queried after restart.

**Result fields**

| Field | Description |
|-------|-------------|
| status | `completed` for synchronous completion, `accepted` for background execution |
| uri | Requested URI after path-variable resolution |
| object_type | Inferred target type, such as `resource`, `skill`, `memory`, `user_namespace`, `skill_namespace`, or `global_namespace` |
| mode | Effective reindex mode |
| scanned_records | Number of records or semantic sources considered |
| rebuilt_records | Number of vector records successfully rebuilt |
| deleted_records | Number of vector records deleted by `prune_orphans`; `0` for `dry_run=true` |
| would_delete_records | Number of vector records that would be deleted by `prune_orphans` in dry-run mode |
| unsupported_records | Number of records skipped because no usable vector source was available |
| failed_records | Number of records that failed while rebuilding |
| duration_ms | Synchronous run duration in milliseconds |
| warnings | Recoverable per-record warnings |
| task_id | Background task ID, present only when `wait=false` |

**Behavior notes**

- `vectors_only` and `semantic_and_vectors` are non-destructive. They use rebuild/upsert behavior and do not require dropping the vector collection first.
- `prune_orphans` is destructive unless `dry_run=true`: it removes vector records whose source files no longer exist.
- `viking://` reindex fans out to supported top-level namespaces and excludes `session`.
- Namespace reindex operations such as `viking://user` propagate to supported child content types.
- `vectors_only` is the right mode when only the embedding model or vector index needs to be refreshed.
- `semantic_and_vectors` is the right mode when semantic artifacts themselves must be regenerated before re-vectorization.
- `prune_orphans` is the right mode when the filesystem has been changed outside normal APIs and the vector store may still contain records for deleted paths.
- Only one reindex task can run for the same URI and owner at a time. A concurrent request for the same target returns a conflict.
- For resource files, text files can use file content when no summary is available. Non-text files require a generated summary or existing vector record fallback; otherwise they are counted as unsupported.

**Current limitations**

- Reindex uses the best currently recoverable source inputs. It is not guaranteed to replay the exact historical embedding input byte-for-byte in every case.
- Memory semantic reindex is based on the currently persisted memory tree. It does not reconstruct the original chronological memory-extraction pipeline.

---

## Related Documentation

- [File System](03-filesystem.md) - directory and file operations
- [Retrieval](06-retrieval.md) - semantic and pattern search
- [Background Tasks](17-tasks.md) - track asynchronous reindex tasks
