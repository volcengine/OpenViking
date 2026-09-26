# OVPack Import and Export

OVPack is OpenViking's recoverable content package format for migrating or
backing up supported content trees under `viking://`. It stores file content,
semantic sidecar files, portable index scalar fields, and optional dense vector
snapshots.

OVPack is not a raw ZIP copy and is not a trusted publishing format. Import
validates the manifest, file list, directory list, and checksums so package
content cannot drift from the manifest. If an attacker can rewrite both content
and manifest, rely on external signatures, secure transport, and access control.

Storage encryption does not encrypt the exported package. Export reads through the transparent decryption layer and writes plaintext content into a normal ZIP archive. Protect the `.ovpack` file independently during storage and transfer; possession of the archive is enough to read its content without the source storage key.

## Supported Scope

Regular `export/import` handles one package root:

- `viking://resources/...`
- `viking://user/...`

Full migration uses the separate `backup/restore` flow. It packages public
scope roots together (these are namespace roots, not a statement that their content is publicly readable):

- `viking://resources`
- all `viking://user/{user_id}` content in the current account

Only ROOT or ADMIN can call `backup/restore`, and these operations traverse all
user content within the current account. Backup packages do not contain user
accounts or the server API-key registry. User files, including privacy configuration containing external credentials, are still part of the content tree; treat the archive accordingly.

Sessions are included through the user namespace at
`viking://user/{user_id}/sessions/{session_id}`. The
`viking://session/...` alias is not an OVPack v3 import/export scope.

Internal or runtime data such as `temp`, `queue`, `upload`, lock files, watch
control files, and `.relations.json` are outside the OVPack migration scope.

## Working with Multi-Write Storage

Multi-write storage only replicates writes that happen after it is enabled. It
does not automatically copy historical files that already existed before
`storage.agfs.backups` was turned on.

To seed the primary and replicas together, enable multi-write on the **empty target environment before restoring** the package:

1. Pause application writes and export or back up each source account.
2. Configure the target primary and backup backends, including their write policies, then start the target server and provision its restore identity.
3. Restore or import through that server so historical content passes through the configured write fanout.
4. Check synchronization status for every restored scope and verify the expected files in each replica before switching traffic. Asynchronous replication can still be pending after restore returns.
5. Resume writes after validating content and index integrity. Keep the source and backup until verification is complete.

For example, using the target account's admin key:

```bash
ov system backend sync-status viking://resources
ov system backend sync-status viking://user
```

Restoring first and enabling backups afterwards only replicates subsequent writes; it does not seed historical content into replicas.

For more details, see the [Multi-Write Storage Guide](./13-multi-write-storage.md).

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

To replace an existing root, first back up that target and confirm its entire subtree may be removed:

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
report = await client.check_consistency(uri="viking://resources/my-project")
print(report["ok"], report["missing_records"])
```

Go SDK:

```go
report, err := client.CheckConsistency(ctx, "viking://resources/my-project")
if err != nil {
    return err
}
fmt.Println(report["ok"], report["missing_records"])
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
```

Backup packages can only be restored with `restore`; regular `import` rejects
them.

Backup reads live files and is not an atomic point-in-time snapshot. Content
that changes during backup may represent different moments. Pause writes during
the backup window when strict consistency is required.

In API key mode, a new target needs an account and an admin identity **before** restore. The root key can create that identity but cannot call tenant-scoped pack APIs. Set `OPENVIKING_ROOT_API_KEY` to the target root key, then call the target server to create the account with a restore operator whose user ID is absent from the package:

```bash
curl -f -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: $OPENVIKING_ROOT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acme", "admin_user_id": "restore-operator"}'
```

Use the returned `user_key` as `api_key` in a dedicated target client config, for example `restore.ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "<target-admin-user-key>"
}
```

Account initialization already creates scope directories. Consequently, `fail` rejects a restore even when this new account contains no business data; choosing a different restore operator does not avoid that scope-level check. Confirm that the target contains **only the newly created account's initial content**, then restore with `overwrite`:

```bash
OPENVIKING_CLI_CONFIG_FILE=./restore.ovcli.conf \
  ov restore ./backups/openviking.ovpack --on-conflict overwrite
