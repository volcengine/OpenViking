# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite implementation of the product usage/audit store."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from openviking.observability.events import ObservabilityEvent

from .projection import UsageAuditProjection, project_events, safe_int
from .schema import SQLITE_SCHEMA
from .time import resolve_usage_timezone


def _date_range(start_date: str, end_date: str) -> Iterable[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        return []
    days = (end - start).days
    return ((start + timedelta(days=offset)).isoformat() for offset in range(days + 1))


class SQLiteUsageAuditStore:
    """Async wrapper around a SQLite usage/audit database."""

    def __init__(
        self,
        db_path: Path,
        *,
        usage_retention_days: int = 14,
        audit_retention_days: int = 7,
        audit_retention_per_account: int = 1000,
        timezone_name: str = "local",
    ) -> None:
        self._db_path = Path(db_path)
        self._usage_retention_days = int(usage_retention_days)
        self._audit_retention_days = int(audit_retention_days)
        self._audit_retention_per_account = int(audit_retention_per_account)
        self._tz = resolve_usage_timezone(timezone_name)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SQLITE_SCHEMA)
        self._conn = conn

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def record_batch(self, events: Sequence[ObservabilityEvent]) -> None:
        if not events:
            return
        projection = project_events(events, tz=self._tz)
        async with self._lock:
            await asyncio.to_thread(self._record_projection_sync, projection)

    def _record_projection_sync(self, projection: UsageAuditProjection) -> None:
        assert self._conn is not None
        updated_at = datetime.now(timezone.utc).isoformat()
        conn = self._conn
        conn.execute("BEGIN")
        try:
            self._write_token_rows(conn, projection.token_rows, updated_at)
            self._write_retrieval_rows(conn, projection.retrieval_rows, updated_at)
            self._write_context_rows(conn, projection.context_rows, updated_at)
            self._write_agent_rows(conn, projection.agent_rows, updated_at)
            self._write_audit_rows(conn, projection.audit_rows)
            self._trim_usage_rows(conn, self._usage_max_dates(projection))
            self._trim_audit_rows(conn, projection.touched_audit_accounts)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _write_token_rows(conn, rows: dict[tuple, int], updated_at: str) -> None:
        conn.executemany(
            """
            INSERT INTO usage_token_daily (
                account_id, user_id, agent_id, date, source, token_type,
                provider, model_name, token_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                account_id, user_id, agent_id, date, source, token_type, provider, model_name
            )
            DO UPDATE SET
                token_count = token_count + excluded.token_count,
                updated_at = excluded.updated_at
            """,
            [(*key, value, updated_at) for key, value in rows.items() if value > 0],
        )

    @staticmethod
    def _write_retrieval_rows(conn, rows: dict[tuple, tuple[int, int]], updated_at: str) -> None:
        conn.executemany(
            """
            INSERT INTO usage_retrieval_daily (
                account_id, user_id, agent_id, date, operation, status,
                request_count, result_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_id, user_id, agent_id, date, operation, status)
            DO UPDATE SET
                request_count = request_count + excluded.request_count,
                result_count = result_count + excluded.result_count,
                updated_at = excluded.updated_at
            """,
            [
                (*key, count, result_count, updated_at)
                for key, (count, result_count) in rows.items()
            ],
        )

    @staticmethod
    def _write_context_rows(conn, rows: dict[tuple, int], updated_at: str) -> None:
        conn.executemany(
            """
            INSERT INTO usage_context_write_bucket (
                account_id, user_id, agent_id, date, hour_bucket, operation, count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_id, user_id, agent_id, date, hour_bucket, operation)
            DO UPDATE SET
                count = count + excluded.count,
                updated_at = excluded.updated_at
            """,
            [(*key, value, updated_at) for key, value in rows.items() if value > 0],
        )

    @staticmethod
    def _write_agent_rows(conn, rows: dict[tuple, tuple[int, str]], updated_at: str) -> None:
        conn.executemany(
            """
            INSERT INTO usage_agent_activity_daily (
                account_id, agent_id, date, request_count, last_seen_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_id, agent_id, date)
            DO UPDATE SET
                request_count = request_count + excluded.request_count,
                last_seen_at = MAX(last_seen_at, excluded.last_seen_at),
                updated_at = excluded.updated_at
            """,
            [(*key, count, last_seen, updated_at) for key, (count, last_seen) in rows.items()],
        )

    @staticmethod
    def _write_audit_rows(conn, rows: list[tuple]) -> None:
        conn.executemany(
            """
            INSERT INTO request_audit (
                request_id, account_id, user_id, agent_id, method, route,
                api_type, status_code, duration_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _trim_audit_rows(self, conn, accounts: set[str]) -> None:
        if self._audit_retention_days > 0:
            max_dates = self._audit_max_dates(conn, accounts)
            for account_id, cutoff_date in self._cutoff_dates(
                max_dates,
                retention_days=self._audit_retention_days,
            ).items():
                conn.execute(
                    """
                    DELETE FROM request_audit
                    WHERE account_id = ? AND substr(created_at, 1, 10) < ?
                    """,
                    (account_id, cutoff_date),
                )
        if self._audit_retention_per_account <= 0:
            return
        for account_id in accounts:
            conn.execute(
                """
                DELETE FROM request_audit
                WHERE account_id = ?
                  AND id NOT IN (
                    SELECT id FROM request_audit
                    WHERE account_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
                """,
                (account_id, account_id, self._audit_retention_per_account),
            )

    def _trim_usage_rows(self, conn, max_dates_by_account: dict[str, str]) -> None:
        cutoff_by_account = self._cutoff_dates(
            max_dates_by_account,
            retention_days=self._usage_retention_days,
        )
        for account_id, cutoff_date in cutoff_by_account.items():
            for table in (
                "usage_token_daily",
                "usage_retrieval_daily",
                "usage_context_write_bucket",
                "usage_agent_activity_daily",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE account_id = ? AND date < ?",
                    (account_id, cutoff_date),
                )

    @staticmethod
    def _usage_max_dates(projection: UsageAuditProjection) -> dict[str, str]:
        max_dates: dict[str, str] = {}
        SQLiteUsageAuditStore._merge_max_dates(max_dates, projection.token_rows, date_index=3)
        SQLiteUsageAuditStore._merge_max_dates(max_dates, projection.retrieval_rows, date_index=3)
        SQLiteUsageAuditStore._merge_max_dates(max_dates, projection.context_rows, date_index=3)
        SQLiteUsageAuditStore._merge_max_dates(max_dates, projection.agent_rows, date_index=2)
        return max_dates

    @staticmethod
    def _merge_max_dates(
        target: dict[str, str], rows: dict[tuple, Any], *, date_index: int
    ) -> None:
        for key in rows:
            account_id = str(key[0])
            event_date = str(key[date_index])
            if event_date > target.get(account_id, ""):
                target[account_id] = event_date

    @staticmethod
    def _cutoff_dates(
        max_dates_by_account: dict[str, str], *, retention_days: int
    ) -> dict[str, str]:
        if retention_days <= 0:
            return {}
        return {
            account_id: (
                date.fromisoformat(max_date) - timedelta(days=retention_days - 1)
            ).isoformat()
            for account_id, max_date in max_dates_by_account.items()
        }

    @staticmethod
    def _audit_max_dates(conn, accounts: set[str]) -> dict[str, str]:
        max_dates: dict[str, str] = {}
        for account_id in accounts:
            row = conn.execute(
                """
                SELECT MAX(substr(created_at, 1, 10)) AS max_date
                FROM request_audit
                WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            if row and row["max_date"]:
                max_dates[account_id] = str(row["max_date"])
        return max_dates

    async def get_today_tokens(self, *, account_id: str, date: str) -> dict[str, int]:
        async with self._lock:
            return await asyncio.to_thread(self._get_today_tokens_sync, account_id, date)

    def _get_today_tokens_sync(self, account_id: str, event_date: str) -> dict[str, int]:
        assert self._conn is not None
        cur = self._conn.execute(
            """
            SELECT source, token_type, SUM(token_count) AS total
            FROM usage_token_daily
            WHERE account_id = ? AND date = ?
            GROUP BY source, token_type
            """,
            (account_id, event_date),
        )
        result = {"vlm_input": 0, "vlm_output": 0, "embedding_input": 0}
        for row in cur.fetchall():
            key = f"{row['source']}_{row['token_type']}"
            if key in result:
                result[key] = int(row["total"] or 0)
        result["total"] = sum(result.values())
        return result

    async def get_today_retrievals(self, *, account_id: str, date: str) -> dict[str, int]:
        async with self._lock:
            return await asyncio.to_thread(self._get_today_retrievals_sync, account_id, date)

    def _get_today_retrievals_sync(self, account_id: str, event_date: str) -> dict[str, int]:
        assert self._conn is not None
        cur = self._conn.execute(
            """
            SELECT operation, SUM(request_count) AS total
            FROM usage_retrieval_daily
            WHERE account_id = ? AND date = ? AND status = 'success'
            GROUP BY operation
            """,
            (account_id, event_date),
        )
        result = {"find": 0, "search": 0}
        for row in cur.fetchall():
            operation = str(row["operation"])
            if operation in result:
                result[operation] = int(row["total"] or 0)
        result["total"] = sum(result.values())
        return result

    async def get_agent_overview(
        self, *, account_id: str, date: str, limit: int = 5
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_agent_overview_sync, account_id, date, int(limit)
            )

    def _get_agent_overview_sync(
        self, account_id: str, event_date: str, limit: int
    ) -> dict[str, Any]:
        assert self._conn is not None
        total_cur = self._conn.execute(
            """
            SELECT COUNT(DISTINCT agent_id) AS total
            FROM usage_agent_activity_daily
            WHERE account_id = ? AND date = ?
            """,
            (account_id, event_date),
        )
        total = int(total_cur.fetchone()["total"] or 0)
        cur = self._conn.execute(
            """
            SELECT agent_id, last_seen_at
            FROM usage_agent_activity_daily
            WHERE account_id = ? AND date = ?
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (account_id, event_date, limit),
        )
        return {
            "total": total,
            "items": [
                {"agent_id": row["agent_id"], "last_seen_at": row["last_seen_at"]}
                for row in cur.fetchall()
            ],
        }

    async def get_token_series(
        self, *, account_id: str, start_date: str, end_date: str, bucket: str
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_token_series_sync, account_id, start_date, end_date, bucket
            )

    def _get_token_series_sync(
        self, account_id: str, start_date: str, end_date: str, bucket: str
    ) -> list[dict[str, Any]]:
        assert self._conn is not None
        cur = self._conn.execute(
            """
            SELECT date, source, token_type, SUM(token_count) AS total
            FROM usage_token_daily
            WHERE account_id = ? AND date >= ? AND date <= ?
            GROUP BY date, source, token_type
            """,
            (account_id, start_date, end_date),
        )
        by_date = {
            d: {"date": d, "vlm_input": 0, "vlm_output": 0, "embedding_input": 0}
            for d in _date_range(start_date, end_date)
        }
        for row in cur.fetchall():
            key = f"{row['source']}_{row['token_type']}"
            if key in {"vlm_input", "vlm_output", "embedding_input"}:
                by_date.setdefault(
                    row["date"],
                    {"date": row["date"], "vlm_input": 0, "vlm_output": 0, "embedding_input": 0},
                )[key] = int(row["total"] or 0)
        return list(by_date.values())

    async def get_context_commit_heatmap(
        self, *, account_id: str, start_date: str, end_date: str, bucket: str
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_context_commit_heatmap_sync,
                account_id,
                start_date,
                end_date,
                bucket,
            )

    def _get_context_commit_heatmap_sync(
        self, account_id: str, start_date: str, end_date: str, bucket: str
    ) -> list[dict[str, Any]]:
        assert self._conn is not None
        bucket_size = 4 if bucket == "4h" else 1
        cur = self._conn.execute(
            """
            SELECT date, hour_bucket, operation, SUM(count) AS total
            FROM usage_context_write_bucket
            WHERE account_id = ? AND date >= ? AND date <= ?
            GROUP BY date, hour_bucket, operation
            """,
            (account_id, start_date, end_date),
        )
        rows: dict[tuple[str, int], dict[str, Any]] = {}
        for event_date in _date_range(start_date, end_date):
            for hour in range(0, 24, bucket_size):
                rows[(event_date, hour)] = self._empty_context_row(event_date, hour)
        for row in cur.fetchall():
            hour = int(row["hour_bucket"])
            normalized_hour = (hour // bucket_size) * bucket_size
            key = (row["date"], normalized_hour)
            item = rows.setdefault(key, self._empty_context_row(row["date"], normalized_hour))
            operation_key = str(row["operation"]).replace(".", "_")
            if operation_key in item:
                item[operation_key] += int(row["total"] or 0)
            item["total"] += int(row["total"] or 0)
        return [rows[key] for key in sorted(rows)]

    @staticmethod
    def _empty_context_row(event_date: str, hour: int) -> dict[str, Any]:
        return {
            "date": event_date,
            "hour": hour,
            "total": 0,
            "add_resource": 0,
            "add_skill": 0,
            "session_add_message": 0,
            "session_commit": 0,
        }

    async def query_audit_logs(
        self,
        *,
        account_id: str,
        request_id: str | None = None,
        statuses: list[str] | None = None,
        api_types: list[str] | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._query_audit_logs_sync,
                account_id,
                request_id,
                statuses or [],
                api_types or [],
                int(page),
                int(page_size),
            )

    def _query_audit_logs_sync(
        self,
        account_id: str,
        request_id: str | None,
        statuses: list[str],
        api_types: list[str],
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        assert self._conn is not None
        where = ["account_id = ?"]
        params: list[Any] = [account_id]
        if request_id:
            where.append("request_id = ?")
            params.append(request_id)
        status_clause, status_params = self._status_filter_sql(statuses)
        if status_clause:
            where.append(status_clause)
            params.extend(status_params)
        if api_types:
            placeholders = ", ".join("?" for _ in api_types)
            where.append(f"api_type IN ({placeholders})")
            params.extend(api_types)
        where_sql = " AND ".join(where)
        summary = self._conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status_code >= 200 AND status_code < 400 THEN 1 ELSE 0 END)
                    AS success
            FROM request_audit
            WHERE {where_sql}
            """,
            params,
        ).fetchone()
        total = int(summary["total"] or 0)
        success = int(summary["success"] or 0)
        offset = max(page - 1, 0) * page_size
        rows = self._conn.execute(
            f"""
            SELECT request_id, account_id, user_id, agent_id, method, route, api_type,
                   status_code, duration_ms, created_at
            FROM request_audit
            WHERE {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, offset],
        ).fetchall()
        return {
            "total": total,
            "success_rate": (success / total) if total else 0.0,
            "page": page,
            "page_size": page_size,
            "items": [dict(row) for row in rows],
        }

    @staticmethod
    def _status_filter_sql(statuses: list[str]) -> tuple[str, list[Any]]:
        if not statuses:
            return "", []
        clauses: list[str] = []
        params: list[Any] = []
        for status in statuses:
            value = str(status).strip().lower()
            if not value:
                continue
            if value in {"success", "ok"}:
                clauses.append("(status_code >= 200 AND status_code < 400)")
            elif value == "2xx":
                clauses.append("(status_code >= 200 AND status_code < 300)")
            elif value == "3xx":
                clauses.append("(status_code >= 300 AND status_code < 400)")
            elif value in {"error", "failed"}:
                clauses.append("status_code >= 400")
            elif value.endswith("xx") and len(value) == 3 and value[0].isdigit():
                start = int(value[0]) * 100
                clauses.append("(status_code >= ? AND status_code < ?)")
                params.extend([start, start + 100])
            else:
                clauses.append("status_code = ?")
                params.append(safe_int(value))
        if not clauses:
            return "", []
        return "(" + " OR ".join(clauses) + ")", params
