# Content

The Content API reads L0/L1/L2 content, writes text, and maintains semantic and vector indexes for stored content.

## API Reference

### abstract()

Read the L0 abstract (an approximately 100-token summary), excluding the OKF header.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI (must be a directory) |


**Python SDK**

```python
abstract = client.abstract(uri="viking://resources/docs/")
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

Read the L1 overview for a directory, excluding the OKF header.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI (must be a directory) |


**Python SDK**

```python
overview = client.overview(uri="viking://resources/docs/")
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

Read the complete text of an L0, L1, or L2 file.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI (e.g. `viking://resources/docs/api.md`) or a 32-character hex vector record `id` (returned by `stat()`) |
| offset | int | No | 0 | Starting line number (0-indexed) |
| limit | int | No | -1 | Number of lines to read, `-1` means read to end |
| raw | bool | No | false | Return raw stored content without memory-field cleanup. HTTP API only (Python SDK does not expose it yet). |

**Notes**

- `read()` accepts file URIs only. Passing an existing directory URI returns `INVALID_ARGUMENT` (`400`), not `NOT_FOUND`. This error carries a structured `details` payload — `details.expected` is `"file"`, `details.actual` is `"directory"`, and `details.resource` is the offending URI (present on the HTTP path) — so clients can detect a file-vs-directory mismatch programmatically (for example, fall back to `list`) instead of string-matching the message.
- Instead of a Viking URI, you may pass the 32-character hex `id` returned by `stat()` for a file. The server looks up the URI via the vector index and applies the same permission checks. Because indexing is asynchronous, a newly returned ID might not be resolvable immediately; lookup also fails if the corresponding vector record has been deleted. In both cases, the server returns `NOT_FOUND` and indicates that the data may not have been indexed yet or may have been deleted.
- Public URI parameters accept `resources` and `user` scopes. For session files, use `viking://user/{user_id}/sessions/{session_id}` or the backward-compatible `viking://session/{session_id}` alias. Internal scopes such as `temp` and `queue` return `INVALID_URI`.


**Python SDK**

```python
content = client.read(uri="viking://resources/docs/api.md")
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

Write a file and automatically refresh related semantics and vectors.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | File URI to write |
| content | str | Yes | - | New content to write |
| mode | str | No | `replace` | `replace` overwrites an existing file or creates a missing file; `append` appends to an existing file or creates a missing file; `create` creates only a missing file and returns `409 Conflict` if it already exists |
| wait | bool | No | `false` | Wait for background semantic/vector refresh |
| timeout | float | No | `null` | Timeout in seconds when `wait=true` |
| tags | string[] | No | Unset | Explicit retrieval tags for the written file, for example `["team=search", "env=prod"]` |
| tag_mode | string | No | `replace` | Tag update mode: `replace` overwrites tags, `append` merges by key, and `clear` removes existing tags without requiring `tags` |

**Notes**

- `replace` and `append` create a missing target file. `append` uses the supplied content as the initial file content in that case. `create` targets only a missing file and returns `409 Conflict` when the path already exists. Directories are always rejected.
- Explicit `create` only accepts text-writable extensions: `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, `.ts`. Parent directories are created automatically for every write mode.
- Existing `.abstract.md` and `.overview.md` bodies may be updated, but public APIs cannot create them. A body-only request preserves stored OKF metadata; a full-OKF request must match the stored metadata. Unknown metadata fields are silently dropped. A sidecar body write rebuilds only the directory's existing L0/L1 vectors and does not regenerate semantics.
- File content is updated before the API returns. `wait` only controls whether the call waits for semantic/vector refresh to finish.
- The public API no longer accepts `regenerate_semantics` or `revectorize`; write automatically schedules related semantic and vector processing.
- Parent L0/L1 refreshes for resource writes are best-effort: a parent lock conflict skips that directory refresh while preserving the file write and its own summary/vector work. Skipping L0/L1 persistence also skips directory vector updates; a later refresh is not guaranteed. Locks on the written file itself still raise conflicts. Contention detected before enqueueing returns `semantic_status: "skipped"`; skips during background execution are logged, and `wait=true` does not guarantee updated parent summaries.
- When non-empty `tags` are supplied, tags are included in the file's first vector upsert rather than updated after processing. Omitting `tags`, or using `tags: []` with `tag_mode: "replace"`, preserves existing tags. Use `tag_mode: "clear"` to remove all existing tags; `clear` ignores any supplied tag values.


