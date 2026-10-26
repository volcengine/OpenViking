# Copyright (c) 2026 Beijing Volcano Engine Technology Co. Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rollback-resilience tests for the Usage/Audit SQLite store.

SQLite auto-rolls-back a transaction after certain fatal errors (SQLITE_FULL,
SQLITE_IOERR). The store's error paths used to issue a bare
``conn.execute("ROLLBACK")`` which then raised
``OperationalError: cannot rollback - no transaction is active`` and replaced
the underlying storage failure with a misleading secondary error (seen in
production as issue #4303). These tests pin the fixed behaviour: the original
exception must survive, and a still-active transaction must still be rolled
back.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from openviking.observability.usage_audit.projection import UsageAuditProjection
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore


def _make_store(tmp_path: Path) -> SQLiteUsageAuditStore:
    store = SQLiteUsageAuditStore(tmp_path / "usage_audit.sqlite3")
    asyncio.run(store.initialize())
    return store


def _projection_with_token_row() -> UsageAuditProjection:
    projection = UsageAuditProjection()
    projection.token_rows[
        ("acct-1", "user-1", "2026-05-12", 1, "vlm", "input", "prov", "model")
    ] = 5
    return projection


class _AutoRollbackInsertFailure:
    """Simulate a fatal SQLite write error that auto-rolled-back the txn."""

    def __init__(self, *, auto_rollback: bool) -> None:
        self.auto_rollback = auto_rollback

    def __call__(self, conn, rows, updated_at):  # signature of _write_token_rows
        if self.auto_rollback:
            conn.execute("ROLLBACK")
        raise sqlite3.OperationalError("disk I/O error")


def test_record_batch_preserves_original_error_when_sqlite_auto_rolled_back(
    tmp_path, monkeypatch
):
    store = _make_store(tmp_path)
    monkeypatch.setattr(
        SQLiteUsageAuditStore,
        "_write_token_rows",
        staticmethod(_AutoRollbackInsertFailure(auto_rollback=True)),
    )

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store._record_projection_sync(_projection_with_token_row())


def test_record_batch_still_rolls_back_active_transaction(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    conn = store._conn
    assert conn is not None
    monkeypatch.setattr(
        SQLiteUsageAuditStore,
        "_write_token_rows",
        staticmethod(_AutoRollbackInsertFailure(auto_rollback=False)),
    )

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store._record_projection_sync(_projection_with_token_row())

    assert conn.in_transaction is False


def test_record_batch_recovers_after_transient_failure(tmp_path, monkeypatch):
    store = _make_store(tmp_path)
    monkeypatch.setattr(
        SQLiteUsageAuditStore,
        "_write_token_rows",
        staticmethod(_AutoRollbackInsertFailure(auto_rollback=True)),
    )

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store._record_projection_sync(_projection_with_token_row())

    # A later batch must succeed on the same connection once the transient
    # storage fault is gone — the failed path must not wedge the store.
    monkeypatch.undo()
    store._record_projection_sync(_projection_with_token_row())


class _DeleteFailingConn:
    """Connection proxy whose first DELETE simulates an auto-rollback fault."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._failed = False

    def execute(self, sql, *args):
        if not self._failed and sql.lstrip().upper().startswith("DELETE"):
            self._failed = True
            self._conn.execute("ROLLBACK")
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_delete_user_data_preserves_original_error_when_sqlite_auto_rolled_back(tmp_path):
    store = _make_store(tmp_path)
    conn = store._conn
    assert conn is not None
    store._conn = _DeleteFailingConn(conn)  # type: ignore[assignment]

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store._delete_user_data_sync("acct-1", "user-1")

    assert conn.in_transaction is False
