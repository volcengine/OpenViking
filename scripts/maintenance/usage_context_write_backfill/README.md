# Usage Context Write Backfill

This maintenance script backfills historical rows into the
`usage_context_write_bucket` table so the Studio "context commits" heatmap
shows activity from before the Usage/Audit system was enabled.

## Background

Usage/Audit only records `add_resource`, `add_skill`, `session_add_message`,
and `session_commit` events emitted after it was enabled. Resources ingested
earlier are invisible to the heatmap even though they exist in the workspace.

## What It Does

- Walks the resource files under `<workspace>/viking/<account>/resources`.
- Buckets each file's modification time (mtime) into UTC 4-hour buckets.
- Upserts the aggregated counts into `usage_context_write_bucket` as
  `add_resource` rows (`ON CONFLICT ... count = count + excluded.count`).

## What It Does Not Do

- It does not backfill `add_skill`, `session_add_message`, or
  `session_commit`; those operations leave no per-file timestamp to recover.
- It does not create the Usage/Audit schema; start the server once with
  Usage/Audit enabled first.
- It does not deduplicate against rows already recorded by the live system —
  run it once, against history that predates Usage/Audit.

## Usage

```bash
python scripts/maintenance/usage_context_write_backfill/backfill_context_writes.py \
    --workspace /path/to/workspace --account default --user default
```

Options:

| Flag | Default | Description |
| --- | --- | --- |
| `--workspace` | (required) | Workspace path holding `viking/` and `_system/` |
| `--account` | `default` | `account_id` to attribute backfilled rows to |
| `--user` | `default` | `user_id` to attribute backfilled rows to |
| `--resources-dir` | `<workspace>/viking/<account>/resources` | Override the scanned directory |
| `--dry-run` | off | Print bucket counts without writing |

## Verification

1. Re-run the Studio and refresh the home page — the context-commit heatmap
   should show historical buckets.
2. Or query the console API:
   `GET /api/v1/console/context-commits?start_date=...&end_date=...&bucket=4h`
   and confirm items with `total > 0`.

## Caveats

- File mtime can differ from the original ingestion time after copies,
  migrations, or filesystem-level changes.