**Python SDK**

```python
result = client.write(
    uri="viking://resources/docs/api.md",
    content="# Updated API\n\nFresh content.",
    mode="replace",
    options={"tags": ["team=search", "env=prod"], "tag_mode": "replace"},
)
print(result["root_uri"])
```

**TypeScript SDK**

```typescript
await client.write("viking://resources/docs/new.md", "# New document\n", {
  tags: ["team=search", "env=prod"],
  tagMode: "replace",
});
```

**Go SDK**

```go
result, err := client.Write(
    ctx,
    "viking://resources/docs/api.md",
    "# Updated API\n\nFresh content.",
    &openviking.WriteOptions{
        Mode: "replace",
        Tags: []string{"team=search", "env=prod"},
        TagMode: "replace",
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
    "tags": ["team=search", "env=prod"],
    "tag_mode": "replace"
  }'
```

**CLI**

```bash
openviking write viking://resources/docs/api.md \
  --content "# Updated API\n\nFresh content." \
  --tags team=search,env=prod \
  --tag-mode replace
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

### batch_write()

Write multiple files below one Resource or Memory directory, then refresh the affected semantic and vector indexes once after all writes finish.

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `root_uri` | string | Yes | - | Existing Resource or Memory directory containing every target |
| `operations` | array | Yes | - | File writes to validate and apply |
| `wait` | boolean | No | `true` | Wait for semantic/vector refresh |
| `timeout` | number | No | `null` | Refresh timeout in seconds when `wait=true` |
| `telemetry` | boolean/object | No | `false` | Include operation telemetry |

Each operation contains:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `uri` | string | Yes | Target file URI below `root_uri` |
| `content` | string | Conditional | UTF-8 text; exactly one of `content` and `content_base64` is required |
| `content_base64` | string | Conditional | Base64-encoded bytes; not supported for Memory targets |
| `mode` | string | No | `replace` (default), `append`, `create`, or `upsert` |

**Notes**

- A request supports at most 256 operations, 8 MiB per file, and 16 MiB total.
- All targets must be files below `root_uri`, use the same context type, and have unique canonical URIs.
- Resource targets may use any safe file extension; Memory targets retain the text extension allowlist and do not accept binary content.
- `replace`, `append`, and `create` match `write()` semantics. `upsert` replaces an existing file or creates a missing file.
- The batch acquires exact locks for all target files before validating file state and writing. Writes to disjoint files in the same directory can proceed concurrently; overlapping writes and parent-directory deletion or moves still conflict. Semantic processing starts after all writes finish and the locks are released, refreshing the affected `.overview.md` and `.abstract.md` files together.
- Resource parent refreshes use the same best-effort behavior as `write()`: L0/L1 lock conflicts skip the directory refresh and its vector updates while preserving file writes and file processing. A later refresh is not guaranteed.
- An underlying I/O failure can still leave writes completed earlier in the batch visible.
- Existing `.abstract.md` and `.overview.md` bodies may be replaced or appended. OpenViking preserves and validates protected OKF metadata and rebuilds only the directory's existing L0/L1 vectors for these operations.
- In the response body, `semantic_status` (`queued`, `complete`, `deferred`, or `skipped`) reports the directory aggregation status; it is `skipped` if any directory encounters contention before enqueueing. Meanwhile, `vector_status` reports vector maintenance for changed files.

**Python SDK**

```python
result = client.batch_write(
    root_uri="viking://resources/wiki",
    operations=[
        {
            "uri": "viking://resources/wiki/new.md",
            "content": "# New page\n",
            "mode": "upsert",
        },
        {
            "uri": "viking://resources/wiki/existing.md",
            "content": "# Updated page\n",
            "mode": "upsert",
        },
    ],
    wait=False,
)
```

**HTTP API**

```
POST /api/v1/content/batch-write
```

```bash
curl -X POST http://localhost:1933/api/v1/content/batch-write \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "root_uri": "viking://resources/wiki",
    "operations": [
      {
        "uri": "viking://resources/wiki/new.md",
        "content": "# New page\n",
        "mode": "upsert"
      }
    ],
    "wait": false
  }'
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "root_uri": "viking://resources/wiki",
    "created": ["viking://resources/wiki/new.md"],
    "updated": [],
    "unchanged": [],
    "semantic_status": "complete",
    "vector_status": "complete",
    "queue_status": {
      "Semantic": {
        "processed": 1,
        "error_count": 0,
        "errors": []
      }
    }
  }
}
```

The TypeScript and Go SDKs and the CLI do not currently expose batch write directly.

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

**CLI**

```bash
ov get viking://resources/images/logo.png ./logo.png
```

**Response**

On success, the endpoint returns HTTP `200` with the raw file bytes instead of the standard JSON envelope:

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Disposition: attachment; filename*=UTF-8''logo.png

<binary body>
```

