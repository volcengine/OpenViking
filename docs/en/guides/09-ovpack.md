# OVPack Import and Export

OVPack is OpenViking's packaging format for exporting/importing context subtrees (e.g., resources and memories) for backup, migration, and sharing.

## Quick Start

### Export Resources

Export OpenViking resources to an `.ovpack` file.

**CLI**
```bash
openviking export viking://resources/my-project/ ./exports/my-project.ovpack
```

**Python SDK**
```python
from openviking import AsyncOpenViking

async def export_example():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        exported_path = await client.export_ovpack(
            uri="viking://resources/my-project/",
            to="./exports/my-project.ovpack"
        )
        print(f"Export successful: {exported_path}")
    finally:
        await client.close()
```

### Import Resources

Import an `.ovpack` file into OpenViking.

**CLI**
```bash
# Basic import
openviking import ./exports/my-project.ovpack viking://resources/imported/

# Explicit conflict policy
openviking import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite
```

**Python SDK**
```python
from openviking import AsyncOpenViking

async def import_example():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        imported_uri = await client.import_ovpack(
            file_path="./exports/my-project.ovpack",
            parent="viking://resources/imported/",
            on_conflict="overwrite"
        )
        print(f"Import successful: {imported_uri}")
        await client.wait_processed()
    finally:
        await client.close()
```

**HTTP API**
```bash
# Step 1: Upload the local ovpack file
# This uses local temporary storage by default.
# Add: -F "upload_mode=shared" only when you explicitly need distributed shared temporary uploads.
# Python HTTP client / CLI users can instead set ovcli.conf: upload.mode = "shared".
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/my-project.ovpack' \
  | jq -r '.result.temp_file_id'
)

# Step 2: Import using temp_file_id
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"on_conflict\": \"overwrite\"
  }"
```

## Format Notes

OVPack v2 files are standard ZIP archives. Each package contains an OpenViking
manifest at `<root>/_._ovpack_manifest.json`; this is the ZIP-escaped form of the
hidden filename `.ovpack_manifest.json`.

The manifest records `kind`, `format_version`, the exported root, content
entries, and portable vector scalar metadata. In `entries`, `path` is relative to
the exported root. An empty path (`""`) means the root directory itself, for
example `my-project/`.

Example manifest for a package exported from `viking://resources/demo/`:

```json
{
  "kind": "openviking.ovpack",
  "format_version": 2,
  "root": {
    "name": "demo",
    "uri": "viking://resources/demo",
    "scope": "resources"
  },
  "entries": [
    {
      "path": "",
      "kind": "directory"
    },
    {
      "path": "notes.txt",
      "kind": "file",
      "size": 5,
      "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    }
  ],
  "content_sha256": "b2a6e9582119c7510d68e3446de3e71a486934bf450d68f65596259ed1cf7997",
  "vectors": {}
}
```

For packages with manifest entries, import validates the ZIP file and directory
set, per-file `size`, per-file `sha256`, and top-level `content_sha256` before
writing any resources. Missing, extra, or modified content entries, and missing
or mismatched v2 `content_sha256`, are rejected. This is an integrity check for the package
contents, not a signature or authentication mechanism if both the manifest and
content can be rewritten.

Raw embedding vectors are not exported. Runtime fields such as `created_at`,
`updated_at`, and `active_count` are also not exported; imports rebuild vectors
and runtime state in the target environment. Session packages restore session
files and do not trigger vectorization. Packages without a manifest are rejected
by default because OpenViking cannot verify their file set or content checksums.
Packages with a manifest validate `kind` and `format_version`, and packages
whose format version is not the current supported version are rejected.
`.abstract.md` and `.overview.md` are restored as semantic sidecar files;
`.relations.json` and OVPack internals are excluded.

Manifest scalar `context_type`, when present, is treated as exported scalar
metadata only. Import compatibility is based on the source and target URI scopes;
the final `context_type` is derived again from the target URI during
vectorization.

Top-level scope packages such as `viking://resources/`, `viking://user/`,
`viking://agent/`, and `viking://session/` must be imported to `viking://`.
Regular import also requires the manifest root scope to match the final target
root scope. Structured scopes (`user`, `agent`, and `session`) cannot be imported
in a way that changes the root depth, such as importing a session package into a
concrete session URI and creating `session/sess_123/sess_123`.
Full backups use the separate `backup` / `restore` interface, not regular
import parent semantics.
OVPack itself does not add package-size, file-count, or directory-depth limits;
the practical limit comes from ZIP, the storage backend, and the runtime
environment.

## Memory Import and Export

