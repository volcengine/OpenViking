# OVPack Import and Export

OVPack is OpenViking's recoverable content package format for migrating or
backing up public content trees under `viking://`. It stores file content,
semantic sidecar files, portable index scalar fields, and optional dense vector
snapshots.

OVPack is not a raw ZIP copy and is not a trusted publishing format. Import
validates the manifest, file list, directory list, and checksums so package
content cannot drift from the manifest. If an attacker can rewrite both content
and manifest, rely on external signatures, secure transport, and access control.

## Supported Scope

Regular `export/import` handles one package root:

- `viking://resources/...`
- `viking://user/...`
- `viking://agent/...`
- `viking://session/...`

Full migration uses the separate `backup/restore` flow. It packages public
scope roots together:

- `viking://resources`
- `viking://user`
- `viking://agent`
- `viking://session`

Internal or runtime data such as `temp`, `queue`, `upload`, lock files, watch
control files, and `.relations.json` are outside the OVPack migration scope.

## Quick Start

### Export and Import a Resource Directory

```bash
ov export viking://resources/my-project ./exports/my-project.ovpack
ov import ./exports/my-project.ovpack viking://resources/imported/
```

The import target is the parent directory, not the final root. If the package
root is `my-project`, the imported URI is:

```text
viking://resources/imported/my-project
```

Overwrite an existing root:

```bash
ov import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite
```

### Export Vector Snapshots

By default, export does not store dense vectors. Import recomputes vectors in
the target environment:

```bash
ov export viking://resources/my-project ./exports/my-project.ovpack
ov import ./exports/my-project.ovpack viking://resources/imported/
```

If the export and import environments use the same embedding configuration, you
can explicitly include a dense vector snapshot:

```bash
ov export viking://resources/my-project ./exports/my-project.ovpack --include-vectors
ov import ./exports/my-project.ovpack viking://resources/imported/ --vector-mode auto
```

`--vector-mode` controls how import handles package vectors:

| Value | Behavior |
| --- | --- |
| `auto` | Default. Restore a dense snapshot when present and embedding metadata is compatible; otherwise recompute vectors. |
| `recompute` | Ignore package dense snapshots and always recompute vectors. |
| `require` | Require a compatible dense snapshot. Missing, incomplete, model-mismatched, or dimension-mismatched snapshots fail import. |

Compatibility checks compare the package embedding provider, model, input,
query/document parameters, and dimensions with the current environment. OVPack
vector snapshots currently support pure dense indexes only. If the underlying
`VectorIndex.IndexType` is hybrid, `--include-vectors` fails the export. When
importing into a hybrid-index environment, `auto` recomputes vectors and
`require` fails.

Before exporting a dense vector snapshot, OpenViking runs a data consistency
check. It verifies that content expected to be in the vector index already has
matching index records. Missing records fail the export so the package does not
carry an incomplete index snapshot.

You can call the consistency check directly when debugging data state:

```bash
ov system consistency viking://resources/my-project
```

The API returns only a summary and at most 20 missing records. It does not return
the full expected-record list. When `--include-vectors` export fails, error
details include only one missing key to keep logs small.

Python SDK:

```python
report = await client.check_consistency("viking://resources/my-project")
print(report["ok"], report["missing_records"])
```

HTTP API:

```bash
curl -X POST http://localhost:1933/api/v1/system/consistency \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources/my-project"}'
```

### Full Backup and Restore

Do not use `export viking://` for full migration. Use a backup package:

```bash
ov backup ./backups/openviking.ovpack
ov restore ./backups/openviking.ovpack --on-conflict overwrite
```

Backup packages can only be restored with `restore`; regular `import` rejects
them.

## Python SDK

```python
from openviking import AsyncOpenViking


async def migrate_project():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://resources/my-project",
            to="./exports/my-project.ovpack",
            include_vectors=False,
        )

        imported_uri = await client.import_ovpack(
            file_path="./exports/my-project.ovpack",
            parent="viking://resources/imported/",
            on_conflict="overwrite",
            vector_mode="auto",
        )
        print(imported_uri)
        await client.wait_processed()
    finally:
        await client.close()
```

Full backup:

```python
await client.backup_ovpack("./backups/openviking.ovpack", include_vectors=True)
await client.restore_ovpack(
    "./backups/openviking.ovpack",
    on_conflict="overwrite",
    vector_mode="auto",
)
```

## HTTP API

HTTP export returns a file stream directly. HTTP import and restore first upload
the local `.ovpack`, then call the pack endpoint with `temp_file_id`.

Export:

```bash
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources/my-project","include_vectors":false}' \
  --output my-project.ovpack
```

Import:

