# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the usage_context_write_backfill maintenance script."""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openviking.observability.usage_audit.schema import SQLITE_SCHEMA

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "maintenance"
    / "usage_context_write_backfill"
    / "backfill_context_writes.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("backfill_context_writes", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


def _create_usage_audit_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SQLITE_SCHEMA)
    finally:
        conn.close()


def _set_mtime(path: Path, dt: datetime) -> None:
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def test_scan_resource_files_buckets_by_mtime(script, tmp_path: Path):
    resources = tmp_path / "resources"
    (resources / "nested").mkdir(parents=True)

    first = resources / "a.md"
    first.write_text("a")
    _set_mtime(first, datetime(2026, 5, 12, 1, 30, tzinfo=timezone.utc))

    second = resources / "nested" / "b.md"
    second.write_text("b")
    _set_mtime(second, datetime(2026, 5, 12, 3, 59, tzinfo=timezone.utc))

    third = resources / "c.md"
    third.write_text("c")
    _set_mtime(third, datetime(2026, 5, 12, 23, 5, tzinfo=timezone.utc))

    buckets = script.scan_resource_files(resources)

    assert buckets == Counter(
        {
            ("2026-05-12", 0): 2,
            ("2026-05-12", 20): 1,
        }
    )


def test_scan_resource_files_missing_dir_returns_empty(script, tmp_path: Path):
    assert script.scan_resource_files(tmp_path / "does-not-exist") == Counter()


def test_backfill_inserts_and_accumulates(script, tmp_path: Path):
    db_path = tmp_path / "usage_audit.sqlite3"
    _create_usage_audit_db(db_path)

    buckets = Counter({("2026-05-12", 0): 2, ("2026-05-13", 4): 1})
    inserted = script.backfill(db_path, buckets, "acct-1", "user-1")
    assert inserted == 2

    # Re-running accumulates counts via the ON CONFLICT upsert.
    script.backfill(db_path, Counter({("2026-05-12", 0): 3}), "acct-1", "user-1")

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT account_id, user_id, date_utc, hour_utc, operation, count "
            "FROM usage_context_write_bucket ORDER BY date_utc, hour_utc"
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        ("acct-1", "user-1", "2026-05-12", 0, "add_resource", 5),
        ("acct-1", "user-1", "2026-05-13", 4, "add_resource", 1),
    ]


def test_backfill_missing_table_raises(script, tmp_path: Path):
    db_path = tmp_path / "empty.sqlite3"
    sqlite3.connect(db_path).close()

    with pytest.raises(RuntimeError, match="usage_context_write_bucket"):
        script.backfill(db_path, Counter({("2026-05-12", 0): 1}), "acct-1", "user-1")
