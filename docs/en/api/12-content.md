# Content

The Content API reads L0/L1/L2 content, writes text, and maintains semantic and vector indexes for stored content.

## API Reference

### abstract()

Read the L0 abstract, excluding the OKF header. Directory abstracts have a default limit of 256 characters; this is not a token count.

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
ov abstract viking://resources/docs/
```


**Response**

```json
{
  "status": "ok",
  "result": "Documentation for the project API, covering authentication, endpoints..."
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
ov overview viking://resources/docs/
```


**Response**

```json
{
  "status": "ok",
  "result": "## docs/\n\nContains API documentation and guides..."
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
| raw | bool | No | false | Skip default sanitization and MEMORY_FIELDS cleanup, and attempt placeholder restoration. In Python, use `client.read_raw(uri)`. |

**Notes**

- `read()` accepts file URIs only. Passing an existing directory URI returns `INVALID_ARGUMENT` (`400`), not `NOT_FOUND`. This error carries a structured `details` payload — `details.expected` is `"file"`, `details.actual` is `"directory"`, and `details.resource` is the offending URI (present on the HTTP path) — so clients can detect a file-vs-directory mismatch programmatically (for example, fall back to `list`) instead of string-matching the message.
- Instead of a Viking URI, you may pass the 32-character hex `id` returned by `stat()` for a file. The server looks up the URI via the vector index and applies the same permission checks. Because indexing is asynchronous, a newly returned ID might not be resolvable immediately; lookup also fails if the corresponding vector record has been deleted. In both cases, the server returns `NOT_FOUND` and indicates that the data may not have been indexed yet or may have been deleted.
- Public URI parameters accept `resources`, `user`, and `agent` scopes. For session files, use `viking://user/{user_id}/sessions/{session_id}/messages.jsonl` or the current user's `viking://~/sessions/{session_id}/messages.jsonl` home alias. The old `viking://session/...` scope and internal scopes such as `temp` and `queue` return `INVALID_URI`.
- Sensitive information handling: default reads (`raw=false`) preserve existing placeholders and sanitize remaining registered plaintext values; `raw=true` skips default sanitization and returns the restored content. Restoration affects only the response, not stored files; see [Privacy Configs](../concepts/13-privacy.md).

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
ov read viking://resources/docs/api.md
```


**Response**

```json
{
  "status": "ok",
  "result": "# API Documentation\n\nFull content of the file..."
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
| mode | str | No | `replace` | `replace` overwrites an existing file or creates a missing file; `append` appends to an existing file or creates a missing file; `create` is a compatibility alias for `replace` |
| wait | bool | No | `false` | Wait for background semantic/vector refresh |
| timeout | float | No | None | Deprecated: accepted for compatibility and ignored; configure HTTP timeouts on the client |
| tags | string[] | No | Unset | Explicit retrieval tags for the written file, for example `["team=search", "env=prod"]` |
| tag_mode | string | No | `replace` | Tag update mode: `replace` overwrites tags, `append` merges by key, and `clear` removes existing tags without requiring `tags` |
| resolve_conflicts | bool | No | false | Resolve conflicts |

**Notes**

- `replace`, `append`, and the `create` compatibility alias create a missing target file. `append` uses the supplied content as the initial file content in that case; `create` otherwise behaves exactly like `replace`. Directories are always rejected.
- Creating a missing file with any mode only accepts text-writable extensions: `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, `.ts`. Parent directories are created automatically for every write mode.
- Existing `.abstract.md` and `.overview.md` bodies may be updated, but public APIs cannot create them. A body-only request preserves stored OKF metadata; a full-OKF request must match the stored metadata. Unknown metadata fields are silently dropped. A sidecar body write rebuilds only the directory's existing L0/L1 vectors and does not regenerate semantics.
- File content is updated before the API returns. `wait` only controls whether the call waits for semantic/vector refresh to finish.
- Once file content is committed, a later semantic/vector enqueue failure does not roll it back. The request returns an error; retry the same write to resubmit derived maintenance. Explicit writes always recommit their final bytes, so retries remain safe while the vector index lags the file.
- The public API no longer accepts `regenerate_semantics` or `revectorize`; write automatically schedules related semantic and vector processing.
- Parent L0/L1 refreshes for resource writes are best-effort: a parent lock conflict skips that directory refresh while preserving the file write and its own summary/vector work. Skipping L0/L1 persistence also skips directory vector updates; a later refresh is not guaranteed. Locks on the written file itself still raise conflicts. Contention detected before enqueueing returns `semantic_status: "skipped"`; skips during background execution are logged, and `wait=true` does not guarantee updated parent summaries.
- When non-empty `tags` are supplied, tags are included in the file's first vector upsert rather than updated after processing. Omitting `tags`, or using `tags: []` with `tag_mode: "replace"`, preserves existing tags. Use `tag_mode: "clear"` to remove all existing tags; `clear` ignores any supplied tag values.
- `resolve_conflicts=true` indicates that the edit is intended to resolve conflicts.


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
ov write viking://resources/docs/api.md \
  --content $'# Updated API\n\nFresh content.' \
  --tags team=search,env=prod \
  --tag-mode replace
```


**Response when `wait=true` and refresh completes**

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
| timeout | float | No | None | Deprecated: accepted for compatibility and ignored; configure HTTP timeouts on the client |
| `telemetry` | boolean/object | No | `false` | Include operation telemetry |
| `processing_mode` | string | No | `semantic_and_vectors` | One processing mode for the whole batch: `semantic_and_vectors` or `vectors_only` |
| `defer_processing` | boolean | No | `false` | Save files without scheduling semantic or vector work; the caller schedules later processing |

Each operation contains:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `uri` | string | Yes | Target file URI below `root_uri` |
| `content` | string | Conditional | UTF-8 text; exactly one of `content` and `content_base64` is required |
| `content_base64` | string | Conditional | Base64-encoded bytes; not supported for Memory targets |
| `mode` | string | No | `replace` (default), `append`, `create`, or `upsert` |
| `tags` | string[] | No | - | Explicit retrieval tags for this file |
| `tag_mode` | string | No | `replace` | Tag write mode for this file: `replace`, `append`, or `clear` |
| `acl` | object | No | - | Direct ACL for this file; requires manage permission on the file |

**Notes**

- Operation count, individual file size, and total content size have no application-level quotas.
- All targets must be files below `root_uri`, use the same context type, and have unique canonical URIs.
- Resource targets may use any safe file extension; Memory targets retain the text extension allowlist and do not accept binary content.
- `create`, `replace`, and `upsert` are compatible spellings for setting the requested final body: they overwrite an existing file and create a missing one. `append` appends when the file exists and creates it with the supplied body when it does not.
- `processing_mode` applies to the whole batch; operations cannot mix semantic modes. `vectors_only` maintains changed-file vectors without refreshing parent semantics.
- `tags`, `tag_mode`, and `acl` apply per operation. Tags travel with the file's first vector upsert; `clear` does not require `tags` and ignores supplied tag values.
- The batch acquires exact locks for all target files before validating file state and writing. Writes to disjoint files in the same directory can proceed concurrently; overlapping writes and parent-directory deletion or moves still conflict. Without deferral, asynchronous processing retains the file locks until the associated work settles, refreshing the affected `.overview.md` and `.abstract.md` files together.
- Resource parent refreshes use the same best-effort behavior as `write()`: L0/L1 lock conflicts skip the directory refresh and its vector updates while preserving file writes and file processing. A later refresh is not guaranteed.
- An underlying I/O failure can still leave writes completed earlier in the batch visible.
- Existing `.abstract.md` and `.overview.md` bodies may be replaced or appended. OpenViking preserves and validates protected OKF metadata and rebuilds only the directory's existing L0/L1 vectors for these operations.
- In the response body, `semantic_status` (`queued`, `complete`, `deferred`, or `skipped`) reports grouped parent-directory aggregation. It is `skipped` only when no parent group was enqueued; one busy parent does not hide work enqueued for another. `vector_status` reports vector maintenance for changed files.
- If formal content commits but asynchronous refresh enqueue fails, the body is kept. Re-run with the final body using `replace`, `create`, or `upsert`; do not blindly replay an already-successful `append`, because it would append twice.

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
    wait=True,
)
```

**TypeScript SDK**

```typescript
const result = await client.batchWrite("viking://resources/wiki", [
  {
    uri: "viking://resources/wiki/new.md",
    content: "# New page\n",
    mode: "upsert",
  },
]);
console.log(result);
```

**Go SDK**

```go
content := "# New page\n"
result, err := client.BatchWrite(ctx, "viking://resources/wiki", []openviking.BatchWriteOperation{
    {URI: "viking://resources/wiki/new.md", Content: &content, Mode: "upsert"},
}, nil)
if err != nil {
    return err
}
fmt.Println(result)
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
    "wait": true
  }'
```

**Response when `wait=true` and refresh completes**

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

The CLI does not currently expose batch write directly.

---

### download()

Download a file as raw bytes. This is intended for images, PDFs, and other non-text content. The response uses `application/octet-stream` and returns the filename through `Content-Disposition`.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uri` | string | Yes | File URI to download |

**Python SDK**

```python
from pathlib import Path

Path("logo.png").write_bytes(
    client.download_bytes("viking://resources/images/logo.png")
)
```

**TypeScript SDK**

```typescript
import { writeFile } from "node:fs/promises";

const bytes = await client.downloadBytes("viking://resources/images/logo.png");
await writeFile("logo.png", bytes);
```

**Go SDK**

```go
// Requires the os package.
data, err := client.DownloadBytes(ctx, "viking://resources/images/logo.png")
if err != nil {
    return err
}
if err := os.WriteFile("logo.png", data, 0600); err != nil {
    return err
}
```

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

`ov get <uri> <local-path>` downloads through the HTTP API above and writes the file to a local path.

---

### ensure_directory_index()

Ensure semantic indexes for one existing directory without replacing its L0/L1 content or tags. If both `.abstract.md` and `.overview.md` are absent, create only L0 using `fallback_abstract`. Existing summaries remain unchanged; only missing indexes are built, without an LLM call. Files, unsupported namespaces, empty or malformed summaries, and permission failures return errors.

**Python SDK**

```python
result = client.ensure_directory_index(
    uri="viking://resources/project/",
    fallback_abstract="This is the compile generated folder",
    wait=True,
)
client.set_tags(
    uri="viking://resources/project/",
    tags=["compile_target=true"],
    mode="append",
    recursive=False,
)
```

TypeScript exposes `client.ensureDirectoryIndex(uri, fallbackAbstract, { wait: true })`; Go exposes `client.EnsureDirectoryIndex(ctx, uri, fallbackAbstract, nil)` with readiness waiting enabled by default.

**HTTP API**

```http
POST /api/v1/content/ensure_directory_index
Content-Type: application/json

{
  "uri": "viking://resources/project/",
  "fallback_abstract": "This is the compile generated folder",
  "wait": true
}
```

`uri` and a non-empty `fallback_abstract` are required. `wait` defaults to `true`: the response confirms that indexes for all existing summary levels are readable. With `wait=false`, missing indexes are queued and the result includes `task_id`; `indexed_levels` lists only levels already readable at response time. The operation checks directory write access and does not process descendants.

**Response**

```json
{
  "status": "ok",
  "result": {
    "uri": "viking://resources/project",
    "created_abstract": true,
    "indexed_levels": [0],
    "vector_status": "complete"
  }
}
```

---

### set_tags()

Set explicit `k=v` tags used by retrieval filters. `replace` replaces existing tags, `append` adds tags, and `clear` explicitly removes existing tags. When the target is a directory, `recursive=true` applies the update to files below it. Omitting `tags`, or passing an empty list with `replace`, is a no-op; only `clear` removes tags and it ignores any supplied tag values.

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

Validate and repair semantic and/or vector artifacts for existing content already stored in OpenViking. For resources and skills, reindex compares persisted content with existing vector records: missing or stale records are rebuilt, vector records whose content no longer exists are deleted, and matching records are skipped. Deletion is allowed only after both sides are read completely. Use `force=true` to rebuild existing content unconditionally; completeness checks still apply.

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

The HTTP request body ignores unknown fields to preserve compatibility while clients and servers evolve independently. `uri` may use OpenViking path variables accepted by other content APIs; it is resolved before validation.

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

For a concrete user root, namespace reindex processes `resources`, skill packages, memory shards, peer resources/memories, and other indexed files or custom top-level directories. Broad operations use shallow paginated discovery and skip session, privacy, internal settings, and storage-control subtrees. An actor-peer view must use an explicit visible content target because a broader namespace cannot provide a complete persisted-content view.

Namespace reindex discovers targets from both persisted content and existing vector records. It reads and reuses one complete lightweight vector-record list per supported URI prefix, so it can clean missing content roots. Retryable failures or record-count changes retry the whole read at most three times; an incomplete read is never used for deletion. Directly requesting an already-missing target still returns `NOT_FOUND`; use an existing parent scope or namespace to remove its leftover vectors. A concrete content request never expands to sibling scopes.

Memory uses the original file's L2 and no longer generates virtual `#chunk_NNNN` records. Reindex removes records whose content is gone and all legacy chunks, including a requested file's own old chunks. If the original memory file cannot be processed, its existing L2 is preserved, and real files whose names resemble chunk URIs are not deleted.

Memory directory L0/L1 records require a nonempty, valid `.abstract.md`/`.overview.md` source for the respective level. A confirmed missing or empty source removes old records at that level; IO or format errors preserve them, increase `failed_records`, and add a warning. The other level and healthy files continue processing.

For `semantic_and_vectors`, a memory directory removes records whose content is gone first, then reuses the same processing flow as `add_resources` and waits for the related semantic and vector work. An exact memory file only rebuilds its own L2 and removes legacy chunks; it does not regenerate directory semantics.

**Modes**

- `vectors_only`: compares current content fingerprints with vector-record fingerprints and rebuilds only missing or stale L0/L1/L2 records; it does not rewrite `.abstract.md` or `.overview.md`
- `semantic_and_vectors`: repairs semantic artifacts first, then updates the corresponding L0/L1/L2 vectors

For `resource` and `skill`, `semantic_and_vectors` refreshes directory/file semantic artifacts, including `.abstract.md` and `.overview.md`. For `memory`, it rebuilds the current persisted memory subtree semantics and vectors.

Both resource/skill modes share one persisted-content scan and one lightweight vector-record read. Each source is read once and reused during later processing. `force=true` bypasses fingerprint equality but still performs completeness and scope checks.

For a resource/skill directory, `recursive=false` limits both modes to the target directory's own L0/L1 records. Direct children are used only to aggregate the target directory; they are not scanned or rebuilt. Namespace containers have no index records of their own, so namespace targets reject `recursive=false` instead of silently becoming a no-op. Memory remains on the existing reindex path.

When non-empty `tags` are provided, tags are saved in the same write as each vector record produced by reindex; reindex does not call `set_tags` afterwards. Directory and namespace reindex operations apply tags to successfully rebuilt directory L0/L1 and leaf L2 records. `replace` overwrites existing tags, while `append` merges by key. `replace` with an empty tag list is a no-op. `clear` removes existing tags and does not require `tags`; if values are supplied with `clear`, they are ignored.

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
  -H "X-API-Key: your-admin-key" \
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
ov reindex viking://resources --mode vectors_only \
  --force --tags team=search,env=prod --tag-mode replace
```

Use `--tag-mode clear` without `--tags` to clear existing tags:

```bash
ov reindex viking://resources --mode vectors_only --tag-mode clear
```

```bash
ov reindex viking://user/default/skills --mode semantic_and_vectors --wait false
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
  }
}
```

Poll the returned task through the task API:

```bash
curl -X GET http://localhost:1933/api/v1/tasks/task_xxx \
  -H "X-API-Key: your-admin-key"
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
| deleted_records | Number of vector records confirmed to have no corresponding content and deleted |
| unsupported_records | Number of records skipped because no usable vector source was available |
| failed_records | Number of records that failed while rebuilding |
| duration_ms | Synchronous run duration in milliseconds |
| warnings | Recoverable per-record warnings |
| task_id | Background task ID, present only when `wait=false` |

**Behavior notes**

- Reindex does not delete the whole vector collection. It overwrites records for live content and deletes only records confirmed to have no corresponding content.
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