`ov get <uri> <local-path>` downloads through the HTTP API above and writes the file to a local path. The Python, TypeScript, and Go SDKs do not currently expose a dedicated raw-byte download method.

---

### set_tags()

Set explicit `k=v` tags used by retrieval filters. `replace` replaces existing tags, while `append` adds tags. When the target is a directory, `recursive=true` applies the update to files below it.

**Python SDK**

```python
result = client.set_tags(
    uri="viking://resources/project/",
    tags=["team=search", "env=prod"],
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

Validate and repair semantic and/or vector artifacts for existing content already stored in OpenViking. Resource and skill targets use incremental RFV (Request / Formal / Vector) convergence by default: unchanged file fingerprints, directory L0/L1 visible-body fingerprints, and request scalars are skipped. Use `force=true` for an unconditional rebuild.

This API operates on existing `viking://...` content. It does not import new files. For normal ingestion, use [Resources](02-resources.md).

**Authentication**

- In `api_key` mode, shared `viking://resources/...` targets require an admin key. A regular user key may reindex only its own `viking://user/<user_id>/...` namespace, including the equivalent `viking://~/...` home alias. A root key cannot access tenant-scoped data APIs.
- Python HTTP client / CLI: sends the current authenticated identity

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| uri | str | Yes | - | Viking URI to reindex |
| mode | str | No | `vectors_only` | Reindex mode: `vectors_only` or `semantic_and_vectors` |
| wait | bool | No | `true` | Whether to wait for completion |
| force | bool | No | `false` | Skip fingerprint equality and reprocess every available resource/skill file and directory level in scope |
| recursive | bool | No | `true` | Whether to process descendants recursively; both resource/skill rebuild modes honor this option |
| tags | list[str] | No | `null` | Write tags to every successfully rebuilt vector record. Omitting tags, or passing an empty list with `replace`, preserves existing tags |
| tag_mode | str | No | `replace` | Tag write mode: `replace`, `append`, or `clear`; `clear` removes existing tags without requiring `tags` |

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

- `vectors_only`: compares current source MD5 values with vector-record MD5 values through RFV and rebuilds only missing or stale L0/L1/L2 records; it does not rewrite `.abstract.md` or `.overview.md`
- `semantic_and_vectors`: uses the same RFV state and `ContextUpdatePlan` to drive semantic repair and L0/L1/L2 updates, without a second manual vector scan