```

For an existing target account, use its admin key instead of creating it again. If it contains business data, do not run this command unchanged: pause writes, back up the target, and review the package paths against target content before deciding what may be overwritten, or use a separate clean target. `overwrite` replaces matching paths; a file/directory type conflict can remove the existing subtree. `fail` checks for existing scope roots, not individual file conflicts, and `skip` skips the whole restore when a scope exists. Each backup/restore is account-scoped; repeat with the appropriate identity for other accounts.

After restoring content, register the remaining users with the same `user_id` values found in the package. Existing user directories do not mean that user accounts exist. The target generates new API keys; source keys are not restored. Verify reads with each target user's key before switching clients.

## Python SDK

Use source credentials for export/backup and a separately configured target client for import/restore across deployments. The full restore examples below assume you have completed the target-account and overwrite checks above. Queue completion alone does not prove a successful index rebuild; inspect errors and run a consistency check.

```python
from openviking_sdk import AsyncHTTPClient


async def migrate_project():
    client = AsyncHTTPClient(url="http://localhost:1933", api_key="your-key")
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
            on_conflict="fail",
            vector_mode="auto",
        )
        print(imported_uri)
        print(await client.wait_processed(timeout=120))
        print(await client.check_consistency(uri=imported_uri))
    finally:
        await client.close()
```

Full backup:

```python
await client.backup_ovpack(
    to="./backups/openviking.ovpack",
    include_vectors=True,
)
await client.restore_ovpack(
    file_path="./backups/openviking.ovpack",
    on_conflict="overwrite",
    vector_mode="auto",
)
```

## Go SDK

```go
outPath, err := client.ExportOVPack(
    ctx,
    "viking://resources/my-project",
    "./exports/my-project.ovpack",
    &openviking.PackOptions{IncludeVectors: false},
)
if err != nil {
    return err
}

importedURI, err := client.ImportOVPack(
    ctx,
    outPath,
    "viking://resources/imported/",
    &openviking.ImportPackOptions{
        OnConflict: "fail",
        VectorMode: "auto",
    },
)
if err != nil {
    return err
}
fmt.Println(importedURI)
```

Full backup:

```go
backupPath, err := client.BackupOVPack(
    ctx,
    "./backups/openviking.ovpack",
    &openviking.PackOptions{IncludeVectors: true},
)
if err != nil {
    return err
}

restoredURI, err := client.RestoreOVPack(
    ctx,
    backupPath,
    &openviking.ImportPackOptions{
        OnConflict: "overwrite",
        VectorMode: "auto",
    },
)
if err != nil {
    return err
}
fmt.Println(restoredURI)
```

## HTTP API

HTTP export returns a file stream directly. HTTP import and restore first upload
the local `.ovpack`, then call the pack endpoint with `temp_file_id`.

Export:

```bash
curl -f -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"uri":"viking://resources/my-project","include_vectors":false}' \
  --output my-project.ovpack
```

Import:

```bash
TEMP_FILE_ID=$(
  curl -fsS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-admin-key" \
    -F "file=@./exports/my-project.ovpack" \
  | jq -er ".result.temp_file_id"
)

curl -f -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"on_conflict\": \"fail\",
    \"vector_mode\": \"auto\"
  }"
```

Full backup:

```bash
curl -f -X POST http://localhost:1933/api/v1/pack/backup \
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

The table above describes regular `import`. For full `restore`, `overwrite`
performs a merge-upsert: missing target paths are created, matching paths are
overwritten, and target-only paths absent from the backup are normally preserved. If a package file replaces an existing directory (or vice versa), restore removes the conflicting target first; existing descendants of that directory are also removed. Restore
does not delete the full `viking://resources` or `viking://user` tree, and it
does not create or write the aggregate `viking://user` container itself.
Vectors are restored or recomputed only for package content that is added or
overwritten; vectors for target-only content remain unchanged.

