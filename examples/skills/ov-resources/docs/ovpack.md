# OVPack Import/Export/Backup/Restore

OVPack is a `.ovpack` archive format for backing up and migrating OpenViking resource trees. Requires ROOT or ADMIN permissions.

## Export (`ov export`)

Packages resources under a URI into a `.ovpack` file.

```bash
# Export a resource tree
ov export viking://resources/my-project/ ./exports/my-project.ovpack

# Include dense vector snapshot
ov export viking://resources/my-project/ ./exports/my-project.ovpack --include-vectors
```

The ZIP stores user content under `<root>/files/` and metadata under `<root>/_ovpack/`:
- `manifest.json` — entry list with `path`, `size`, `sha256`, `content_sha256`
- `index_records.jsonl` — portable index scalar fields
- `dense.f32` — pure-dense float32 vector snapshot (when `--include-vectors`)

Hybrid index types reject vector snapshot export.

## Import (`ov import`)

Restores a `.ovpack` file to a target location.

```bash
# Basic import
ov import ./exports/my-project.ovpack viking://resources/imported/

# Explicit conflict policy
ov import ./exports/my-project.ovpack viking://resources/imported/ --on-conflict overwrite

# Require compatible dense vector snapshot
ov import ./exports/my-project.ovpack viking://resources/imported/ --vector-mode require
```

Conflict policies: `fail` (default), `overwrite`, `skip`.
Vector modes: `auto` (default), `recompute`, `require`.

## Backup (`ov backup`)

Backs up all public scope roots (`resources`, `user`, `agent`, `session`) as a restore-only `.ovpack`.

```bash
ov backup ./backups/openviking.ovpack
ov backup ./backups/openviking.ovpack --include-vectors
```

## Restore (`ov restore`)

Restores a backup package created by `ov backup` to the original public scope roots.

```bash
ov restore ./backups/openviking.ovpack --on-conflict overwrite
ov restore ./backups/openviking.ovpack --on-conflict overwrite --vector-mode require
```

Regular import rejects backup packages. Session files are restored without vectorization.

## Important Notes

- Packages without a manifest are rejected.
- Content integrity is validated (file sizes, `sha256`, `content_sha256`).
- Runtime fields (`id`, `uri`, `account_id`, `created_at`, `updated_at`) are regenerated on import.
- Top-level scope packages (e.g. `viking://resources/`) must be imported to `viking://`.