```bash
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./exports/my-project.ovpack" \
  | jq -r ".result.temp_file_id"
)

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

Full backup:

```bash
curl -X POST http://localhost:1933/api/v1/pack/backup \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"include_vectors":true}' \
  --output openviking-backup.ovpack
```

## Conflict Policy

`on_conflict` only applies when the import root already exists.

| Value | Behavior |
| --- | --- |
| `fail` | Default. Return `409 CONFLICT` when the target root exists. |
| `overwrite` | Delete the existing root, then write package content and rebuild index state. |
| `skip` | Return the existing root URI without writing package content. |

`skip` is a root-level skip, not a file-level merge.

## Package Layout

OVPack v2 is a standard ZIP archive with one package root directory:

```text
my-project/
my-project/files/
my-project/files/notes.txt
my-project/files/.abstract.md
my-project/files/.overview.md
my-project/_ovpack/
my-project/_ovpack/index_records.jsonl
my-project/_ovpack/dense.f32                # only with --include-vectors and exportable vectors
my-project/_ovpack/manifest.json
```

`files/` stores user content with the same relative paths used by OpenViking.
Dotfiles are no longer escaped with `_._`. `_ovpack/` stores OVPack internal
metadata and is not imported as user content.

The manifest stores package structure, file checksums, and checksums for
internal index files. It does not inline per-file index records:

```json
{
  "kind": "openviking.ovpack",
  "format_version": 2,
  "root": {
    "name": "my-project",
    "uri": "viking://resources/my-project",
    "scope": "resources"
  },
  "entries": [
    {"path": "", "kind": "directory"},
    {
      "path": "notes.txt",
      "kind": "file",
      "size": 5,
      "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    }
  ],
  "content_sha256": "b2a6e9582119c7510d68e3446de3e71a486934bf450d68f65596259ed1cf7997",
  "index": {
    "records": {
      "path": "_ovpack/index_records.jsonl",
      "count": 2,
      "sha256": "..."
    },
    "dense": {
      "path": "_ovpack/dense.f32",
      "count": 1,
      "dtype": "float32",
      "byte_order": "little",
      "dimensions": 1024,
      "sha256": "...",
      "embedding": {
        "provider": "volcengine",
        "model": "doubao-embedding-vision-251215",
        "input": "multimodal",
        "dimensions": 1024
      }
    }
  }
}
```

`entries[].path == ""` means the package root directory itself. Nested paths are
stored as relative paths:

```json
[
  {"path": "", "kind": "directory"},
  {"path": "docs", "kind": "directory"},
  {"path": "docs/a.md", "kind": "file", "size": 12, "sha256": "..."}
]
```

`index_records.jsonl` contains one index record per line and can describe files,
directories, and the root directory:

```jsonl
{"record_id":"r000001","path":"","kind":"directory","level":0,"text":"root abstract","scalars":{"abstract":"root abstract","context_type":"resource","level":0}}
{"record_id":"r000002","path":"notes.txt","kind":"file","level":2,"scalars":{"abstract":"note summary","tags":"demo"},"vector":{"dense":{"offset":0,"dimensions":1024}}}
```

`dense.f32` is a contiguous little-endian float32 array. The
`vector.dense.offset` in `index_records.jsonl` is a float offset, not a byte
offset. In the example above, `offset=0, dimensions=1024` means reading 1024
float values from float offset 0.

## Index Fields

By default, export stores portable index scalar fields:

```text
type, context_type, level, name, description, tags, abstract
```

These fields are regenerated in the target environment and are not restored
from the package:

```text
id, uri, account_id, owner_user_id, owner_agent_id, owner_space,
created_at, updated_at, active_count
```

With `--include-vectors`, export also stores pure-dense vectors and embedding
metadata. Even when import restores dense snapshots, runtime fields are rebuilt
from the target URI, target account, and current time. Hybrid indexes do not
currently support vector snapshot export.

## Import Validation

Import validates the entire package before writing package content. Core checks:

1. ZIP members must be under one package root and cannot contain absolute paths, backslashes, drive letters, or `..`.
2. `<root>/_ovpack/manifest.json` must exist.
3. `kind` must be `openviking.ovpack`, and `format_version` must equal the currently supported version.
4. `root.name` must match the ZIP root, and the leaf of `root.uri` must also match `root.name`.
5. The file set and directory set declared by the manifest must exactly match ZIP content.
6. Each file `size` and `sha256` must match actual content.
7. `content_sha256` must match the sorted file inventory.
8. `_ovpack/index_records.jsonl` and optional `_ovpack/dense.f32` must match manifest hashes, counts, and dimensions.
9. Source scope and target scope must match; structured scopes such as `user`, `agent`, and `session` also keep root depth stable.
10. No package content is written before validation passes; conflict handling also runs before writes.

Typical rejection examples:

```text
INVALID_ARGUMENT: Missing ovpack manifest
INVALID_ARGUMENT: ovpack file sha256 does not match manifest
INVALID_ARGUMENT: ovpack entries do not match manifest
INVALID_ARGUMENT: ovpack source scope does not match target scope
INVALID_ARGUMENT: ovpack package does not contain a dense vector snapshot
```

## Import Path Rules

Regular subtree packages import into a parent directory in the same scope and
keep the package root:

```bash
ov export viking://resources/a ./exports/a.ovpack
ov import ./exports/a.ovpack viking://resources/imported/
```

Result:

```text
viking://resources/imported/a
```

Top-level scope packages can only be imported to `viking://`:

```bash
ov export viking://resources ./exports/resources.ovpack
ov import ./exports/resources.ovpack viking:// --on-conflict overwrite
```

These imports are rejected:

```bash
# A resources package cannot be imported into session.
ov import ./exports/a.ovpack viking://session/

# A session subtree cannot be imported into resources.
ov import ./exports/sess_123.ovpack viking://resources/

# A session subtree cannot use itself as parent, which would create session/sess_123/sess_123.
ov import ./exports/sess_123.ovpack viking://session/sess_123/
```

## Memories and Sessions

Memory directories have fixed structures. Import the package into the matching
parent directory to avoid duplicate path segments.

User memories:

```bash
ov export viking://user/default/memories ./exports/user-memories.ovpack
ov import ./exports/user-memories.ovpack viking://user/default/ --on-conflict overwrite
```

Agent memories:

```bash
ov export viking://agent/default/memories ./exports/agent-memories.ovpack
ov import ./exports/agent-memories.ovpack viking://agent/default/ --on-conflict overwrite
```

Sessions restore file state only and do not trigger vectorization:

```bash
ov export viking://session/sess_123 ./exports/sess_123.ovpack
ov import ./exports/sess_123.ovpack viking://session/ --on-conflict overwrite
```

Result:

```text
viking://session/sess_123
```

## Old Packages and Future Versions

The current implementation only accepts OVPack v2. Legacy packages without a
manifest do not provide a file set, directory set, or checksums, so OpenViking
cannot tell whether content was removed, modified, or mixed in. They are
rejected by default. To migrate a legacy package, import it in a trusted old
environment first, then re-export it with the current version.

Future package versions are not silently accepted either. Upgrade OpenViking or
re-export from an environment that can read that version.

## Common Errors

| Error | Common cause | Fix |
| --- | --- | --- |
| `Missing ovpack manifest` | Legacy package without a manifest | Re-export as v2 in a trusted environment. |
| `Unsupported ovpack format_version` | Package format version is not currently supported | Upgrade OpenViking or re-export. |
| `sha256 does not match manifest` | File or internal index content was changed | Discard the package or re-export from a trusted source. |
| `ovpack entries do not match manifest` | ZIP content is missing files/directories or includes extra files/directories | Discard the package or re-export. |
| `source scope does not match target scope` | Cross-scope import, such as session into resources | Import into a parent directory in the same scope. |
| `source path is incompatible with target path` | Structured scope root depth would change | Import into the correct system parent directory. |
| `Top-level scope ovpack packages must be imported to viking://` | A top-level scope package was imported to a non-root parent | Import to `viking://`. |
| `Backup ovpack packages must be restored` | A backup package was imported with regular import | Use `ov restore`. |
| `Resource already exists` | Target root already exists | Use `--on-conflict overwrite` or `--on-conflict skip`. |
| `incomplete OpenViking vector index snapshot` | `--include-vectors` found missing index records in the export range | Run `ov system consistency <uri>` to locate the issue, then wait for processing or reindex. |
| `dense vector snapshot is incompatible` | Package embedding metadata does not match current config | Use `--vector-mode recompute`, or switch to a compatible config. |

## FAQ

**Can I manually extract and inspect OVPack files?**

Yes. OVPack is a ZIP file and can be opened with ordinary ZIP tools. Do not edit
it manually before import, because edits break manifest validation. If both
manifest and content are changed, use external signatures and trusted sources to
decide whether the package is safe.

**Why are vectors not exported by default?**

Vectors are reusable only when the embedding model, input mode, parameters, and
dimensions are fully compatible. Recomputing by default is safer. Use
`--include-vectors` with `--vector-mode auto/require` when you need faster cold
migration and know the environments are compatible.

**What if large package imports are slow?**

Default import rebuilds target semantic and vector state. For large migrations,
use `--include-vectors` to reduce recomputation, or split content into smaller
OVPack files and import them in batches.
