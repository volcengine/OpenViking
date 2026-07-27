#!/usr/bin/env python3
"""Backfill historical context-commit stats for the Studio usage heatmap.

The ``usage_context_write_bucket`` table only records ``add_resource`` /
``add_skill`` / ``session_add_message`` / ``session_commit`` events emitted
after the Usage/Audit system was enabled, so resources ingested earlier never
show up in the Studio "context commits" heatmap. This one-off maintenance
script approximates the missing history by scanning resource files in the
workspace and bucketing their modification times (mtime, 4-hour buckets in
UTC) into the ``usage_context_write_bucket`` table as ``add_resource`` rows.

Usage:
    python scripts/maintenance/usage_context_write_backfill/backfill_context_writes.py \
        --workspace /path/to/workspace --account default --user default

Limitations:
    - Only ``add_resource`` is backfilled (from file mtime); the other
      operations leave no per-file timestamp to recover.
    - File mtime may differ from the original ingestion time after copies or
      migrations.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BUCKET_HOURS = 4

UPSERT_SQL = """
INSERT INTO usage_context_write_bucket
    (account_id, user_id, date_utc, hour_utc, operation, count, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(account_id, user_id, date_utc, hour_utc, operation)
DO UPDATE SET count = count + excluded.count, updated_at = excluded.updated_at
"""


def scan_resource_files(resources_dir: Path) -> Counter:
    """Count resource files per (date_utc, hour_utc) 4-hour bucket by mtime."""
    buckets: Counter = Counter()
    if not resources_dir.is_dir():
        return buckets
    for root, _dirs, fnames in os.walk(resources_dir):
        for fname in fnames:
            fp = Path(root) / fname
            try:
                mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            date_utc = mtime.strftime("%Y-%m-%d")
            hour_utc = (mtime.hour // BUCKET_HOURS) * BUCKET_HOURS
            buckets[(date_utc, hour_utc)] += 1
    return buckets


def backfill(db_path: Path, buckets: Counter, account_id: str, user_id: str) -> int:
    """Upsert aggregated buckets into ``usage_context_write_bucket``."""
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        table_exists = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_context_write_bucket'"
        ).fetchone()
        if table_exists is None:
            raise RuntimeError(
                "Table usage_context_write_bucket does not exist. "
                "Start the server once with Usage/Audit enabled to create the schema."
            )
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        for (date_utc, hour_utc), count in sorted(buckets.items()):
            cursor.execute(
                UPSERT_SQL,
                (account_id, user_id, date_utc, hour_utc, "add_resource", count, now),
            )
            inserted += cursor.rowcount
        conn.commit()
        return inserted
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill historical context-commit stats from resource file mtimes."
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="OpenViking workspace path (the directory holding viking/ and _system/)",
    )
    parser.add_argument("--account", default="default", help="account_id to attribute rows to")
    parser.add_argument("--user", default="default", help="user_id to attribute rows to")
    parser.add_argument(
        "--resources-dir",
        type=Path,
        default=None,
        help="Override the resources directory (default: <workspace>/viking/<account>/resources)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report bucket counts without writing to the database",
    )
    args = parser.parse_args()

    resources_dir = args.resources_dir or (args.workspace / "viking" / args.account / "resources")
    db_path = args.workspace / "_system" / "usage_audit" / "usage_audit.sqlite3"

    if not db_path.exists():
        print(f"Error: Usage/Audit database not found: {db_path}")
        print("Start the server once with Usage/Audit enabled, then re-run this script.")
        return 1

    buckets = scan_resource_files(resources_dir)
    total_files = sum(buckets.values())
    print(f"Scanned {total_files} resource files across {len(buckets)} time buckets")

    if total_files == 0:
        print("No resource files found; nothing to backfill.")
        return 0

    if args.dry_run:
        for (date_utc, hour_utc), count in sorted(buckets.items()):
            print(f"  {date_utc} {hour_utc:02d}:00 UTC  add_resource x{count}")
        print("Dry run: no rows written.")
        return 0

    inserted = backfill(db_path, buckets, args.account, args.user)
    print(f"Backfilled {inserted} rows into usage_context_write_bucket")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
