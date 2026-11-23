# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared PostgreSQL projections with transaction-owned account serialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Sequence

from sqlalchemy import BigInteger, Text, cast, delete, func, inspect, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, NoSuchTableError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from openviking.observability.events import ObservabilityEvent
from openviking.storage.postgres_config import postgres_connection_options
from openviking.storage.sql_runtime import (
    get_shared_sql_runtime,
    sql_runtime_store,
    wait_sql_cleanup,
)

from .postgres_schema import (
    DEFAULT_SCHEMA,
    POSTGRES_SCHEMA_VERSION,
    PROVISION_SQL,
    make_tables,
    validate_lock_timeout,
)
from .projection import normalize_identity, project_events, safe_int

UTC = timezone.utc
_USAGE_TABLES = ("usage_token_hourly", "usage_retrieval_hourly", "usage_context_write_bucket")


@dataclass(frozen=True, slots=True)
class _FencedEvent(ObservabilityEvent):
    """Immutable admission version; never refresh an already queued event."""

    deletion_generation: int = 0


def _event_scope(event: ObservabilityEvent) -> tuple[str, str]:
    if event.event_name == "http.request":
        return (
            normalize_identity(event.payload.get("account_id") or event.account_id, unknown=True),
            normalize_identity(event.payload.get("user_id") or event.user_id),
        )
    return normalize_identity(event.account_id, unknown=True), normalize_identity(event.user_id)


