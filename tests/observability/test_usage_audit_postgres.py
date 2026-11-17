# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from openviking.observability.events import ObservabilityEvent, try_publish_event
from openviking.observability.usage_audit import (
    init_usage_audit_from_server_config,
    shutdown_usage_audit,
)
from openviking.server.auth import get_request_context
from openviking.server.config import ServerConfig, UsageAuditConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.console import router
from openviking_cli.session.user_id import UserIdentifier


def _event(name="vlm.call", *, account="acct", user="alice", day=12, **payload):
    return ObservabilityEvent(
        name,
        payload,
        timestamp=datetime(2026, 5, day, 1, tzinfo=timezone.utc),
        account_id=account,
        user_id=user,
    )


def test_postgres_backend_configuration_and_unchanged_defaults():
    default = UsageAuditConfig()
    assert (default.backend, default.usage_retention_days, default.audit_retention_days) == (
        "sqlite",
        14,
        7,
    )
    config = UsageAuditConfig(
        backend="postgres", postgres={"dsn": "postgresql://alice:synthetic-secret@localhost/db"}
    )
    assert config.postgres.schema_name == "openviking_usage_audit"
    assert "synthetic-secret" not in repr(config)
    assert "synthetic-secret" not in config.model_dump_json()


@pytest.mark.parametrize(
    "postgres",
    [
        None,
        {"dsn": ""},
        {"dsn": "x", "schema_name": "public"},
        {"dsn": "x", "schema_name": "pg_catalog"},
        {"dsn": "x", "schema_name": "a;DROP SCHEMA public"},
    ],
)
def test_postgres_backend_rejects_invalid_configuration(postgres):
    with pytest.raises(ValidationError):
        UsageAuditConfig(backend="postgres", postgres=postgres)


@pytest.mark.asyncio
async def test_shared_writers_restart_retention_and_scoped_purge(usage_store_factory):
    first = usage_store_factory(audit_retention_per_account=3)
    second = usage_store_factory(audit_retention_per_account=3)
    if hasattr(first, "_dsn"):
        await asyncio.gather(first.initialize(), second.initialize())
    else:
        # SQLite remains a per-instance local store, not shared startup DDL.
        await first.initialize()
        await second.initialize()
    await asyncio.gather(
        *[
            store.record_batch(
                [
                    _event(prompt_tokens=2, completion_tokens=1),
                    _event(
                        "http.request",
                        request_id=f"request-{index}",
                        method="GET",
                        route="/api/v1/tasks",
                        status="200",
                    ),
                ]
            )
            for index, store in enumerate([first, second] * 10)
        ]
    )
    assert (
        await first.get_today_tokens(account_id="acct", user_date="2026-05-12", tz=ZoneInfo("UTC"))
    )["vlm_input"] == 40
    assert (await second.query_audit_logs(account_id="acct"))["total"] == 3
    await first.record_batch(
        [
            _event(user="bob", prompt_tokens=7),
            _event(account="other", prompt_tokens=11),
        ]
    )
    await first.delete_data(account_id="acct", user_id="alice")
    await first.close()
    reader = usage_store_factory()
    await reader.initialize()
    tokens = await reader.get_today_tokens(
        account_id="acct", user_date="2026-05-12", tz=ZoneInfo("UTC")
    )
    assert tokens["vlm_input"] == 7
    assert (await reader.query_audit_logs(account_id="acct"))["total"] == 0
    await reader.delete_data(account_id="acct")
    assert (
        await reader.get_today_tokens(
            account_id="other", user_date="2026-05-12", tz=ZoneInfo("UTC")
        )
    )["vlm_input"] == 11