OpenViking memories are stored under fixed directory structures:

- User memories: `viking://user/{user_space}/memories/`
- Agent memories: `viking://agent/{agent_id}/memories/` or `viking://agent/{agent_id}/user/{user_id}/memories/`

When migrating memories with OVPack, you must import the `.ovpack` into the parent of the corresponding space (not an arbitrary directory). Otherwise you may end up with paths like `.../memories/memories/...`, and OpenViking will not be able to access and use them as memories.

### Export/Import User Memories (CLI)

```bash
# Export the whole user memories subtree
openviking export viking://user/default/memories/ ./exports/user-memories.ovpack

# Import into the user space root (imports to viking://user/default/memories/)
openviking import ./exports/user-memories.ovpack viking://user/default/ --on-conflict overwrite
```

### Export/Import Agent Memories (CLI)

```bash
# isolate_agent_scope_by_user = false
openviking export viking://agent/default/memories/ ./exports/agent-memories.ovpack
openviking import ./exports/agent-memories.ovpack viking://agent/default/ --on-conflict overwrite

# isolate_agent_scope_by_user = true
openviking export viking://agent/default/user/alice/memories/ ./exports/agent-memories.ovpack
openviking import ./exports/agent-memories.ovpack viking://agent/default/user/alice/ --on-conflict overwrite
```

### Export/Import Memories (Python SDK)

```python
from openviking import AsyncOpenViking

async def export_import_user_memories():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://user/default/memories/",
            to="./exports/user-memories.ovpack",
        )

        await client.import_ovpack(
            file_path="./exports/user-memories.ovpack",
            parent="viking://user/default/",
            on_conflict="overwrite",
        )
    finally:
        await client.close()

async def export_import_agent_memories():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://agent/default/memories/",
            to="./exports/agent-memories.ovpack",
        )
        await client.import_ovpack(
            file_path="./exports/agent-memories.ovpack",
            parent="viking://agent/default/",
            on_conflict="overwrite",
        )
    finally:
        await client.close()
```

### Export/Import Memories (HTTP API)

```bash
# Export user memories
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://user/default/memories/",
    "to": "./exports/user-memories.ovpack"
  }'

# Import user memories (upload first, then import via temp_file_id)
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/user-memories.ovpack' \
  | jq -r '.result.temp_file_id'
)
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://user/default/\",
    \"on_conflict\": \"overwrite\"
  }"
```

### Vectorization on Import

Imports always rebuild vectors in the target environment for `find/search`.
OVPack no longer exposes an import option to disable vectorization. Session
imports are restored as files only and are not vectorized.

### Session Import and Export

```bash
openviking export viking://session/sess_123/ ./exports/sess_123.ovpack
openviking import ./exports/sess_123.ovpack viking://session/ --on-conflict overwrite
```

The restored root is `viking://session/sess_123/`.

### Scope Root Import

```bash
openviking export viking://resources/ ./backups/resources.ovpack
openviking import ./backups/resources.ovpack viking:// --on-conflict overwrite
```

Scope-root packages cannot be imported under another scope directory.

## Use Cases

### Full Backup and Migration

Use a backup package for full migration instead of regular `export/import`
parent-directory semantics:

```bash
openviking backup ./backups/openviking.ovpack
openviking restore ./backups/openviking.ovpack --on-conflict overwrite
```

Backup packages restore to the original public scope roots: `resources`, `user`,
`agent`, and `session`. They exclude internal/runtime data such as `temp`,
`queue`, lock files, OVPack manifests, and `.relations.json`. Non-session
content is re-vectorized; session files are only restored.

### Resource Backup
```bash
DATE=$(date +%Y%m%d)
openviking export viking://resources/ ./backups/backup_${DATE}.ovpack
```

### Resource Migration
```bash
# Export on Machine A
openviking export viking://resources/my-project/ ./migration.ovpack

# Import on Machine B
openviking import ./migration.ovpack viking://resources/ --on-conflict overwrite
```

### Resource Sharing
```bash
# Export
openviking export viking://resources/shared-docs/ ./shared-docs.ovpack

# Recipient imports
openviking import ./shared-docs.ovpack viking://resources/team-shared/
```

## FAQ

**Q: Can I manually extract and view OVPack files?**
A: Yes! OVPack is a standard ZIP format and can be opened with any compression tool.

**Q: What if large OVPack imports are slow?**
A: Imports now always rebuild vectors. If import time is too high, split the content into smaller OVPack files and import them in batches.

**Q: How to handle duplicate resources during import?**
A: Use `--on-conflict overwrite` to replace existing resources, or `--on-conflict skip` to keep them.
