# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PostgreSQL contract tests; OV_TEST_USAGE_AUDIT_POSTGRES is a disposable DB DSN."""

import asyncio
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit.postgres_schema import (
    POSTGRES_SCHEMA_VERSION,
    PROVISION_SQL,
)
from openviking.observability.usage_audit.postgres_store import PostgresUsageAuditStore
from openviking.observability.usage_audit.sqlite_store import SQLiteUsageAuditStore
from openviking.server.config import UsageAuditConfig

UTC = timezone.utc


def event(name="vlm.call", *, account="a", user="u", day=12, hour=16, **payload):
    return ObservabilityEvent(
        event_name=name,
        account_id=account,
        user_id=user,
        timestamp=datetime(2026, 5, day, hour, tzinfo=UTC),
        payload=payload,
    )


def http(*, status=200, route="/api/v1/search/find", **kwargs):
    return event(
        "http.request", method="POST", route=route, status=status, duration_ms=12.5, **kwargs
    )


@pytest.fixture
def pg_database():
    dsn = os.environ.get("OV_TEST_USAGE_AUDIT_POSTGRES")
    if not dsn:
        pytest.skip("Set OV_TEST_USAGE_AUDIT_POSTGRES to run real PostgreSQL tests")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.conninfo import conninfo_to_dict

    schema = "test_usage_audit_" + uuid.uuid4().hex
    params = conninfo_to_dict(dsn)
    params["database"] = params.pop("dbname")
    params["schema"] = schema
    sql = Path(PROVISION_SQL).read_text().replace("openviking_usage_audit", schema)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)
        try:
            yield params, conn
        finally:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.parametrize(
    "params",
    [
        {"schema": "a; DROP TABLE x", "user": "u", "database": "db"},
        {"schema": "A", "user": "u", "database": "db"},
        {"user": "u"},
        {"user": "u", "database": "db", "dsn": "invalid"},
        {"user": "u", "database": "db", "sslmode": "invalid"},
    ],
)
def test_invalid_config(params):
    with pytest.raises(ValidationError):
        UsageAuditConfig(backend="postgres", postgres=params)


def test_config_defaults_and_disabled():
    assert UsageAuditConfig().backend == "sqlite"
    with pytest.raises(ValidationError):
        UsageAuditConfig(backend="postgres")
    assert not UsageAuditConfig(backend="postgres", enabled=False).enabled


@pytest.mark.asyncio
async def test_postgres_matches_sqlite_queries(pg_database, tmp_path):
    params, _ = pg_database
    postgres = PostgresUsageAuditStore(
        params, usage_retention_days=0, audit_retention_days=0, audit_retention_per_account=0
    )
    sqlite = SQLiteUsageAuditStore(
        tmp_path / "usage.db",
        usage_retention_days=0,
        audit_retention_days=0,
        audit_retention_per_account=0,
    )
    try:
        await postgres.initialize()
        await sqlite.initialize()
        events = [
            event(prompt_tokens=100, completion_tokens=20),
            event("embedding.call", day=13, hour=0, prompt_tokens=40),
            event("rerank.call", prompt_tokens=9),
            event(account="other", prompt_tokens=999),
            event(user="other", prompt_tokens=10),
            http(request_id="find", result_count=3),
            http(status=302, request_id="redirect"),
            http(
                status=404,
                error_code="NOT_FOUND",
                error_message="Missing",
                error_details={"resource": "x"},
                request_id="error",
            ),
            http(route="/api/v1/search/search", result_count=2),
            http(route="/api/v1/resources"),
            http(route="/api/v1/skills", user="other"),
            http(route="/api/v1/sessions/{session_id}/messages"),
            http(route="/api/v1/sessions/{session_id}/commit"),
            http(route="/api/v1/console/audit"),
            http(account="other"),
            event(day=1, prompt_tokens=5),
        ]
        for store in (postgres, sqlite):
            await store.record_batch(events)
        for tz in (
            ZoneInfo("UTC"),
            ZoneInfo("Asia/Shanghai"),
            ZoneInfo("America/New_York"),
            ZoneInfo("Asia/Kolkata"),
        ):
            for user in (None, "u", "other", "missing"):
                scope = {"account_id": "a", "user_id": user, "tz": tz}
                for method in ("get_today_tokens", "get_today_retrievals"):
                    assert await getattr(postgres, method)(
                        **scope, user_date="2026-05-13"
                    ) == await getattr(sqlite, method)(**scope, user_date="2026-05-13")
                for method, buckets in (
                    ("get_token_series", ["day"]),
                    ("get_context_commit_heatmap", ["hour", "4h"]),
                ):
                    for bucket in buckets:
                        kwargs = dict(
                            **scope,
                            start_user_date="2026-05-11",
                            end_user_date="2026-05-14",
                            bucket=bucket,
                        )
                        assert await getattr(postgres, method)(**kwargs) == await getattr(
                            sqlite, method
                        )(**kwargs)
        for filters in (
            {},
            {"statuses": ["success"]},
            {"statuses": ["3xx"]},
            {"statuses": ["failed", "200"]},
            {"statuses": ["404"]},
            {"statuses": ["", " nonsense "]},
            {"api_types": ["search.find"]},
            {"request_id": "error"},
            {"user_id": "other"},
            {"page": 2, "page_size": 3},
            {"account_id": "other"},
        ):
            kwargs = {"account_id": "a"} | filters
            assert await postgres.query_audit_logs(**kwargs) == await sqlite.query_audit_logs(
                **kwargs
            )
        assert await postgres.delete_data(account_id="a", user_id="u") == await sqlite.delete_data(
            account_id="a", user_id="u"
        )
        assert await postgres.query_audit_logs(account_id="a") == await sqlite.query_audit_logs(
            account_id="a"
        )
        assert await postgres.delete_data(account_id="a") == await sqlite.delete_data(
            account_id="a"
        )
    finally:
        await postgres.close()
        await sqlite.close()