@pytest.mark.asyncio
async def test_entire_batch_rolls_back_and_next_write_succeeds(usage_store_factory, monkeypatch):
    store = usage_store_factory()
    await store.initialize()
    original = store._write_audit_rows

    def fail(*args):
        raise RuntimeError("synthetic failure after token upsert")

    monkeypatch.setattr(store, "_write_audit_rows", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await store.record_batch([_event(prompt_tokens=17)])
    monkeypatch.setattr(store, "_write_audit_rows", original)
    await store.record_batch([_event(prompt_tokens=2)])
    assert (
        await store.get_today_tokens(account_id="acct", user_date="2026-05-12", tz=ZoneInfo("UTC"))
    )["vlm_input"] == 2


@pytest.mark.asyncio
async def test_late_batch_does_not_remove_newer_rows(usage_store_factory):
    store = usage_store_factory(usage_retention_days=2, audit_retention_days=2)
    await store.initialize()
    for day in [12, 15, 13]:
        await store.record_batch(
            [
                _event(day=day, prompt_tokens=day),
                _event("http.request", day=day, method="GET", route="/api/v1/tasks", status="200"),
            ]
        )
    assert (
        await store.get_today_tokens(account_id="acct", user_date="2026-05-15", tz=ZoneInfo("UTC"))
    )["vlm_input"] == 15
    assert (await store.query_audit_logs(account_id="acct"))["total"] == 1


@pytest.mark.asyncio
async def test_postgres_runtime_event_bus_to_real_console_with_user_scope(usage_store_factory):
    owner = usage_store_factory()
    if not hasattr(owner, "_dsn"):
        pytest.skip("PostgreSQL runtime selection")
    app = FastAPI()
    app.include_router(router)
    current = RequestContext(
        user=UserIdentifier(account_id="acct", user_id="alice"), role=Role.USER
    )
    app.dependency_overrides[get_request_context] = lambda: current
    config = ServerConfig.model_validate(
        {
            "observability": {
                "usage_audit": {
                    "backend": "postgres",
                    "postgres": {"dsn": owner._dsn, "schema_name": owner._schema},
                    "timezone": "UTC",
                }
            }
        }
    )
    runtime = await init_usage_audit_from_server_config(config, app=app, service=SimpleNamespace())
    try:
        for user in ["alice", "bob"]:
            try_publish_event(
                "http.request",
                {
                    "method": "GET",
                    "route": "/api/v1/tasks",
                    "status": "200",
                    "account_id": "acct",
                    "user_id": user,
                },
            )
        await runtime.worker.flush()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = (await client.get("/api/v1/console/audit")).json()["result"]
            assert result["total"] == 1
            assert result["items"][0]["user_id"] == "alice"
            current.role = Role.ADMIN
            result = (await client.get("/api/v1/console/audit")).json()["result"]
            assert result["total"] == 2
    finally:
        await shutdown_usage_audit(app=app)


@pytest.mark.asyncio
async def test_postgres_independent_processes_share_additive_projection(usage_store_factory):
    store = usage_store_factory()
    if not hasattr(store, "_dsn"):
        pytest.skip("independent PostgreSQL writers")
    await store.initialize()
    script = """
import asyncio, sys
from datetime import datetime, timezone
from openviking.observability.events import ObservabilityEvent
from openviking.observability.usage_audit.postgres_store import PostgresUsageAuditStore
async def run():
    store = PostgresUsageAuditStore(sys.argv[1], schema=sys.argv[2])
    await store.initialize()
    for i in range(20):
        await store.record_batch([ObservabilityEvent('vlm.call', {'prompt_tokens': 3}, timestamp=datetime(2026,5,12,tzinfo=timezone.utc), account_id='acct', user_id='alice')])
    await store.close()
asyncio.run(run())
"""
    processes = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            store._dsn,
            store._schema,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        for _ in range(2)
    ]
    results = await asyncio.wait_for(
        asyncio.gather(*[process.communicate() for process in processes]), timeout=45
    )
    for process, (_, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stderr.decode()
    assert (
        await store.get_today_tokens(account_id="acct", user_date="2026-05-12", tz=ZoneInfo("UTC"))
    )["vlm_input"] == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_state", ["future", "missing_version", "missing_meta"])
async def test_postgres_schema_version_rejected_without_reset(usage_store_factory, schema_state):
    store = usage_store_factory()
    if not hasattr(store, "_dsn"):
        pytest.skip("PostgreSQL migration policy")
    await store.initialize()
    await store.record_batch([_event(prompt_tokens=19)])
    if schema_state == "future":
        store._conn.execute("UPDATE _schema_meta SET value = '999' WHERE key = 'version'")
    elif schema_state == "missing_version":
        store._conn.execute("DELETE FROM _schema_meta")
    else:
        store._conn.execute("DROP TABLE _schema_meta")
    await store.close()
    replacement = usage_store_factory()
    with pytest.raises(RuntimeError, match="migration required"):
        await replacement.initialize()
    import psycopg
    from psycopg import sql

    with psycopg.connect(store._dsn) as conn:
        count = conn.execute(
            sql.SQL("SELECT SUM(token_count) FROM {}.usage_token_hourly").format(
                sql.Identifier(store._schema)
            )
        ).fetchone()[0]
    assert count == 19


@pytest.mark.asyncio
async def test_postgres_lock_timeout_rolls_back_without_replay(usage_store_factory):
    store = usage_store_factory()
    if not hasattr(store, "_dsn"):
        pytest.skip("PostgreSQL transaction timeout")
    import psycopg

    store._statement_timeout = 100
    await store.initialize()
    with psycopg.connect(store._dsn) as blocker:
        blocker.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"ov-usage-account:{store._schema}:acct",),
        )
        with pytest.raises(psycopg.errors.QueryCanceled):
            await store.record_batch([_event(prompt_tokens=50)])
    await store.record_batch([_event(prompt_tokens=3)])
    assert (
        await store.get_today_tokens(account_id="acct", user_date="2026-05-12", tz=ZoneInfo("UTC"))
    )["vlm_input"] == 3