For `resource` and `skill`, `semantic_and_vectors` refreshes directory/file semantic artifacts, including `.abstract.md` and `.overview.md`. For `memory`, it rebuilds the current persisted memory subtree semantics and vectors, but it does not replay historical extraction order.

Resource/skill `vectors_only` and `semantic_and_vectors` share one F traversal and one narrow-field V inventory. Each source is read once during state resolution and that result is reused while executing the plan. `force=true` bypasses MD5 equality but still performs completeness and scope checks.

For a resource/skill directory, `recursive=false` limits both modes to the target directory's own L0/L1 records; descendants are neither scanned nor rebuilt. Namespace containers have no index records of their own, so namespace targets reject `recursive=false` instead of silently becoming a no-op. Memory remains on the existing reindex path.

When the F snapshot is complete, the RFV diff identifies records present in V but absent from F as orphans and removes them during the same reindex. Deletion fails closed when the F snapshot is incomplete.

When non-empty `tags` are provided, tags are included in the same upsert as each vector record produced by reindex; reindex does not call `set_tags` afterwards. Directory and namespace reindex operations apply tags to successfully rebuilt directory L0/L1 and leaf L2 records. `replace` overwrites existing tags, while `append` merges by key. `replace` with an empty tag list is a no-op. `clear` removes existing tags and does not require `tags`; if values are supplied with `clear`, they are ignored.

Subtree reindex is not transactional. Records skipped because no semantic source is available, or records whose embedding fails, do not receive the new tags.

**Python SDK**

```python
result = client.reindex(
    uri="viking://resources",
    mode="vectors_only",
    wait=False,
    options={
        "tags": ["team=search", "env=prod"],
        "tag_mode": "replace",
    },
)
print(result)
```

```python
result = client.reindex(
    uri="viking://user/default/skills",
    mode="semantic_and_vectors",
    force=True,
    wait=False,
)
print(result["status"])
```

**TypeScript SDK**

```typescript
console.log(await client.reindex("viking://resources/docs/", {
  force: true,
  tags: ["team=search"],
  tagMode: "append",
}));
```

**Go SDK**

With non-`nil` `ReindexOptions`, omitting `Wait` uses Go's zero value `false`;
only `opts=nil` applies the SDK default `wait=true`.

```go
result, err := client.Reindex(ctx, "viking://resources", &openviking.ReindexOptions{
    Mode: "vectors_only",
    Force: true,
    Tags: []string{"team=search"},
    TagMode: "replace",
})
if err != nil {
    return err
}
fmt.Println(result["status"])
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
    "mode": "vectors_only",
    "wait": false,
    "force": true,
    "tags": ["team=search", "env=prod"],
    "tag_mode": "replace"
  }'
```

**CLI**

```bash
openviking reindex viking://resources --mode vectors_only \
  --force --tags team=search,env=prod --tag-mode replace
```

Use `--tag-mode clear` without `--tags` to clear existing tags:

```bash
openviking reindex viking://resources --mode vectors_only --tag-mode clear
```

```bash
openviking reindex viking://user/default/skills --mode semantic_and_vectors --wait false
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
| deleted_records | Number of orphan vector records confirmed and deleted by the RFV diff |
| unsupported_records | Number of records skipped because no usable vector source was available |
| failed_records | Number of records that failed while rebuilding |
| duration_ms | Synchronous run duration in milliseconds |
| warnings | Recoverable per-record warnings |
| task_id | Background task ID, present only when `wait=false` |

**Behavior notes**

- `vectors_only` and `semantic_and_vectors` are non-destructive. They use rebuild/upsert behavior and do not require dropping the vector collection first.
- `viking://` reindex fans out to supported top-level namespaces and excludes `session`.
- Namespace reindex operations such as `viking://user` propagate to supported child content types.
- `vectors_only` is the right mode when only the embedding model or vector index needs to be refreshed.
- `semantic_and_vectors` is the right mode when semantic artifacts themselves must be regenerated before re-vectorization.
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
