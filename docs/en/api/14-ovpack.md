# OVPack

The OVPack API imports, exports, backs up, and restores OpenViking data.

## API Reference

### export_ovpack

Export a resource tree as a `.ovpack` file.

#### 1. API Implementation Overview

Packages all resources under the specified URI into a `.ovpack` file for backup or migration. Available to ROOT, ADMIN, and USER roles; normal URI access controls still apply.

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

**Permission Requirements**: ROOT, ADMIN, or USER

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

**TypeScript SDK**

```typescript
const outputPath = await client.exportOVPack(
  "viking://resources/docs/",
  "./exports/docs.ovpack",
  true,
);
console.log(outputPath);
```

**Go SDK**

```go
outPath, err := client.ExportOVPack(
    ctx,
    "viking://resources/my-project/",
    "./exports/my-project.ovpack",
    &openviking.PackOptions{IncludeVectors: false},
)
if err != nil {
    return err
}
fmt.Println(outPath)
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

Imports a `.ovpack` file to a specified location for restoring or migrating data. Available to ROOT, ADMIN, and USER roles; normal URI access controls still apply.

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

**Permission Requirements**: ROOT, ADMIN, or USER

**Behavior Notes**:
- The API no longer accepts `vectorize` or `force`.
- `vector_mode=auto` restores a compatible dense snapshot when present, otherwise recomputes vectors. `recompute` always ignores package vectors. `require` fails unless a compatible dense snapshot is present.
- Dense snapshot compatibility checks compare embedding provider, model, input mode, query/document parameters, and dimensions.
- Session files are part of the user namespace (`viking://user/{user_id}/sessions/...`) and do not trigger vectorization.
- `on_conflict=fail` returns a structured `409 CONFLICT` when the target root already exists.
- `on_conflict=overwrite` replaces the existing target root. `on_conflict=skip` keeps the existing target root and returns it without writing package contents. `skip` is root-level, not file-level.
- Packages without a manifest are rejected by default because they cannot provide content integrity guarantees.
- Packages with manifest entries are rejected if content files or directories are missing, extra files or directories are present, file sizes differ, per-file `sha256` differs, or `content_sha256` is missing or differs.
- Packages whose manifest `format_version` is not the current supported version (`3`) are rejected.
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

**TypeScript SDK**

```typescript
const uri = await client.importOVPack(
  "./exports/docs.ovpack",
  "viking://resources/",
  {
    onConflict: "overwrite",
    vectorMode: "auto",
  },
);
console.log(uri);
```

**Go SDK**

```go
uri, err := client.ImportOVPack(
    ctx,
    "./exports/my-project.ovpack",
    "viking://resources/imported/",
    &openviking.ImportPackOptions{
        OnConflict: "overwrite",
        VectorMode: "auto",
    },
)
if err != nil {
    return err
}
fmt.Println(uri)
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
`resources` and `user`; sessions are included through the user namespace under
`user/{user_id}/sessions`. It excludes internal runtime data such as `temp` and
`queue`. Set `include_vectors=true` to include compatible
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

Go SDK:

```go
outPath, err := client.BackupOVPack(
    ctx,
    "./backups/openviking.ovpack",
    &openviking.PackOptions{IncludeVectors: true},
)
if err != nil {
    return err
}
fmt.Println(outPath)
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
`vector_mode`; session files under the user namespace are restored without
vectorization.

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

Go SDK:

```go
uri, err := client.RestoreOVPack(
    ctx,
    "./backups/openviking.ovpack",
    &openviking.ImportPackOptions{
        OnConflict: "overwrite",
        VectorMode: "require",
    },
)
if err != nil {
    return err
}
fmt.Println(uri)
```

CLI:

```bash
ov restore ./backups/openviking.ovpack --on-conflict overwrite
ov restore ./backups/openviking.ovpack --on-conflict overwrite --vector-mode require
```

---

## Related Documentation

- [OVPack Guide](../guides/09-ovpack.md) - format, migration, and workflows
- [Snapshots](11-snapshot.md) - workspace version management
- [Temporary Upload](02-resources.md#temp_upload) - upload packages before import
