# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit import sqlite_store as sqlite_store_module
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore

UTC = ZoneInfo("UTC")


def _event(
    event_name: str,
    payload: dict,
    *,
    ts: datetime | None = None,
    user_id: str = "user-1",
) -> ObservabilityEvent:
    return ObservabilityEvent(
        event_name=event_name,
        payload=payload,
        timestamp=ts or datetime(2026, 5, 12, 1, 2, 3, tzinfo=timezone.utc),
        request_id=payload.get("request_id"),
        account_id="acct-1",
        user_id=user_id,
    )


def _request(request_id, route, *, status=200, method="POST", **scope):
    return _event(
        "http.request",
        {
            "request_id": request_id,
            "method": method,
            "route": route,
            "status": str(status),
            "duration_seconds": 0.1,
        },
        **scope,
    )


def _tokens(prompt, *, completion=0, source="vlm", **scope):
    return _event(
        f"{source}.call",
        {
            "provider": "p",
            "model_name": "m",
            "prompt_tokens": prompt,
            "completion_tokens": completion,
        },
        **scope,
    )


def _create_legacy_usage_audit_db(db_path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE _schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO _schema_meta (key, value) VALUES ('version', '3');
            CREATE TABLE usage_token_daily (
                account_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                source TEXT NOT NULL,
                token_type TEXT NOT NULL,
                provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (
                    account_id, user_id, date, source, token_type, provider, model_name
                )
            );
            CREATE TABLE usage_retrieval_daily (
                account_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                result_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, user_id, date, operation, status)
            );
            CREATE TABLE usage_context_write_bucket (
                account_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                date TEXT NOT NULL,
                hour_bucket INTEGER NOT NULL,
                operation TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, user_id, date, hour_bucket, operation)
            );
            CREATE TABLE request_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                account_id TEXT NOT NULL,
                user_id TEXT,
                method TEXT NOT NULL,
                route TEXT NOT NULL,
                api_type TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO request_audit (
                request_id, account_id, user_id, method, route,
                api_type, status_code, duration_ms, created_at
            )
            VALUES (
                'legacy-req', 'acct-1', 'user-1', 'GET',
                '/api/v1/system/status', 'system', 200, 1.0,
                '2026-05-11T00:00:00+08:00'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _create_v4_usage_audit_db(db_path) -> None:
    """Create and populate the exact compatible layout shipped by schema v4."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE _schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO _schema_meta (key, value) VALUES ('version', '4');

            CREATE TABLE usage_token_hourly (
                account_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                date_utc TEXT NOT NULL,
                hour_utc INTEGER NOT NULL,
                source TEXT NOT NULL,
                token_type TEXT NOT NULL,
                provider TEXT NOT NULL,
                model_name TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (
                    account_id, user_id, date_utc, hour_utc,
                    source, token_type, provider, model_name
                )
            );
            CREATE TABLE usage_retrieval_hourly (
                account_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                date_utc TEXT NOT NULL,
                hour_utc INTEGER NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                result_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (
                    account_id, user_id, date_utc, hour_utc, operation, status
                )
            );
            CREATE TABLE usage_context_write_bucket (
                account_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                date_utc TEXT NOT NULL,
                hour_utc INTEGER NOT NULL,
                operation TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (account_id, user_id, date_utc, hour_utc, operation)
            );
            CREATE TABLE request_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                account_id TEXT NOT NULL,
                user_id TEXT,
                method TEXT NOT NULL,
                route TEXT NOT NULL,
                api_type TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                created_at TEXT NOT NULL
            );

            INSERT INTO usage_token_hourly VALUES (
                'acct-1', 'user-1', '2026-05-12', 1,
                'vlm', 'input', 'p', 'm', 11, '2026-05-12T01:02:03+00:00'
            );
            INSERT INTO usage_retrieval_hourly VALUES (
                'acct-1', 'user-1', '2026-05-12', 1,
                'find', 'success', 2, 0, '2026-05-12T01:02:03+00:00'
            );
            INSERT INTO usage_context_write_bucket VALUES (
                'acct-1', 'user-1', '2026-05-12', 1,
                'session.commit', 3, '2026-05-12T01:02:03+00:00'
            );
            INSERT INTO request_audit (
                request_id, account_id, user_id, method, route,
                api_type, status_code, duration_ms, created_at
            ) VALUES (
                'v4-request', 'acct-1', 'user-1', 'GET',
                '/api/v1/system/status', 'system', 200, 1.0,
                '2026-05-12T01:02:03+00:00'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_aggregates_dashboard_data(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    messages = "/api/v1/sessions/{session_id}/messages"
    day = "2026-05-12"
    try:
        await store.record_batch(
            [
                _tokens(3, completion=2),
                _tokens(5, source="embedding"),
                _tokens(100, source="embedding", user_id="user-2"),
                _request("req-find", "/api/v1/search/find"),
                _request("req-search-user-2", "/api/v1/search/search", user_id="user-2"),
                _request("req-message", messages),
                _request("req-message-user-2", messages, user_id="user-2"),
                _request("req-console", "/api/v1/console/dashboard/summary", method="GET"),
                _request("req-docs", "/docs", method="GET"),
                _request("req-favicon", "/favicon.ico", method="GET"),
            ]
        )

        for user, embedding, searches, commits, request_ids in (
            (
                None,
                105,
                1,
                2,
                {"req-find", "req-search-user-2", "req-message", "req-message-user-2"},
            ),
            ("user-1", 5, 0, 1, {"req-find", "req-message"}),
        ):
            assert await store.get_today_tokens(
                account_id="acct-1", user_id=user, user_date=day, tz=UTC
            ) == {
                "vlm_input": 3,
                "vlm_output": 2,
                "embedding_input": embedding,
                "total": 5 + embedding,
            }
            assert await store.get_today_retrievals(
                account_id="acct-1", user_id=user, user_date=day, tz=UTC
            ) == {
                "find": 1,
                "search": searches,
                "total": 1 + searches,
            }
            hours = await store.get_context_commit_heatmap(
                account_id="acct-1",
                user_id=user,
                start_user_date=day,
                end_user_date=day,
                bucket="hour",
                tz=UTC,
            )
            assert {
                hour["hour"]: hour["session_add_message"] for hour in hours if hour["total"]
            } == {1: commits}
            audit = await store.query_audit_logs(account_id="acct-1", user_id=user)
            assert audit["total"] == len(request_ids) and audit["success_rate"] == 1.0
            assert {item["request_id"] for item in audit["items"]} == request_ids
            assert {item["api_type"] for item in audit["items"]} == (
                {"search.find", "search.search", "sessions"}
                if searches
                else {"search.find", "sessions"}
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_resets_incompatible_legacy_schema(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    _create_legacy_usage_audit_db(db_path)

    store = SQLiteUsageAuditStore(db_path)
    await store.initialize()
    try:
        await store.record_batch(
            [
                _event(
                    "vlm.call",
                    {
                        "provider": "p",
                        "model_name": "m",
                        "prompt_tokens": 8,
                        "completion_tokens": 2,
                    },
                ),
                _event(
                    "http.request",
                    {
                        "request_id": "req-message",
                        "method": "POST",
                        "route": "/api/v1/sessions/{session_id}/messages",
                        "status": "200",
                        "duration_seconds": 0.1,
                    },
                ),
            ]
        )

        assert await store.get_today_tokens(
            account_id="acct-1", user_date="2026-05-12", tz=UTC
        ) == {
            "vlm_input": 8,
            "vlm_output": 2,
            "embedding_input": 0,
            "total": 10,
        }
        commits = await store.get_context_commit_heatmap(
            account_id="acct-1",
            start_user_date="2026-05-12",
            end_user_date="2026-05-12",
            bucket="hour",
            tz=UTC,
        )
        assert any(row["hour"] == 1 and row["session_add_message"] == 1 for row in commits)
        audit = await store.query_audit_logs(account_id="acct-1", page_size=10)
        assert audit["total"] == 1
        assert audit["items"][0]["request_id"] == "req-message"
    finally:
        await store.close()

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "usage_token_daily" not in tables
        assert "usage_retrieval_daily" not in tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(usage_context_write_bucket)")}
        assert "date_utc" in columns
        assert "date" not in columns
        context_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(usage_context_write_bucket)")
        }
        assert "hour_utc" in context_columns
        assert "hour_bucket" not in context_columns
        version = conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'").fetchone()
        assert version == ("5",)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_migrates_v4_without_losing_rows(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    _create_v4_usage_audit_db(db_path)

    store = SQLiteUsageAuditStore(db_path)
    await store.initialize()
    try:
        tokens = await store.get_today_tokens(
            account_id="acct-1",
            user_id="user-1",
            user_date="2026-05-12",
            tz=UTC,
        )
        retrievals = await store.get_today_retrievals(
            account_id="acct-1",
            user_id="user-1",
            user_date="2026-05-12",
            tz=UTC,
        )
        commits = await store.get_context_commit_heatmap(
            account_id="acct-1",
            user_id="user-1",
            start_user_date="2026-05-12",
            end_user_date="2026-05-12",
            bucket="hour",
            tz=UTC,
        )
        before = await store.query_audit_logs(account_id="acct-1", page_size=10)

        assert tokens["vlm_input"] == 11
        assert retrievals["find"] == 2
        assert any(row["session_commit"] == 3 for row in commits)
        assert before["items"][0]["request_id"] == "v4-request"
        assert before["items"][0]["error_code"] is None
        assert before["items"][0]["error_message"] is None
        assert before["items"][0]["error_details"] is None

        await store.record_batch(
            [
                _event(
                    "http.request",
                    {
                        "request_id": "new-error",
                        "method": "GET",
                        "route": "/api/v1/tasks/{task_id}",
                        "status": "404",
                        "duration_seconds": 0.01,
                        "error_code": "NOT_FOUND",
                        "error_message": "Task was not found",
                        "error_details": {"task_id": "missing"},
                    },
                )
            ]
        )
        after = await store.query_audit_logs(account_id="acct-1", page_size=10)
        inserted = next(item for item in after["items"] if item["request_id"] == "new-error")
        assert inserted["error_code"] == "NOT_FOUND"
        assert inserted["error_message"] == "Task was not found"
        assert inserted["error_details"] == {"task_id": "missing"}
        assert {item["request_id"] for item in after["items"]} == {
            "v4-request",
            "new-error",
        }
    finally:
        await store.close()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(request_audit)")}
        assert {"error_code", "error_message", "error_details"} <= columns
        version = conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'").fetchone()
        assert version == ("5",)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_rejects_unhandled_future_migration_without_data_loss(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "usage.sqlite3"
    _create_v4_usage_audit_db(db_path)

    store = SQLiteUsageAuditStore(db_path)
    await store.initialize()
    await store.close()

    monkeypatch.setattr(sqlite_store_module, "SCHEMA_VERSION", 6)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(
            RuntimeError,
            match="No usage/audit schema migration path from version 5 to 6",
        ):
            SQLiteUsageAuditStore._migrate_legacy_sync(conn)

        assert conn.execute("SELECT COUNT(*) FROM request_audit").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM usage_token_hourly").fetchone()[0] == 1
        version = conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'").fetchone()
        assert version[0] == "5"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_handles_malformed_error_details(tmp_path):
    db_path = tmp_path / "usage.sqlite3"
    store = SQLiteUsageAuditStore(db_path)
    await store.initialize()
    try:
        assert store._conn is not None
        store._conn.execute(
            """
            INSERT INTO request_audit (
                request_id, account_id, user_id, method, route, api_type,
                status_code, duration_ms, error_code, error_message,
                error_details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "malformed",
                "acct-1",
                "user-1",
                "GET",
                "/api/v1/tasks/{task_id}",
                "tasks",
                404,
                1.0,
                "NOT_FOUND",
                "Task was not found",
                "not-json",
                "2026-05-12T01:02:03+00:00",
            ),
        )

        audit = await store.query_audit_logs(account_id="acct-1")
        assert audit["items"][0]["error_details"] is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_trims_audit_per_account(tmp_path):
    store = SQLiteUsageAuditStore(
        tmp_path / "usage.sqlite3",
        audit_retention_per_account=2,
    )
    await store.initialize()
    try:
        await store.record_batch(
            [_request(f"req-{idx}", "/api/v1/system/status", method="GET") for idx in range(3)]
        )
        audit = await store.query_audit_logs(account_id="acct-1", page_size=10)
        assert audit["total"] == 2
        assert [item["request_id"] for item in audit["items"]] == ["req-2", "req-1"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_usage_audit_store_success_filter_includes_3xx(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    try:
        await store.record_batch(
            [
                _request(f"req-{status}", "/api/v1/system/status", method="GET", status=status)
                for status in (200, 302, 500)
            ]
        )

        success = await store.query_audit_logs(
            account_id="acct-1",
            statuses=["success"],
            page_size=10,
        )
        explicit_2xx = await store.query_audit_logs(
            account_id="acct-1",
            statuses=["2xx"],
            page_size=10,
        )

        assert success["total"] == 2
        assert {item["request_id"] for item in success["items"]} == {"req-200", "req-302"}
        assert explicit_2xx["total"] == 1
        assert explicit_2xx["items"][0]["request_id"] == "req-200"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_usage_queries_rebucket_utc_events_to_viewer_timezones(tmp_path):
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    shanghai = ZoneInfo("Asia/Shanghai")
    may13 = "2026-05-13"
    late_utc = datetime(2026, 5, 12, 23, 30, tzinfo=UTC)
    try:
        await store.record_batch(
            [
                _tokens(7, ts=datetime(2026, 5, 12, 3, tzinfo=UTC)),  # May 11 in New York
                _tokens(11, ts=late_utc),
                _tokens(22, ts=datetime(2026, 5, 13, 4, 30, tzinfo=UTC)),
                _tokens(9999, ts=datetime(2026, 5, 13, 16, 30, tzinfo=UTC)),  # May 14 in Shanghai
                _request("req-late", "/api/v1/sessions/{session_id}/messages", ts=late_utc),
            ]
        )

        sh_today = await store.get_today_tokens(account_id="acct-1", user_date=may13, tz=shanghai)
        utc_today = await store.get_today_tokens(account_id="acct-1", user_date=may13, tz=UTC)
        assert sh_today["vlm_input"] == sh_today["total"] == 33
        assert utc_today["vlm_input"] == 22 + 9999

        series = await store.get_token_series(
            account_id="acct-1",
            start_user_date="2026-05-10",
            end_user_date="2026-05-12",
            bucket="day",
            tz=ZoneInfo("America/New_York"),
        )
        # 04:30 UTC on May 13 is after midnight local, so falls beyond the requested range.
        assert {row["date"]: row["vlm_input"] for row in series} == {
            "2026-05-10": 0,
            "2026-05-11": 7,
            "2026-05-12": 11,
        }

        commits = await store.get_context_commit_heatmap(
            account_id="acct-1",
            start_user_date=may13,
            end_user_date=may13,
            bucket="hour",
            tz=shanghai,
        )
        assert [
            (row["date"], row["hour"], row["session_add_message"], row["total"])
            for row in commits
            if row["total"]
        ] == [(may13, 7, 1, 1)]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_usage_queries_handle_sub_hour_timezone_offsets(tmp_path):
    """A local day in a half-hour-offset zone starts and ends mid UTC hour.

    An hourly row belongs to the local date of its hour start. Window queries
    that compare only the hour number used to pull in rows from the previous
    local day and drop the current day's last partial hour, so `today`
    disagreed with `series`/heatmap for the same local date.
    """
    store = SQLiteUsageAuditStore(tmp_path / "usage.sqlite3")
    await store.initialize()
    kolkata = ZoneInfo("Asia/Kolkata")
    try:
        await store.record_batch(
            [
                # 23:40 on May 12 in Kolkata — previous local day.
                _tokens(10, ts=datetime(2026, 5, 12, 18, 10, tzinfo=UTC)),
                # 23:40 on May 13 in Kolkata — inside the requested local day.
                _tokens(20, ts=datetime(2026, 5, 13, 18, 10, tzinfo=UTC)),
                _request(
                    "req-kolkata-commit",
                    "/api/v1/sessions/{session_id}/messages",
                    ts=datetime(2026, 5, 13, 18, 10, tzinfo=UTC),
                ),
            ]
        )

        today = await store.get_today_tokens(
            account_id="acct-1", user_date="2026-05-13", tz=kolkata
        )
        assert today["vlm_input"] == today["total"] == 20

        series = await store.get_token_series(
            account_id="acct-1",
            start_user_date="2026-05-13",
            end_user_date="2026-05-13",
            bucket="day",
            tz=kolkata,
        )
        assert {row["date"]: row["vlm_input"] for row in series} == {"2026-05-13": 20}

        commits = await store.get_context_commit_heatmap(
            account_id="acct-1",
            start_user_date="2026-05-13",
            end_user_date="2026-05-13",
            bucket="hour",
            tz=kolkata,
        )
        assert [
            (row["date"], row["hour"], row["session_add_message"])
            for row in commits
            if row["total"]
        ] == [("2026-05-13", 23, 1)]
    finally:
        await store.close()
