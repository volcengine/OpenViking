# Usage Context Write Backfill

This maintenance script backfills historical rows into the
`usage_context_write_bucket` table so the Studio "context commits" heatmap
shows activity from before the Usage/Audit system was enabled.

## Background

Usage/Audit only records `add_resource`, `add_skill`, `session_add_message`,
and `session_commit` events emitted after it was enabled. Resources ingested
earlier are invisible to the heatmap even though they exist in the workspace.

## What It Does

- Scans the top-level entries under `<workspace>/viking/<account>/resources`
  and counts **one `add_resource` per entry**, matching the live metric's
  unit of one successful `POST /api/v1/resources` request.
- Uses each entry's earliest content-file mtime as the ingestion time and
  buckets it into UTC 4-hour buckets. Generated semantic sidecars
  (`.abstract.md`, `.overview.md`, `.relations.json`) never count and never
  contribute timestamps.
- Skips entries ingested at or after the live recorder started (derived from
  the earliest `request_audit` row; override with `--cutoff`), so requests
  already counted by the live metric are not double counted.
- Upserts the aggregated counts into `usage_context_write_bucket` as
  `add_resource` rows (`ON CONFLICT ... count = count + excluded.count`).

## What It Does Not Do

- It does not backfill `add_skill`, `session_add_message`, or
  `session_commit`; those operations leave no per-file timestamp to recover.
- It does not create the Usage/Audit schema; start the server once with
  Usage/Audit enabled first.
- It does not deduplicate its own rows across runs — re-running still
  accumulates counts for pre-cutoff history, so run it once (or `--dry-run`
  first to preview).

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
| `--cutoff` | earliest `request_audit` row | ISO-8601 timestamp; entries ingested at or after it are skipped |
| `--dry-run` | off | Print bucket counts without writing |

## Verification

1. Re-run the Studio and refresh the home page — the context-commit heatmap
   should show historical buckets.
2. Or query the console API:
   `GET /api/v1/console/context-commits?start_date=...&end_date=...&bucket=4h`
   and confirm items with `total > 0`.

## Caveats

- Backfilled values are an approximation, not exact request counts: file
  mtime can differ from the original ingestion time after copies,
  migrations, or filesystem-level changes, and one request importing into
  the resources root can expand into several top-level entries.
- The cutoff relies on `request_audit`, which is reset on Usage/Audit schema
  upgrades; after a reset, pass `--cutoff` explicitly if you know the real
  recorder start time.
