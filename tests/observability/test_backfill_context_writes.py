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


def test_scan_counts_one_per_top_level_entry(script, tmp_path: Path):
    resources = tmp_path / "resources"
    resources.mkdir()

    # Reviewer scenario: one repository import expands to six physical files
    # (two content files plus four generated sidecars) but must count as one
    # add_resource, bucketed at the earliest content mtime.
    repo = resources / "repo"
    (repo / "src").mkdir(parents=True)
    readme = repo / "README.md"
    readme.write_text("readme")
    _set_mtime(readme, datetime(2026, 5, 12, 1, 30, tzinfo=timezone.utc))
    main_py = repo / "src" / "main.py"
    main_py.write_text("main")
    _set_mtime(main_py, datetime(2026, 5, 12, 2, 10, tzinfo=timezone.utc))
    for sidecar_dir in (repo, repo / "src"):
        for name in (".abstract.md", ".overview.md"):
            sidecar = sidecar_dir / name
            sidecar.write_text("generated")
            _set_mtime(sidecar, datetime(2026, 5, 13, 9, 0, tzinfo=timezone.utc))

    # A standalone top-level file counts once at its own mtime.
    single = resources / "c.md"
    single.write_text("c")
    _set_mtime(single, datetime(2026, 5, 12, 23, 5, tzinfo=timezone.utc))

    buckets = script.scan_resource_entries(resources)

    assert buckets == Counter(
        {
            ("2026-05-12", 0): 1,
            ("2026-05-12", 20): 1,
        }
    )


def test_scan_skips_sidecar_only_entries(script, tmp_path: Path):
    resources = tmp_path / "resources"
    (resources / "empty_dir").mkdir(parents=True)

    # Top-level sidecar files and directories containing only sidecars are
    # not resources and must not count.
    top_sidecar = resources / ".abstract.md"
    top_sidecar.write_text("generated")
    sidecar_dir = resources / "only_sidecars"
    sidecar_dir.mkdir()
    (sidecar_dir / ".overview.md").write_text("generated")

    assert script.scan_resource_entries(resources) == Counter()


def test_scan_respects_cutoff(script, tmp_path: Path):
    resources = tmp_path / "resources"
    resources.mkdir()

    old = resources / "old.md"
    old.write_text("old")
    _set_mtime(old, datetime(2026, 5, 12, 1, 30, tzinfo=timezone.utc))

    recent = resources / "recent.md"
    recent.write_text("recent")
    _set_mtime(recent, datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc))

    cutoff = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    buckets = script.scan_resource_entries(resources, cutoff=cutoff)

    # Entries ingested after the live recorder started are already counted
    # by the request-based metric and must be skipped.
    assert buckets == Counter({("2026-05-12", 0): 1})


def test_scan_resource_entries_missing_dir_returns_empty(script, tmp_path: Path):
    assert script.scan_resource_entries(tmp_path / "does-not-exist") == Counter()


def test_recorder_start_time_from_request_audit(script, tmp_path: Path):
    db_path = tmp_path / "usage_audit.sqlite3"
    _create_usage_audit_db(db_path)

    # No live requests recorded yet -> no cutoff.
    assert script.recorder_start_time(db_path) is None

    conn = sqlite3.connect(db_path)
    try:
        for created_at in ("2026-06-02T08:00:00+00:00", "2026-06-01T00:00:00+00:00"):
            conn.execute(
                "INSERT INTO request_audit "
                "(request_id, account_id, user_id, method, route, api_type, "
                " status_code, duration_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "req",
                    "acct-1",
                    "user-1",
                    "POST",
                    "/api/v1/resources",
                    "data",
                    200,
                    1.0,
                    created_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    assert script.recorder_start_time(db_path) == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def test_recorder_start_time_missing_table_returns_none(script, tmp_path: Path):
    db_path = tmp_path / "empty.sqlite3"
    sqlite3.connect(db_path).close()

    assert script.recorder_start_time(db_path) is None


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