@pytest.mark.asyncio
async def test_concurrent_writers_retention_and_rollback(pg_database):
    params, conn = pg_database
    schema = params["schema"]
    stores = [
        PostgresUsageAuditStore(
            params, usage_retention_days=2, audit_retention_days=2, audit_retention_per_account=5
        )
        for _ in range(2)
    ]
    try:
        for store in stores:
            await store.initialize()
        await stores[0].record_batch([event(day=1, prompt_tokens=7), http(day=1)])
        await asyncio.gather(
            *[
                stores[i % 2].record_batch([event(prompt_tokens=3), http(request_id=str(i))])
                for i in range(30)
            ]
        )
        assert (await stores[0].get_today_tokens(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 90
        assert (await stores[1].query_audit_logs(account_id="a"))["total"] == 5
        assert (
            conn.execute(
                f'SELECT COUNT(*) FROM "{schema}".usage_token_hourly WHERE date_utc = %s',
                ("2026-05-01",),
            ).fetchone()[0]
            == 0
        )
        # A late historical request is removed by the persisted audit cutoff.
        await stores[1].record_batch([http(day=1)])
        assert (await stores[0].query_audit_logs(account_id="a"))["total"] == 5
        # Fail after rollup inserts; verify the whole projection rolls back.
        conn.execute(
            f'ALTER TABLE "{schema}".request_audit ADD CONSTRAINT reject_599 CHECK (status_code <> 599)'
        )
        with pytest.raises(IntegrityError):
            await stores[0].record_batch([event(prompt_tokens=1000), http(status=599)])
        assert (await stores[1].get_today_tokens(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 90
        assert (await stores[1].query_audit_logs(account_id="a"))["total"] == 5
        await stores[0].close()
        await stores[0].initialize()
        assert (await stores[0].get_today_tokens(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 90
    finally:
        for store in stores:
            await store.close()


@pytest.mark.asyncio
async def test_schema_validation_does_not_create_or_migrate(pg_database):
    params, conn = pg_database
    schema = params["schema"]
    store = PostgresUsageAuditStore(dict(params, schema=schema + "_missing"))
    try:
        with pytest.raises(RuntimeError, match="provision"):
            await store.initialize()
        assert not conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema + "_missing",)
        ).fetchone()
        conn.execute(f'UPDATE "{schema}"._schema_meta SET value = %s', ("999",))
        store = PostgresUsageAuditStore(params)
        with pytest.raises(RuntimeError, match="version"):
            await store.initialize()
        conn.execute(
            f'UPDATE "{schema}"._schema_meta SET value = %s WHERE key = %s',
            (str(POSTGRES_SCHEMA_VERSION), "version"),
        )
        conn.execute(
            f'UPDATE "{schema}"._schema_meta SET value = %s WHERE key = %s',
            ("0", "deletion_generation"),
        )
        conn.execute(
            f'ALTER TABLE "{schema}".usage_token_hourly ALTER COLUMN token_count TYPE integer'
        )
        with pytest.raises(RuntimeError, match="column"):
            await store.initialize()
        assert store._engine is None
        assert not store._sql_lease
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "DROP TABLE {schema}.usage_retrieval_hourly",
        "ALTER TABLE {schema}.usage_context_write_bucket DROP COLUMN operation",
        "ALTER TABLE {schema}.usage_token_hourly DROP CONSTRAINT usage_token_hourly_pkey",
        'ALTER TABLE {schema}.usage_token_hourly ALTER COLUMN account_id TYPE text COLLATE "default"',
        "ALTER TABLE {schema}.request_audit ALTER COLUMN id DROP IDENTITY",
    ],
)
async def test_incompatible_schema_fails_with_provisioning_guidance(pg_database, change):
    params, conn = pg_database
    conn.execute(change.format(schema=f'"{params["schema"]}"'))
    store = PostgresUsageAuditStore(params)
    try:
        with pytest.raises(RuntimeError, match="provision"):
            await store.initialize()
        assert store._engine is None
        assert not store._sql_lease
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_write_rolls_back_and_releases_account_lock(pg_database, monkeypatch):
    params, _ = pg_database
    writer, reader = PostgresUsageAuditStore(params), PostgresUsageAuditStore(params)
    entered = threading.Event()

    async def blocked_trim(*args):
        # Cancellation arrives after all four kinds of row have been written.
        entered.set()
        await asyncio.Future()

    try:
        await writer.initialize()
        await reader.initialize()
        monkeypatch.setattr(writer, "_trim", blocked_trim)
        pending = asyncio.create_task(
            writer.record_batch([event(prompt_tokens=10), http(), http(route="/api/v1/resources")])
        )
        assert await asyncio.to_thread(entered.wait, 5)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert (await reader.query_audit_logs(account_id="a"))["total"] == 0
        assert (await reader.get_today_tokens(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 0
        assert (await reader.get_today_retrievals(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 0
        assert (
            sum(
                row["total"]
                for row in await reader.get_context_commit_heatmap(
                    account_id="a",
                    start_user_date="2026-05-12",
                    end_user_date="2026-05-12",
                    bucket="hour",
                    tz=UTC,
                )
            )
            == 0
        )
        await asyncio.wait_for(reader.record_batch([http()]), timeout=5)
        assert (await reader.query_audit_logs(account_id="a"))["total"] == 1
    finally:
        await writer.close()
        await reader.close()


@pytest.mark.asyncio
async def test_postgres_runtime_events_flush_and_delete(pg_database):
    from openviking.observability.events import try_publish_event
    from openviking.observability.usage_audit.runtime import (
        init_usage_audit_from_server_config,
        shutdown_usage_audit,
    )
    from openviking.server.config import ServerConfig

    params, _ = pg_database
    app = SimpleNamespace(state=SimpleNamespace())
    config = ServerConfig.model_validate(
        {
            "observability": {
                "usage_audit": {
                    "backend": "postgres",
                    "postgres": params,
                    "timezone": "UTC",
                }
            }
        }
    )
    runtime = await init_usage_audit_from_server_config(config, app=app)
    assert runtime is not None
    try:
        assert isinstance(runtime.store, PostgresUsageAuditStore)
        try_publish_event(
            "http.request",
            {
                "account_id": "runtime",
                "user_id": "u",
                "method": "POST",
                "route": "/api/v1/search/find",
                "status": 200,
                "request_id": "runtime-request",
            },
        )
        await runtime.worker.flush()
        assert (await runtime.store.query_audit_logs(account_id="runtime"))["total"] == 1
        assert (
            await runtime.store.get_today_retrievals(
                account_id="runtime",
                user_date=runtime.api_service.today(),
                tz=UTC,
            )
        )["find"] == 1
        # delete_data drains the still-queued accepted event before deleting.
        try_publish_event(
            "vlm.call", {"prompt_tokens": 20, "account_id": "runtime", "user_id": "u"}
        )
        deleted = await runtime.delete_data(account_id="runtime", user_id="u")
        assert deleted["usage_token_hourly"] == 1
        assert deleted["request_audit"] == 1
    finally:
        await shutdown_usage_audit(app=app)
    assert app.state.usage_audit_runtime is None
    assert runtime.store._engine is None


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["u", None])
async def test_remote_worker_cannot_resurrect_deleted_data(pg_database, monkeypatch, user_id):
    from openviking.observability.usage_audit.runtime import UsageAuditRuntime
    from openviking.observability.usage_audit.worker import UsageAuditWorker

    params, conn = pg_database
    local, remote = PostgresUsageAuditStore(params), PostgresUsageAuditStore(params)
    workers = [
        UsageAuditWorker(local, flush_interval_seconds=0.1),
        UsageAuditWorker(remote, batch_size=1, flush_interval_seconds=0.1),
    ]
    entered, release = asyncio.Event(), asyncio.Event()
    original = remote.record_batch

    async def paused_batch(events):
        entered.set()
        await release.wait()
        await original(events)

    try:
        await local.initialize()
        await remote.initialize()
        for worker in workers:
            await worker.start()
        workers[0].enqueue(http(request_id="persisted"))
        await workers[0].flush()
        assert (await local.query_audit_logs(account_id="a"))["total"] == 1
        monkeypatch.setattr(remote, "record_batch", paused_batch)
        # The first old event is already in-flight, the rest remain queued.
        workers[1].enqueue(http(request_id="late-old"))
        await asyncio.wait_for(entered.wait(), 5)
        for item in [
            event(prompt_tokens=10),
            http(route="/api/v1/resources"),
            http(user="other", request_id="other-user"),
            http(account="other", request_id="other-account"),
        ]:
            workers[1].enqueue(item)
        runtime = UsageAuditRuntime(local, workers[0], None, 1)
        await runtime.delete_data(account_id="a", user_id=user_id)
        assert (await local.query_audit_logs(account_id="a"))["total"] == 0
        release.set()
        await asyncio.wait_for(workers[1].flush(), 5)
        assert [
            item["request_id"] for item in (await local.query_audit_logs(account_id="a"))["items"]
        ] == (["other-user"] if user_id is not None else [])
        assert (await local.query_audit_logs(account_id="other"))["total"] == 1
        assert (await local.get_today_tokens(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 0
        assert (
            sum(
                row["total"]
                for row in await local.get_context_commit_heatmap(
                    account_id="a",
                    start_user_date="2026-05-12",
                    end_user_date="2026-05-12",
                    bucket="hour",
                    tz=UTC,
                )
            )
            == 0
        )
        # Same IDs may be used again after the writer observed the deletion.
        # This deliberately uses the old event timestamp to prove fencing
        # does not depend on application clocks or event time.
        workers[1].enqueue(http(request_id="new-incarnation"))
        await workers[1].flush()
        assert (await local.query_audit_logs(account_id="a", request_id="new-incarnation"))[
            "total"
        ] == 1
        assert (
            conn.execute(
                f'SELECT COUNT(*) FROM "{params["schema"]}".usage_deletion_fence'
            ).fetchone()[0]
            == 1
        )
    finally:
        release.set()
        for worker in workers:
            await worker.close(timeout_seconds=0.2)
        await local.close()
        await remote.close()


@pytest.mark.asyncio
async def test_deletion_fence_survives_restart_and_empty_scope(pg_database):
    params, _ = pg_database
    writer, deleter = PostgresUsageAuditStore(params), PostgresUsageAuditStore(params)
    try:
        await writer.initialize()
        await deleter.initialize()
        old = writer.prepare_event(http())
        await deleter.delete_data(account_id="a", user_id="u")
        await deleter.close()
        await writer.close()
        await writer.initialize()
        await writer.record_batch([old])
        assert (await writer.query_audit_logs(account_id="a"))["total"] == 0
        await writer.record_batch([http()])
        assert (await writer.query_audit_logs(account_id="a"))["total"] == 1
        # Repeated deletion advances the fence; an already prepared batch is stale.
        old = writer.prepare_event(http())
        await writer.delete_data(account_id="a", user_id="u")
        await writer.record_batch([old])
        assert (await writer.query_audit_logs(account_id="a"))["total"] == 0
    finally:
        await writer.close()
        await deleter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("lock_kind", ["table", "advisory"])
async def test_lock_wait_is_bounded_and_worker_close_finishes(pg_database, lock_kind):
    from sqlalchemy.exc import OperationalError

    from openviking.observability.usage_audit.worker import UsageAuditWorker

    params, conn = pg_database
    schema = params["schema"]
    store = PostgresUsageAuditStore(dict(params, lock_timeout_ms=100))
    worker = UsageAuditWorker(store, flush_interval_seconds=0.1)
    try:
        await store.initialize()
        await store.record_batch([http(request_id="initial")])
        # A table lock covers implicit insert/delete lock waits as well as
        # advisory waits. Another account must remain usable after rollback.
        conn.execute("BEGIN")
        if lock_kind == "table":
            conn.execute(f'LOCK TABLE "{schema}".request_audit IN ACCESS EXCLUSIVE MODE')
        else:
            key = int.from_bytes(
                hashlib.sha256(
                    json.dumps(["openviking_usage_audit", schema, "a"], ensure_ascii=False).encode()
                ).digest()[:8],
                "big",
                signed=True,
            )
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        started = asyncio.get_running_loop().time()
        with pytest.raises(OperationalError) as error:
            await asyncio.wait_for(store.record_batch([event(prompt_tokens=10), http()]), 2)
        assert error.value.orig.sqlstate == "55P03"
        assert asyncio.get_running_loop().time() - started < 1.5
        # Failed deletion must roll back the fence and the shared generation.
        with pytest.raises(OperationalError):
            await asyncio.wait_for(store.delete_data(account_id="a", user_id="u"), 2)
        await worker.start()
        worker.enqueue(http())
        await asyncio.sleep(0.02)
        await asyncio.wait_for(worker.close(timeout_seconds=0.05), 1.5)
        conn.execute("ROLLBACK")
        assert (
            conn.execute(f'SELECT COUNT(*) FROM "{schema}".usage_deletion_fence').fetchone()[0] == 0
        )
        assert (
            conn.execute(
                f'SELECT value FROM "{schema}"._schema_meta WHERE key = %s',
                ("deletion_generation",),
            ).fetchone()[0]
            == "0"
        )
        assert (await store.query_audit_logs(account_id="a"))["total"] == 1
        assert (await store.get_today_tokens(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 0
        await store.record_batch([http(account="other")])
        assert (await store.query_audit_logs(account_id="other"))["total"] == 1
    finally:
        conn.execute("ROLLBACK")
        await worker.close(timeout_seconds=0.2)
        await store.close()


@pytest.mark.parametrize("timeout", [0, -1, 60001, True, 0.5, None, "invalid"])
def test_invalid_lock_timeout(timeout):
    with pytest.raises(ValidationError, match="lock_timeout_ms"):
        UsageAuditConfig(
            backend="postgres", postgres={"user": "u", "database": "d", "lock_timeout_ms": timeout}
        )


@pytest.mark.asyncio
async def test_preview_v1_migration_preserves_rows(pg_database):
    params, conn = pg_database
    schema = params["schema"]
    store = PostgresUsageAuditStore(params)
    try:
        await store.initialize()
        await store.record_batch([event(prompt_tokens=10), http()])
        await store.close()
        # Simulate the preview v1 layout, then apply the administrator script.
        conn.execute(f'DROP TABLE "{schema}".usage_deletion_fence')
        conn.execute(
            f'DELETE FROM "{schema}"._schema_meta WHERE key = %s', ("deletion_generation",)
        )
        conn.execute(
            f'UPDATE "{schema}"._schema_meta SET value = %s WHERE key = %s', ("1", "version")
        )
        sql = (
            Path("openviking/observability/usage_audit/sql/002_usage_audit_deletion_fence.sql")
            .read_text()
            .replace("openviking_usage_audit", schema)
        )
        conn.execute(sql)
        await store.initialize()
        assert (await store.query_audit_logs(account_id="a"))["total"] == 1
        assert (await store.get_today_tokens(account_id="a", user_date="2026-05-12", tz=UTC))[
            "total"
        ] == 10
    finally:
        await store.close()
