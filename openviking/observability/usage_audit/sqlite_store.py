# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""SQLite implementation of the product usage/audit store.

All time-keyed columns are persisted in UTC. Read methods accept a
viewer-supplied `tz` (an `Iana zoneinfo`-style `tzinfo`) and rebucket the
underlying hourly rows into user-local days / hours.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Iterable, Sequence

from openviking.observability.events import ObservabilityEvent

from .projection import UsageAuditProjection, project_events, safe_int
from .schema import RESET_ON_SCHEMA_UPGRADE_TABLES, SCHEMA_VERSION, SQLITE_SCHEMA

UTC = timezone.utc


def _date_range(start_date: str, end_date: str) -> Iterable[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        return []
    days = (end - start).days
    return ((start + timedelta(days=offset)).isoformat() for offset in range(days + 1))


def _user_day_window_utc(day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """Return the UTC `[start, end)` instants spanning one user-tz day."""
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


class SQLiteUsageAuditStore:
    """Async wrapper around a SQLite usage/audit database."""

    def __init__(
        self,
        db_path: Path,
        *,
        usage_retention_days: int = 14,
        audit_retention_days: int = 7,
        audit_retention_per_account: int = 1000,
    ) -> None:
        self._db_path = Path(db_path)
        self._usage_retention_days = int(usage_retention_days)
        self._audit_retention_days = int(audit_retention_days)
        self._audit_retention_per_account = int(audit_retention_per_account)
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
        # Reset incompatible pre-v3 local tables before creating the new
        # UTC/hourly layout. The SQLite backend has short retention and may live
        # inside one workspace, so dropping stale rollups is safer than mixing
        # old daily/local columns with the new UTC columns.
        self._migrate_legacy_sync(conn)
        conn.executescript(SQLITE_SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
            ("version", str(SCHEMA_VERSION)),
        )
        self._conn = conn

    @staticmethod
    def _migrate_legacy_sync(conn: sqlite3.Connection) -> None:
        try:
            row = conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'").fetchone()
        except sqlite3.OperationalError:
            row = None
        current = int(row["value"]) if row and row["value"] else 0
        if current >= SCHEMA_VERSION:
            return
        for table in RESET_ON_SCHEMA_UPGRADE_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def record_batch(self, events: Sequence[ObservabilityEvent]) -> None:
        if not events:
            return
        projection = project_events(events)
        async with self._lock:
            await asyncio.to_thread(self._record_projection_sync, projection)

    def _record_projection_sync(self, projection: UsageAuditProjection) -> None:
        assert self._conn is not None
        updated_at = datetime.now(UTC).isoformat()
        conn = self._conn
        conn.execute("BEGIN")
        try:
            self._write_token_rows(conn, projection.token_rows, updated_at)
            self._write_retrieval_rows(conn, projection.retrieval_rows, updated_at)
            self._write_context_rows(conn, projection.context_rows, updated_at)
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
            INSERT INTO usage_token_hourly (
                account_id, user_id, date_utc, hour_utc,
                source, token_type, provider, model_name,
                token_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                account_id, user_id, date_utc, hour_utc,
                source, token_type, provider, model_name
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
            INSERT INTO usage_retrieval_hourly (
                account_id, user_id, date_utc, hour_utc,
                operation, status, request_count, result_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                account_id, user_id, date_utc, hour_utc, operation, status
            )
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
                account_id, user_id, date_utc, hour_utc,
                operation, count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                account_id, user_id, date_utc, hour_utc, operation
            )
            DO UPDATE SET
                count = count + excluded.count,
                updated_at = excluded.updated_at
            """,
            [(*key, value, updated_at) for key, value in rows.items() if value > 0],
        )

    @staticmethod
    def _write_audit_rows(conn, rows: list[tuple]) -> None:
        conn.executemany(
            """
            INSERT INTO request_audit (
                request_id, account_id, user_id, method, route,
                api_type, status_code, duration_ms, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "usage_token_hourly",
                "usage_retrieval_hourly",
                "usage_context_write_bucket",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE account_id = ? AND date_utc < ?",
                    (account_id, cutoff_date),
                )

    @staticmethod
    def _usage_max_dates(projection: UsageAuditProjection) -> dict[str, str]:
        max_dates: dict[str, str] = {}
        # token_rows: (account, user, date_utc, hour_utc, source, ...)
        SQLiteUsageAuditStore._merge_max_dates(max_dates, projection.token_rows, date_index=2)
        # retrieval_rows: (account, user, date_utc, hour_utc, op, status)
        SQLiteUsageAuditStore._merge_max_dates(max_dates, projection.retrieval_rows, date_index=2)
        # context_rows: (account, user, date_utc, hour_utc, op)
        SQLiteUsageAuditStore._merge_max_dates(max_dates, projection.context_rows, date_index=2)
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

    # ------------------------------------------------------------------
    # Read API. All `*_user_date` arguments are interpreted in the supplied
    # viewer timezone (`tz`). The underlying rows are stored in UTC and
    # rebucketed in Python after a windowed SQL query.
    # ------------------------------------------------------------------

    async def get_today_tokens(
        self, *, account_id: str, user_date: str, tz: tzinfo
    ) -> dict[str, int]:
        async with self._lock:
            return await asyncio.to_thread(self._get_today_tokens_sync, account_id, user_date, tz)

    def _get_today_tokens_sync(self, account_id: str, user_date: str, tz: tzinfo) -> dict[str, int]:
        assert self._conn is not None
        day = date.fromisoformat(user_date)
        utc_start, utc_end = _user_day_window_utc(day, tz)
        rows = self._fetch_hourly_token_rows(account_id, utc_start, utc_end)
        result = {"vlm_input": 0, "vlm_output": 0, "embedding_input": 0}
        for source, token_type, _, _, total in rows:
            key = f"{source}_{token_type}"
            if key in result:
                result[key] += total
        result["total"] = sum(result.values())
        return result

    async def get_today_retrievals(
        self, *, account_id: str, user_date: str, tz: tzinfo
    ) -> dict[str, int]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_today_retrievals_sync, account_id, user_date, tz
            )

    def _get_today_retrievals_sync(
        self, account_id: str, user_date: str, tz: tzinfo
    ) -> dict[str, int]:
        assert self._conn is not None
        day = date.fromisoformat(user_date)
        utc_start, utc_end = _user_day_window_utc(day, tz)
        result = {"find": 0, "search": 0}
        for operation, total in self._fetch_hourly_retrieval_rows(account_id, utc_start, utc_end):
            if operation in result:
                result[operation] += total
        result["total"] = sum(result.values())
        return result

    async def get_token_series(
        self,
        *,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_token_series_sync,
                account_id,
                start_user_date,
                end_user_date,
                bucket,
                tz,
            )

    def _get_token_series_sync(
        self,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
    ) -> list[dict[str, Any]]:
        assert self._conn is not None
        start_day = date.fromisoformat(start_user_date)
        end_day = date.fromisoformat(end_user_date)
        utc_start, _ = _user_day_window_utc(start_day, tz)
        _, utc_end = _user_day_window_utc(end_day, tz)
        by_date: dict[str, dict[str, Any]] = {
            d: {"date": d, "vlm_input": 0, "vlm_output": 0, "embedding_input": 0}
            for d in _date_range(start_user_date, end_user_date)
        }
        for source, token_type, date_utc, hour_utc, total in self._fetch_hourly_token_rows(
            account_id, utc_start, utc_end
        ):
            local_date = (
                datetime(
                    *_parse_ymd(date_utc),
                    hour_utc,
                    tzinfo=UTC,
                )
                .astimezone(tz)
                .date()
                .isoformat()
            )
            slot = by_date.setdefault(
                local_date,
                {"date": local_date, "vlm_input": 0, "vlm_output": 0, "embedding_input": 0},
            )
            key = f"{source}_{token_type}"
            if key in slot:
                slot[key] += total
        # Drop any local dates outside the requested range (a UTC hour at the
        # boundary may rebucket to a neighbour day after tz conversion).
        return [by_date[d] for d in _date_range(start_user_date, end_user_date) if d in by_date]

    async def get_context_commit_heatmap(
        self,
        *,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._get_context_commit_heatmap_sync,
                account_id,
                start_user_date,
                end_user_date,
                bucket,
                tz,
            )

    def _get_context_commit_heatmap_sync(
        self,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
    ) -> list[dict[str, Any]]:
        assert self._conn is not None
        bucket_size = 4 if bucket == "4h" else 1
        start_day = date.fromisoformat(start_user_date)
        end_day = date.fromisoformat(end_user_date)
        utc_start, _ = _user_day_window_utc(start_day, tz)
        _, utc_end = _user_day_window_utc(end_day, tz)
        rows: dict[tuple[str, int], dict[str, Any]] = {}
        for event_date in _date_range(start_user_date, end_user_date):
            for hour in range(0, 24, bucket_size):
                rows[(event_date, hour)] = self._empty_context_row(event_date, hour)
        cur = self._conn.execute(
            """
            SELECT date_utc, hour_utc, operation, SUM(count) AS total
            FROM usage_context_write_bucket
            WHERE account_id = ?
              AND (
                date_utc > ? OR (date_utc = ? AND hour_utc >= ?)
              )
              AND (
                date_utc < ? OR (date_utc = ? AND hour_utc < ?)
              )
            GROUP BY date_utc, hour_utc, operation
            """,
            (
                account_id,
                utc_start.date().isoformat(),
                utc_start.date().isoformat(),
                utc_start.hour,
                utc_end.date().isoformat(),
                utc_end.date().isoformat(),
                utc_end.hour,
            ),
        )
        for row in cur.fetchall():
            local_dt = datetime(
                *_parse_ymd(row["date_utc"]),
                int(row["hour_utc"]),
                tzinfo=UTC,
            ).astimezone(tz)
            local_date = local_dt.date().isoformat()
            local_hour = local_dt.hour
            normalized_hour = (local_hour // bucket_size) * bucket_size
            key = (local_date, normalized_hour)
            item = rows.setdefault(key, self._empty_context_row(local_date, normalized_hour))
            operation_key = str(row["operation"]).replace(".", "_")
            value = int(row["total"] or 0)
            if operation_key in item:
                item[operation_key] += value
            item["total"] += value
        # Only return rows inside the requested user-tz range; UTC hours at
        # boundaries that fall outside the range after rebucketing are dropped.
        in_range = set(_date_range(start_user_date, end_user_date))
        return [rows[key] for key in sorted(rows) if key[0] in in_range]

    def _fetch_hourly_token_rows(
        self, account_id: str, utc_start: datetime, utc_end: datetime
    ) -> list[tuple[str, str, str, int, int]]:
        """Return rows [(source, token_type, date_utc, hour_utc, total)]."""
        assert self._conn is not None
        utc_start_d = utc_start.date().isoformat()
        utc_end_d = utc_end.date().isoformat()
        cur = self._conn.execute(
            """
            SELECT source, token_type, date_utc, hour_utc, SUM(token_count) AS total
            FROM usage_token_hourly
            WHERE account_id = ?
              AND (date_utc > ? OR (date_utc = ? AND hour_utc >= ?))
              AND (date_utc < ? OR (date_utc = ? AND hour_utc < ?))
            GROUP BY source, token_type, date_utc, hour_utc
            """,
            (
                account_id,
                utc_start_d,
                utc_start_d,
                utc_start.hour,
                utc_end_d,
                utc_end_d,
                utc_end.hour,
            ),
        )
        return [
            (
                str(row["source"]),
                str(row["token_type"]),
                str(row["date_utc"]),
                int(row["hour_utc"]),
                int(row["total"] or 0),
            )
            for row in cur.fetchall()
        ]

    def _fetch_hourly_retrieval_rows(
        self, account_id: str, utc_start: datetime, utc_end: datetime
    ) -> list[tuple[str, int]]:
        """Return [(operation, total_request_count)] for successful retrievals."""
        assert self._conn is not None
        utc_start_d = utc_start.date().isoformat()
        utc_end_d = utc_end.date().isoformat()
        cur = self._conn.execute(
            """
            SELECT operation, SUM(request_count) AS total
            FROM usage_retrieval_hourly
            WHERE account_id = ?
              AND status = 'success'
              AND (date_utc > ? OR (date_utc = ? AND hour_utc >= ?))
              AND (date_utc < ? OR (date_utc = ? AND hour_utc < ?))
            GROUP BY operation
            """,
            (
                account_id,
                utc_start_d,
                utc_start_d,
                utc_start.hour,
                utc_end_d,
                utc_end_d,
                utc_end.hour,
            ),
        )
        return [(str(row["operation"]), int(row["total"] or 0)) for row in cur.fetchall()]

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
            SELECT request_id, account_id, user_id, method, route, api_type,
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


def _parse_ymd(date_str: str) -> tuple[int, int, int]:
    parts = date_str.split("-")
    return int(parts[0]), int(parts[1]), int(parts[2])