Every conflict policy first validates the complete manifest, files, checksums,
and vector metadata. A corrupt package fails even with `skip`; it cannot return
success without validation.

## Package Layout

OVPack v3 is a standard ZIP archive with one package root directory:

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

The following manifest is illustrative (placeholder checksums do not form a valid package). It stores package structure, file checksums, and checksums for
internal index files. It does not inline per-file index records:

```json
{
  "kind": "openviking.ovpack",
  "format_version": 3,
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
id, uri, account_id, owner_user_id, owner_space,
created_at, updated_at, active_count
```

User ownership is rebuilt from `viking://user/{user_id}` paths. User accounts
and API keys are not OVPack content.

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
9. Source scope and target scope must match; structured scopes such as `user` also keep root depth stable.
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
# A resources package cannot be imported into user.
ov import ./exports/a.ovpack viking://user/alice/

# A user session subtree cannot be imported into resources.
ov import ./exports/sess_123.ovpack viking://resources/

# A session subtree cannot use itself as parent, which would create
# sessions/sess_123/sess_123.
ov import ./exports/sess_123.ovpack viking://user/alice/sessions/sess_123/
```

## Memories and Sessions

Memory directories have fixed structures. Import the package into the matching
parent directory to avoid duplicate path segments.

User memories:

```bash
ov export viking://user/default/memories ./exports/user-memories.ovpack
ov import ./exports/user-memories.ovpack viking://user/default/ --on-conflict overwrite
```

Session data:

```bash
ov export viking://user/alice/sessions/sess_123 ./exports/sess_123.ovpack
ov import ./exports/sess_123.ovpack viking://user/alice/sessions/ --on-conflict overwrite
```

Sessions restore file state only and do not trigger vectorization.

Result:

```text
viking://user/alice/sessions/sess_123
```

## Old Packages and Future Versions

The current implementation only accepts OVPack v3. Legacy packages without a
manifest do not provide a file set, directory set, or checksums, so OpenViking
cannot tell whether content was removed, modified, or mixed in. They are
rejected by default. To migrate a legacy package, import it in a trusted old
environment first, then re-export it with OVPack v3.

OVPack v2 packages are also rejected by current OpenViking. Re-export old
packages with a current server before importing them here.

Future package versions are not silently accepted either. Upgrade OpenViking or
re-export from an environment that can read that version.

## Common Errors

| Error | Common cause | Fix |
| --- | --- | --- |
| `Missing ovpack manifest` | Legacy package without a manifest | Re-export as v3 in a trusted environment. |
| `Unsupported ovpack format_version` | Package format version is not currently supported | Upgrade OpenViking or re-export. |
| `sha256 does not match manifest` | File or internal index content was changed | Discard the package or re-export from a trusted source. |
| `ovpack entries do not match manifest` | ZIP content is missing files/directories or includes extra files/directories | Discard the package or re-export. |
| `source scope does not match target scope` | Cross-scope import, such as user into resources | Import into a parent directory in the same scope. |
| `source path is incompatible with target path` | Structured scope root depth would change | Import into the correct system parent directory. |
| `Top-level scope ovpack packages must be imported to viking://` | A top-level scope package was imported to a non-root parent | Import to `viking://`. |
| `Backup ovpack packages must be restored` | A backup package was imported with regular import | Use `ov restore`. |
| `Resource already exists` | Target root already exists | Choose a new target parent, use `--on-conflict skip` to keep the existing root, or back it up and use `--on-conflict overwrite` to replace it. |
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

Import preserves packaged semantic sidecars and enqueues vector recomputation when a compatible dense snapshot is not restored. For large migrations,
use `--include-vectors` to reduce recomputation, or split content into smaller
OVPack files and import them in batches.
