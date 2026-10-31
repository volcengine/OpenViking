# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account authentication contracts against configured Redis and MySQL."""

import asyncio
import base64
import json
import os
import threading
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import URL

from openviking.server.account_stores import redis_mysql
from openviking.server.account_stores.base import AccountStore
from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.mysql_schema import Account, Base
from openviking.server.account_stores.redis_mysql import RedisMySQLAccountStore


@pytest.fixture
async def backend():
    raw_mysql = os.environ.get("OV_TEST_MYSQL")
    endpoint = os.environ.get("OV_TEST_REDIS")
    if not raw_mysql or not endpoint:
        pytest.skip("Set OV_TEST_MYSQL (JSON) and OV_TEST_REDIS for account-store contracts")
    redis = pytest.importorskip("redis")
    params = json.loads(raw_mysql)
    prefix = f"ov:test:{uuid4().hex}"
    service = f"test-{uuid4().hex}"
    engine = create_engine(
        URL.create(
            "mysql+pymysql",
            username=params["user"],
            **{key: params[key] for key in ("host", "port", "password", "database")},
        )
    )
    Base.metadata.create_all(engine)
    inspection = redis.Redis.from_url(endpoint, decode_responses=True)
    stores: list[AccountStore] = []
    services = {service}

    async def make_mysql(mode="hash", service_name=service):
        services.add(service_name)
        mysql = MySQLAccountStore(
            service_name=service_name,
            params={
                **params,
                "credential_storage": mode,
                "credential_encryption_key": base64.urlsafe_b64encode(bytes(range(32))).decode(),
            },
        )
        stores.append(mysql)
        await mysql.load()
        return mysql

    async def make_store():
        mysql = await make_mysql()
        store = RedisMySQLAccountStore(
            service_name=service,
            mysql_store=mysql,
            params={
                "cache": {
                    "provider": "redis",
                    "params": {
                        "mode": "standalone",
                        "endpoints": [endpoint],
                        "key_prefix": prefix,
                    },
                }
            },
        )
        stores.append(store)
        await store.load()
        return store, mysql

    try:
        reader, mysql = await make_store()
        writer, authority = await make_store()
        # Seed through MySQL so the first Redis authentication is genuinely cold.
        await authority.create_account_with_api_key("acme", "admin", "admin-key")
        await authority.create_user_with_api_key("acme", "alice", "user", "old-key")
        yield SimpleNamespace(reader=reader, writer=writer, mysql=mysql, make_mysql=make_mysql)
    finally:
        for store in reversed(stores):
            await store.close()
        keys = list(inspection.scan_iter(match=f"{prefix}:*"))
        if keys:
            inspection.delete(*keys)
        inspection.close()
        with engine.begin() as connection:
            connection.execute(delete(Account).where(Account.service_name.in_(services)))
        engine.dispose()


async def snapshot(store, key="old-key"):
    return await store.get_auth_snapshot(key, account_id_hint="acme", user_id_hint="alice")


async def wait(event):
    assert await asyncio.to_thread(event.wait, 5), "operation did not reach its gate"


@pytest.fixture
def paused_snapshot(backend, monkeypatch):
    from redis.asyncio import Redis

    gate = SimpleNamespace(
        started=threading.Event(),
        release=threading.Event(),
        joined=threading.Event(),
        cancelled=threading.Event(),
        settled=threading.Event(),
        cleanup=threading.Event(),
        calls=0,
    )
    gate.cleanup.set()
    original = backend.mysql.get_auth_snapshot
    eval_command: Any = Redis.eval
    reads = 0

    async def observed_eval(client, *args, **kwargs):
        nonlocal reads
        result = await eval_command(client, *args, **kwargs)
        reads += 1
        if reads == 2:
            gate.joined.set()
        return result

    async def paused(*args, **kwargs):
        gate.calls += 1
        value = await original(*args, **kwargs)
        if gate.calls == 1:
            gate.started.set()
            try:
                await wait(gate.release)
            except asyncio.CancelledError:
                gate.cancelled.set()
                await wait(gate.cleanup)
                raise
            finally:
                gate.settled.set()
        return value

    monkeypatch.setattr(Redis, "eval", observed_eval)
    monkeypatch.setattr(backend.mysql, "get_auth_snapshot", paused)
    yield gate
    gate.release.set()
    gate.cleanup.set()


@pytest.mark.parametrize("mode", ["hash", "encrypted"])
async def test_mysql_snapshot_verifies_credentials_and_scopes_identity(backend, mode):
    mysql = await backend.make_mysql(mode)
    assert await snapshot(mysql) == {
        "account_id": "acme",
        "user_id": "alice",
        "role": "user",
        "group_ids": (),
    }
    await mysql.create_user_with_api_key("acme", "bob", "admin", "bob-key")
    await mysql.create_group("acme", "team")
    await mysql.add_group_member("acme", "team", "bob")
    assert (await mysql.get_auth_snapshot("bob-key"))["group_ids"] == ("team",)
    assert await mysql.get_auth_snapshot("bob-key", user_id_hint="alice") is None
    assert await mysql.get_auth_snapshot("bob-key", account_id_hint="other") is None
    assert await mysql.get_auth_snapshot("invalid-key") is None
    isolated = await backend.make_mysql(mode, f"test-{uuid4().hex}")
    assert await isolated.get_auth_snapshot("bob-key") is None
    await mysql.replace_active_user_api_key("acme", "bob", "new-key")
    assert await mysql.get_auth_snapshot("bob-key") is None
    assert (await mysql.get_auth_snapshot("new-key"))["user_id"] == "bob"