@pytest.mark.asyncio
async def test_postgres_disconnected_connection_is_not_replayed(usage_store_factory):
    store = usage_store_factory()
    if not hasattr(store, "_dsn"):
        pytest.skip("PostgreSQL connection loss")
    import psycopg

    await store.initialize()
    await store.record_batch([_event(prompt_tokens=5)])
    pid = store._conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
    with psycopg.connect(store._dsn, autocommit=True) as killer:
        killer.execute("SELECT pg_terminate_backend(%s)", (pid,))
    with pytest.raises(psycopg.OperationalError):
        await store.record_batch([_event(prompt_tokens=100)])
    await store.close()
    await store.initialize()
    await store.record_batch([_event(prompt_tokens=2)])
    assert (
        await store.get_today_tokens(account_id="acct", user_date="2026-05-12", tz=ZoneInfo("UTC"))
    )["vlm_input"] == 7


@pytest.mark.asyncio
async def test_cancellation_keeps_thread_connection_owned_until_completion(
    usage_store_factory, monkeypatch
):
    import threading

    store = usage_store_factory()
    await store.initialize()
    entered = threading.Event()
    release = threading.Event()
    original = store._record_projection_sync

    def slow(projection):
        entered.set()
        assert release.wait(timeout=5)
        original(projection)

    monkeypatch.setattr(store, "_record_projection_sync", slow)
    writer = asyncio.create_task(store.record_batch([_event(prompt_tokens=3)]))
    assert await asyncio.to_thread(entered.wait, 5)
    writer.cancel()
    closer = asyncio.create_task(store.close())
    await asyncio.sleep(0)
    writer.cancel()  # Repeated cancellation must not release the lock either.
    await asyncio.sleep(0)
    assert not closer.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await writer
    await closer
    await store.initialize()
    assert (
        await store.get_today_tokens(account_id="acct", user_date="2026-05-12", tz=ZoneInfo("UTC"))
    )["vlm_input"] == 3