def _days(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def _window(start: str, end: str, tz: tzinfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(date.fromisoformat(start), time.min, tzinfo=tz).astimezone(UTC),
        datetime.combine(
            date.fromisoformat(end) + timedelta(days=1), time.min, tzinfo=tz
        ).astimezone(UTC),
    )


def _token_slot(day: str | None = None) -> dict[str, Any]:
    return {
        **({"date": day} if day is not None else {}),
        "vlm_input": 0,
        "vlm_output": 0,
        "embedding_input": 0,
    }


def _context_slot(day: str, hour: int) -> dict[str, Any]:
    return {
        "date": day,
        "hour": hour,
        "total": 0,
        "add_resource": 0,
        "add_skill": 0,
        "session_add_message": 0,
        "session_commit": 0,
    }


@sql_runtime_store
class PostgresUsageAuditStore:
    """Implement UsageAuditStore; startup only validates administrator-owned DDL."""

    def __init__(
        self,
        params: dict[str, object],
        *,
        usage_retention_days: int = 14,
        audit_retention_days: int = 7,
        audit_retention_per_account: int = 1000,
    ) -> None:
        values = dict(params)
        self._schema = values.pop("schema", DEFAULT_SCHEMA)
        self._lock_timeout_ms = validate_lock_timeout(values.pop("lock_timeout_ms", 1000))
        self._tables = make_tables(self._schema)
        self._url, self._engine_options = postgres_connection_options(values, context="usage/audit")
        self._usage_retention_days = int(usage_retention_days)
        self._audit_retention_days = int(audit_retention_days)
        self._audit_retention_per_account = int(audit_retention_per_account)
        self._sql_runtime = get_shared_sql_runtime()
        self._sql_lease = False
        self._engine: AsyncEngine | None = None
        self._deletion_generation = 0

    async def initialize(self) -> None:
        if self._engine is not None:
            return
        await self._sql_runtime.acquire()
        self._sql_lease = True
        engine = None
        try:
            engine = create_async_engine(self._url, **self._engine_options)
            async with engine.connect() as conn:
                version = await conn.scalar(
                    select(self._tables["_schema_meta"].c.value).where(
                        self._tables["_schema_meta"].c.key == "version"
                    )
                )
                if version != str(POSTGRES_SCHEMA_VERSION):
                    raise RuntimeError(
                        f"Unsupported PostgreSQL usage/audit schema version: {version!r}; provision {PROVISION_SQL}"
                    )
                await conn.run_sync(self._validate_schema)
                for table in self._tables.values():
                    await conn.execute(select(*table.columns).limit(0))
                self._deletion_generation = await self._read_generation(conn)
        except BaseException as exc:
            await wait_sql_cleanup(self._cleanup(engine))
            if isinstance(exc, ImportError):
                raise RuntimeError("PostgreSQL usage/audit requires openviking[postgres]") from None
            if isinstance(exc, NoSuchTableError) or (
                isinstance(exc, DBAPIError)
                and getattr(exc.orig, "sqlstate", None) in {"42P01", "42703", "3F000"}
            ):
                raise RuntimeError(
                    f"PostgreSQL usage/audit schema is missing or incompatible; provision {PROVISION_SQL}"
                ) from exc
            raise
        self._engine = engine

    def _validate_schema(self, conn) -> None:
        inspector = inspect(conn)
        for table in self._tables.values():
            columns = {
                column["name"]: column
                for column in inspector.get_columns(table.name, schema=self._schema)
            }
            collations = dict(
                conn.execute(
                    text("""
                SELECT a.attname, col.collname
                FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_collation col ON col.oid = a.attcollation
                WHERE n.nspname = :schema AND c.relname = :table AND a.attnum > 0
            """),
                    {"schema": self._schema, "table": table.name},
                )
                .tuples()
                .all()
            )
            primary = inspector.get_pk_constraint(table.name, schema=self._schema)[
                "constrained_columns"
            ]
            if set(primary) != {column.name for column in table.primary_key}:
                raise RuntimeError(
                    f"Incompatible PostgreSQL usage/audit primary key: {table.name}; provision {PROVISION_SQL}"
                )
            for column in table.columns:
                actual = columns.get(column.name)
                type_matches = actual is not None and (
                    isinstance(actual["type"], Text)
                    if isinstance(column.type, Text)
                    else actual["type"].compile(conn.dialect) == column.type.compile(conn.dialect)
                )
                if not type_matches or actual["nullable"] != column.nullable:
                    raise RuntimeError(
                        f"Incompatible PostgreSQL usage/audit column: {table.name}.{column.name}; provision {PROVISION_SQL}"
                    )
                if column.name == "id" and not actual.get("identity"):
                    raise RuntimeError(
                        f"PostgreSQL usage/audit requires an identity audit id; provision {PROVISION_SQL}"
                    )
                if isinstance(column.type, Text) and collations.get(column.name) != "C":
                    raise RuntimeError(
                        f"PostgreSQL usage/audit requires C text collation; provision {PROVISION_SQL}"
                    )

    async def _cleanup(self, engine: AsyncEngine | None) -> None:
        try:
            if engine is not None:
                await engine.dispose()
        finally:
            if self._sql_lease:
                self._sql_lease = False
                await self._sql_runtime.release()

    async def close(self) -> None:
        engine, self._engine = self._engine, None
        await wait_sql_cleanup(self._cleanup(engine))

    def _require_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("PostgreSQL usage/audit store is not initialized")
        return self._engine

    def prepare_event(self, event: ObservabilityEvent) -> _FencedEvent:
        """Capture the local admission version without synchronous database I/O.

        Every write refreshes the observed version. After a remote deletion,
        events admitted before that refresh are conservatively discarded for
        the deleted scope, including events already waiting in another queue.
        """
        if isinstance(event, _FencedEvent):
            return event
        return _FencedEvent(
            event_name=event.event_name,
            payload=dict(event.payload),
            timestamp=event.timestamp,
            request_id=event.request_id,
            account_id=event.account_id,
            user_id=event.user_id,
            deletion_generation=self._deletion_generation,
        )

    async def _read_generation(self, conn) -> int:
        table = self._tables["_schema_meta"]
        value = await conn.scalar(select(table.c.value).where(table.c.key == "deletion_generation"))
        try:
            generation = int(value)
        except (TypeError, ValueError):
            raise RuntimeError(
                f"Invalid PostgreSQL usage/audit deletion generation; provision {PROVISION_SQL}"
            ) from None
        if generation < 0:
            raise RuntimeError(
                f"Invalid PostgreSQL usage/audit deletion generation; provision {PROVISION_SQL}"
            )
        return generation

    async def _lock_accounts(self, conn, accounts) -> None:
        # Transaction-local setting also bounds locks taken by inserts/deletes.
        await conn.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{self._lock_timeout_ms}ms"},
        )
        # Stable across processes. Lock order uses the hash itself, so even a
        # hash collision cannot introduce inverse acquisition order.
        keys = {
            int.from_bytes(
                hashlib.sha256(
                    json.dumps(
                        ["openviking_usage_audit", self._schema, account], ensure_ascii=False
                    ).encode()
                ).digest()[:8],
                "big",
                signed=True,
            )
            for account in accounts
        }
        for key in sorted(keys):
            await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    async def record_batch(self, events: Sequence[ObservabilityEvent]) -> None:
        if not events:
            return
        admitted = [self.prepare_event(event) for event in events]
        accounts = {_event_scope(event)[0] for event in admitted}
        async with self._require_engine().begin() as conn:
            await self._lock_accounts(conn, accounts)
            generation = await self._read_generation(conn)
            fences = {}
            if generation:
                table = self._tables["usage_deletion_fence"]
                fences = {
                    (row.account_id, row.scope, row.user_id): row.generation
                    for row in (
                        await conn.execute(select(table).where(table.c.account_id.in_(accounts)))
                    )
                }
            retained = []
            for event in admitted:
                account, user = _event_scope(event)
                cutoff = max(
                    fences.get((account, "account", ""), 0), fences.get((account, "user", user), 0)
                )
                if event.deletion_generation >= cutoff:
                    retained.append(event)
            projection = project_events(retained)
            max_dates: dict[str, str] = {}
            for rows in (projection.token_rows, projection.retrieval_rows, projection.context_rows):
                for key in rows:
                    max_dates[key[0]] = max(max_dates.get(key[0], ""), key[2])
            updated_at = datetime.now(UTC).isoformat()
            for name, rows, counts in (
                ("usage_token_hourly", projection.token_rows, ("token_count",)),
                (
                    "usage_retrieval_hourly",
                    projection.retrieval_rows,
                    ("request_count", "result_count"),
                ),
                ("usage_context_write_bucket", projection.context_rows, ("count",)),
            ):
                table = self._tables[name]
                keys = [column.name for column in table.primary_key]
                values = [
                    {
                        **dict(zip(keys, key, strict=True)),
                        **dict(
                            zip(
                                counts, value if isinstance(value, tuple) else (value,), strict=True
                            )
                        ),
                        "updated_at": updated_at,
                    }
                    for key, value in rows.items()
                ]
                if values:
                    statement = insert(table)
                    # Executemany avoids PostgreSQL's bind limit for large batches.
                    await conn.execute(
                        statement.on_conflict_do_update(
                            index_elements=list(table.primary_key),
                            set_={
                                **{
                                    name: table.c[name] + statement.excluded[name]
                                    for name in counts
                                },
                                "updated_at": statement.excluded.updated_at,
                            },
                        ),
                        values,
                    )
            if projection.audit_rows:
                table = self._tables["request_audit"]
                columns = [column.name for column in table.columns if column.name != "id"]
                await conn.execute(
                    insert(table),
                    [dict(zip(columns, row, strict=True)) for row in projection.audit_rows],
                )
            await self._trim(conn, max_dates, projection.touched_audit_accounts)
        # Publish only after commit; queued events keep their original version.
        self._deletion_generation = max(self._deletion_generation, generation)

    async def _trim(self, conn, max_dates: dict[str, str], audit_accounts: set[str]) -> None:
        # Match SQLite: usage cutoff is anchored to this batch, audit cutoff to
        # the persisted newest timestamp. Zero disables the respective limit.
        if self._usage_retention_days > 0:
            for account, newest in max_dates.items():
                cutoff = (
                    date.fromisoformat(newest) - timedelta(days=self._usage_retention_days - 1)
                ).isoformat()
                for name in _USAGE_TABLES:
                    table = self._tables[name]
                    await conn.execute(
                        delete(table).where(
                            table.c.account_id == account, table.c.date_utc < cutoff
                        )
                    )
        table = self._tables["request_audit"]
        for account in sorted(audit_accounts):
            scope = table.c.account_id == account
            if self._audit_retention_days > 0:
                newest_at = await conn.scalar(
                    select(table.c.created_at)
                    .where(scope)
                    .order_by(table.c.created_at.desc(), table.c.id.desc())
                    .limit(1)
                )
                newest = newest_at[:10] if newest_at else None
                if newest:
                    cutoff = (
                        date.fromisoformat(newest) - timedelta(days=self._audit_retention_days - 1)
                    ).isoformat()
                    await conn.execute(delete(table).where(scope, table.c.created_at < cutoff))
            if self._audit_retention_per_account > 0:
                keep = (
                    select(table.c.id)
                    .where(scope)
                    .order_by(table.c.id.desc())
                    .limit(self._audit_retention_per_account)
                )
                await conn.execute(delete(table).where(scope, table.c.id.not_in(keep)))

    async def delete_data(self, *, account_id: str, user_id: str | None = None) -> dict[str, int]:
        async with self._require_engine().begin() as conn:
            await self._lock_accounts(conn, [account_id])
            meta = self._tables["_schema_meta"]
            generation = await conn.scalar(
                update(meta)
                .where(meta.c.key == "deletion_generation")
                .values(value=cast(cast(meta.c.value, BigInteger) + 1, Text))
                .returning(meta.c.value)
            )
            if generation is None:
                raise RuntimeError(
                    f"Missing PostgreSQL usage/audit deletion generation; provision {PROVISION_SQL}"
                )
            generation = int(generation)
            fence = self._tables["usage_deletion_fence"]
            statement = insert(fence).values(
                account_id=account_id,
                scope="account" if user_id is None else "user",
                user_id="" if user_id is None else user_id,
                generation=generation,
            )
            await conn.execute(
                statement.on_conflict_do_update(
                    index_elements=list(fence.primary_key),
                    set_={"generation": statement.excluded.generation},
                )
            )
            result = {}
            for name in (*_USAGE_TABLES, "request_audit"):
                table = self._tables[name]
                statement = delete(table).where(table.c.account_id == account_id)
                if user_id is not None:
                    statement = statement.where(table.c.user_id == user_id)
                result[name] = (await conn.execute(statement)).rowcount
        self._deletion_generation = max(self._deletion_generation, generation)
        return result

    async def _hourly(
        self,
        name: str,
        dimensions: tuple[str, ...],
        count: str,
        *,
        account_id: str,
        user_id: str | None,
        start: str,
        end: str,
        tz: tzinfo,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        table = self._tables[name]
        first, last = _window(start, end, tz)
        # Preserve the existing hour-grained timezone boundary semantics.
        lower, upper = first.date().isoformat(), last.date().isoformat()
        statement = (
            select(*(table.c[key] for key in dimensions), func.sum(table.c[count]).label("total"))
            .where(
                table.c.account_id == account_id,
                (table.c.date_utc > lower)
                | ((table.c.date_utc == lower) & (table.c.hour_utc >= first.hour)),
                (table.c.date_utc < upper)
                | ((table.c.date_utc == upper) & (table.c.hour_utc < last.hour)),
            )
            .group_by(*(table.c[key] for key in dimensions))
        )
        if user_id:
            statement = statement.where(table.c.user_id == user_id)
        if success_only:
            statement = statement.where(table.c.status == "success")
        async with self._require_engine().connect() as conn:
            return [dict(row) for row in (await conn.execute(statement)).mappings()]

    async def get_today_tokens(
        self, *, account_id: str, user_date: str, tz: tzinfo, user_id: str | None = None
    ) -> dict[str, int]:
        rows = await self._hourly(
            "usage_token_hourly",
            ("source", "token_type"),
            "token_count",
            account_id=account_id,
            user_id=user_id,
            start=user_date,
            end=user_date,
            tz=tz,
        )
        result = _token_slot()
        for row in rows:
            key = f"{row['source']}_{row['token_type']}"
            if key in result:
                result[key] += int(row["total"])
        result["total"] = sum(result.values())
        return result

    async def get_today_retrievals(
        self, *, account_id: str, user_date: str, tz: tzinfo, user_id: str | None = None
    ) -> dict[str, int]:
        rows = await self._hourly(
            "usage_retrieval_hourly",
            ("operation",),
            "request_count",
            account_id=account_id,
            user_id=user_id,
            start=user_date,
            end=user_date,
            tz=tz,
            success_only=True,
        )
        result = {"find": 0, "search": 0}
        for row in rows:
            if row["operation"] in result:
                result[row["operation"]] += int(row["total"])
        result["total"] = sum(result.values())
        return result

    @staticmethod
    def _local_datetime(row: dict[str, Any], tz: tzinfo) -> datetime:
        return datetime.combine(
            date.fromisoformat(row["date_utc"]), time(hour=row["hour_utc"]), tzinfo=UTC
        ).astimezone(tz)

    async def get_token_series(
        self,
        *,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        days = _days(start_user_date, end_user_date)
        slots = {day: _token_slot(day) for day in days}
        rows = await self._hourly(
            "usage_token_hourly",
            ("source", "token_type", "date_utc", "hour_utc"),
            "token_count",
            account_id=account_id,
            user_id=user_id,
            start=start_user_date,
            end=end_user_date,
            tz=tz,
        )
        for row in rows:
            day = self._local_datetime(row, tz).date().isoformat()
            key = f"{row['source']}_{row['token_type']}"
            if day in slots and key in slots[day]:
                slots[day][key] += int(row["total"])
        return list(slots.values())

    async def get_context_commit_heatmap(
        self,
        *,
        account_id: str,
        start_user_date: str,
        end_user_date: str,
        bucket: str,
        tz: tzinfo,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        size = 4 if bucket == "4h" else 1
        slots = {
            (day, hour): _context_slot(day, hour)
            for day in _days(start_user_date, end_user_date)
            for hour in range(0, 24, size)
        }
        rows = await self._hourly(
            "usage_context_write_bucket",
            ("date_utc", "hour_utc", "operation"),
            "count",
            account_id=account_id,
            user_id=user_id,
            start=start_user_date,
            end=end_user_date,
            tz=tz,
        )
        for row in rows:
            local = self._local_datetime(row, tz)
            slot = slots.get((local.date().isoformat(), local.hour // size * size))
            if slot is not None:
                key = row["operation"].replace(".", "_")
                if key in slot:
                    slot[key] += int(row["total"])
                slot["total"] += int(row["total"])
        return list(slots.values())

    async def query_audit_logs(
        self,
        *,
        account_id: str,
        user_id: str | None = None,
        request_id: str | None = None,
        statuses: list[str] | None = None,
        api_types: list[str] | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        table = self._tables["request_audit"]
        conditions = [table.c.account_id == account_id]
        if user_id:
            conditions.append(table.c.user_id == user_id)
        if request_id:
            conditions.append(table.c.request_id == request_id)
        if api_types:
            conditions.append(table.c.api_type.in_(api_types))
        filters = []
        for status in statuses or []:
            value = str(status).strip().lower()
            if not value:
                continue
            if value in {"success", "ok"}:
                filters.append((table.c.status_code >= 200) & (table.c.status_code < 400))
            elif value in {"error", "failed"}:
                filters.append(table.c.status_code >= 400)
            elif value.endswith("xx") and len(value) == 3 and value[0].isdigit():
                start = int(value[0]) * 100
                filters.append((table.c.status_code >= start) & (table.c.status_code < start + 100))
            else:
                filters.append(table.c.status_code == safe_int(value))
        if filters:
            conditions.append(or_(*filters))
        page, page_size = int(page), int(page_size)
        # Summary and items share a snapshot even while another server writes.
        async with self._require_engine().connect() as conn:
            conn = await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                total, success = (
                    await conn.execute(
                        select(
                            func.count(),
                            func.count().filter(
                                (table.c.status_code >= 200) & (table.c.status_code < 400)
                            ),
                        ).where(*conditions)
                    )
                ).one()
                statement = (
                    select(*(column for column in table.columns if column.name != "id"))
                    .where(*conditions)
                    .order_by(table.c.created_at.desc(), table.c.id.desc())
                    .limit(page_size)
                    .offset(max(page - 1, 0) * page_size)
                )
                items = [dict(row) for row in (await conn.execute(statement)).mappings()]
        for item in items:
            try:
                details = json.loads(item["error_details"]) if item["error_details"] else None
            except (TypeError, ValueError):
                details = None
            item["error_details"] = details if isinstance(details, dict) else None
        return {
            "total": int(total),
            "success_rate": success / total if total else 0.0,
            "page": page,
            "page_size": page_size,
            "items": items,
        }