@pytest.mark.parametrize("user_id", ["alice", None], ids=["user", "account"])
async def test_cached_snapshot_observes_role_groups_and_deletion(backend, user_id):
    reader, writer = backend.reader, backend.writer
    await snapshot(reader)
    await snapshot(reader)
    await writer.set_role("acme", "alice", "admin")
    await writer.create_group("acme", "team")
    await writer.add_group_member("acme", "team", "alice")
    assert await snapshot(reader) == {
        "account_id": "acme",
        "user_id": "alice",
        "role": "admin",
        "group_ids": ("team",),
    }
    await writer.delete_group("acme", "team")
    assert (await snapshot(reader))["group_ids"] == ()
    await writer.begin_deletion(
        "acme", user_id, task_id="delete", owner_account_id="system", owner_user_id="system"
    )
    assert await snapshot(reader) is None


@pytest.mark.parametrize("cold", [True, False], ids=["cold", "warm"])
@pytest.mark.parametrize("user_id", ["alice", None], ids=["user", "account"])
async def test_revoke_does_not_join_or_publish_an_earlier_snapshot(
    backend, monkeypatch, cold, user_id
):
    if not cold:
        await snapshot(backend.reader)
    started, release = threading.Event(), threading.Event()
    original = backend.mysql.get_auth_snapshot
    first = True

    async def paused(*args, **kwargs):
        nonlocal first
        value = await original(*args, **kwargs)
        if first:
            first = False
            started.set()
            await wait(release)
        return value

    monkeypatch.setattr(backend.mysql, "get_auth_snapshot", paused)
    old_read = asyncio.create_task(snapshot(backend.reader))
    try:
        await wait(started)
        await backend.writer.revoke_active_api_keys("acme", user_id)
        assert await asyncio.wait_for(snapshot(backend.reader), 5) is None
        release.set()
        assert (await old_read)["user_id"] == "alice"
        assert await snapshot(backend.reader) is None
    finally:
        release.set()
        await asyncio.gather(old_read, return_exceptions=True)


async def test_cancelling_one_waiter_preserves_the_shared_read(backend, paused_snapshot):
    gate = paused_snapshot
    first = asyncio.create_task(snapshot(backend.reader))
    second = None
    try:
        await wait(gate.started)
        second = asyncio.create_task(snapshot(backend.reader))
        await wait(gate.joined)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        gate.release.set()
        assert (await second)["user_id"] == "alice"
        assert gate.calls == 1
        assert not gate.cancelled.is_set()
    finally:
        gate.release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)


async def test_close_waits_for_orphan_read_cleanup(backend, paused_snapshot):
    gate = paused_snapshot
    read = asyncio.create_task(snapshot(backend.reader))
    closing = None
    try:
        await wait(gate.started)
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        gate.cleanup.clear()
        closing = asyncio.create_task(backend.reader.close())
        await wait(gate.cancelled)
        assert not closing.done()
        gate.cleanup.set()
        await closing
        assert gate.settled.is_set()
        assert (await snapshot(backend.writer))["user_id"] == "alice"
    finally:
        gate.release.set()
        gate.cleanup.set()
        await asyncio.gather(read, *([closing] if closing else []), return_exceptions=True)


async def test_redis_failure_reads_authority_and_preserves_database_errors(backend, monkeypatch):
    from redis.asyncio import Redis

    await snapshot(backend.reader)
    await snapshot(backend.reader)
    await backend.mysql.set_role("acme", "alice", "admin")

    async def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(Redis, "eval", offline)
    assert (await snapshot(backend.reader))["role"] == "admin"
    with monkeypatch.context() as patch:
        patch.setattr(backend.mysql, "get_auth_snapshot", offline)
        with pytest.raises(OSError, match="offline"):
            await snapshot(backend.reader)
    assert (await snapshot(backend.reader))["role"] == "admin"


async def test_concurrent_lifecycle_keeps_other_stores_usable_and_stops_runtime(
    backend, monkeypatch
):
    await backend.reader.close()
    threads = []
    factory = redis_mysql.create_redis_client

    def create_client(params):
        threads.append(threading.current_thread())
        return factory(params)

    monkeypatch.setattr(redis_mysql, "create_redis_client", create_client)
    await asyncio.gather(
        backend.reader.load(), asyncio.to_thread(lambda: asyncio.run(backend.reader.load()))
    )
    assert len(threads) == 1
    assert threads[0] is not threading.current_thread()
    assert (await snapshot(backend.reader))["user_id"] == "alice"
    await asyncio.gather(
        backend.reader.close(), asyncio.to_thread(lambda: asyncio.run(backend.reader.close()))
    )
    assert (await snapshot(backend.writer))["user_id"] == "alice"
    await backend.writer.close()
    await asyncio.to_thread(threads[0].join, 5)
    assert not threads[0].is_alive()


async def test_cancelled_startup_releases_the_runtime(backend, monkeypatch):
    from redis.asyncio import Redis

    await backend.reader.close()
    await backend.writer.close()
    started = threading.Event()
    threads = []
    ping = Redis.ping

    async def blocked_ping(client, *args, **kwargs):
        await ping(client, *args, **kwargs)
        threads.append(threading.current_thread())
        started.set()
        await asyncio.Future()

    with monkeypatch.context() as patch:
        patch.setattr(Redis, "ping", blocked_ping)
        loading = asyncio.create_task(backend.reader.load())
        try:
            await wait(started)
            loading.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loading
            await asyncio.to_thread(threads[0].join, 5)
            assert not threads[0].is_alive()
        finally:
            loading.cancel()
            await asyncio.gather(loading, return_exceptions=True)
    await backend.reader.load()
    assert (await snapshot(backend.reader))["user_id"] == "alice"
