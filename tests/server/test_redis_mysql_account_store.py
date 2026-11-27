# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account authentication contracts against configured Redis and MySQL."""

import asyncio
import base64
import json
import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.engine import URL

from openviking.server.account_stores.base import AccountStore
from openviking.server.account_stores.mysql import MySQLAccountStore
from openviking.server.account_stores.mysql_schema import Account, Base
from openviking.server.account_stores.redis_mysql import RedisMySQLAccountStore
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.cache_config import CacheConfig


class _CacheRuntimeObserver:
    """Forward a native CacheRuntime while allowing test-time observation."""

    def __init__(self, runtime):
        """Store the native Runtime handle."""
        self._runtime = runtime

    @property
    def default_ttl_seconds(self):
        """Return the native Runtime default TTL."""
        return self._runtime.default_ttl_seconds

    def register_script(self, script_id: str, redis_lua: str) -> None:
        """Register a script through the native Runtime."""
        self._runtime.register_script(script_id, redis_lua)

    async def execute_script(self, script_id: str, keys: list[str], args: list[bytes]):
        """Execute a registered script through the native Runtime."""
        return await self._runtime.execute_script(script_id, keys, args)

    async def ping(self) -> None:
        """Check the native Runtime provider."""
        await self._runtime.ping()


@pytest.fixture
async def backend(tmp_path):
    raw_mysql = os.environ.get("OV_TEST_MYSQL")
    endpoint = os.environ.get("OV_TEST_REDIS")
    if not raw_mysql or not endpoint:
        pytest.skip("Set OV_TEST_MYSQL (JSON) and OV_TEST_REDIS for account-store contracts")
    redis = pytest.importorskip("redis")
    params = json.loads(raw_mysql)
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
    cache_client = create_agfs_client(
        RagfsBindingConfig(
            agfs=AGFSConfig(path=str(tmp_path), backend="memory"),
            cache=CacheConfig(
                instances={
                    "default": {"provider": "redis", "params": {"endpoints": [endpoint]}},
                    "accountstore": {"provider": "redis", "params": {"endpoints": [endpoint]}},
                }
            ),
            service_name=service,
            required_cache_instances={"Redis MySQL Account Store": "accountstore"},
        )
    )
    cache_runtime = _CacheRuntimeObserver(cache_client.cache_runtime("accountstore"))

    async def make_store():
        mysql = MySQLAccountStore(service_name=service, params=params)
        stores.append(mysql)
        await mysql.load()
        store = RedisMySQLAccountStore(
            service_name=service,
            mysql_store=mysql,
            cache_runtime=cache_runtime,
            params={"cache": {"instance": "accountstore"}},
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
        yield SimpleNamespace(reader=reader, writer=writer, mysql=mysql)
    finally:
        for store in reversed(stores):
            await store.close()
        account_tag = base64.urlsafe_b64encode(f"{service}\0acme".encode()).decode().rstrip("=")
        keys = list(inspection.scan_iter(match=f"ov:account-cache:v2:{{{account_tag}}}:*"))
        if keys:
            inspection.delete(*keys)
        inspection.close()
        with engine.begin() as connection:
            connection.execute(delete(Account).where(Account.service_name == service))
        engine.dispose()


async def snapshot(store, key="old-key"):
    return await store.get_auth_snapshot(key, account_id_hint="acme", user_id_hint="alice")


async def wait(event):
    assert await asyncio.to_thread(event.wait, 5), "operation did not reach its gate"


@pytest.fixture
def paused_snapshot(backend, monkeypatch):
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
    execute_script = backend.reader._cache_runtime.execute_script
    reads = 0

    async def observed_execute(script_id, keys, args):
        nonlocal reads
        result = await execute_script(script_id, keys, args)
        if script_id == "accountstore.v2.read_generations":
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

    monkeypatch.setattr(backend.reader._cache_runtime, "execute_script", observed_execute)
    monkeypatch.setattr(backend.mysql, "get_auth_snapshot", paused)
    yield gate
    gate.release.set()
    gate.cleanup.set()


@pytest.mark.parametrize("user_id", ["alice", None], ids=["user", "account"])
async def test_cached_snapshot_observes_role_groups_and_deletion(backend, user_id):
    reader, writer = backend.reader, backend.writer
    await snapshot(reader)
    await snapshot(reader)
    await writer.set_role("acme", "alice", "admin")
    await writer.create_group("acme", "team")
    await writer.add_group_members("acme", "team", ["alice"])
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
    await snapshot(backend.reader)
    await snapshot(backend.reader)
    await backend.mysql.set_role("acme", "alice", "admin")

    async def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(backend.reader._cache_runtime, "execute_script", offline)
    assert (await snapshot(backend.reader))["role"] == "admin"
    with monkeypatch.context() as patch:
        patch.setattr(backend.mysql, "get_auth_snapshot", offline)
        with pytest.raises(OSError, match="offline"):
            await snapshot(backend.reader)
    assert (await snapshot(backend.reader))["role"] == "admin"


async def test_cancelled_startup_releases_the_runtime(backend, monkeypatch):
    await backend.reader.close()
    await backend.writer.close()
    started = threading.Event()
    threads = []
    ping = backend.reader._cache_runtime.ping

    async def blocked_ping():
        await ping()
        threads.append(threading.current_thread())
        started.set()
        await asyncio.Future()

    with monkeypatch.context() as patch:
        patch.setattr(backend.reader._cache_runtime, "ping", blocked_ping)
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


async def test_main_batch_membership_and_conditional_key_rotation_invalidate_snapshots(
    backend, monkeypatch
):
    from openviking_cli.exceptions import UnauthenticatedError

    reader, writer = backend.reader, backend.writer
    await writer.create_user_with_api_key("acme", "bob", "user", "bob-key")
    await snapshot(reader)
    await snapshot(reader)
    await writer.create_group("acme", "team")
    assert await writer.add_group_members("acme", "team", ["alice", "bob", "alice"])
    assert (await snapshot(reader))["group_ids"] == ("team",)
    assert (await reader.get_auth_snapshot("bob-key"))["group_ids"] == ("team",)
    assert await writer.remove_group_members("acme", "team", ["alice", "bob", "alice"])
    assert (await snapshot(reader))["group_ids"] == ()
    assert (await reader.get_auth_snapshot("bob-key"))["group_ids"] == ()

    await writer.add_group_members("acme", "team", ["alice"])
    assert (await snapshot(reader))["group_ids"] == ("team",)
    remove = writer._mysql.remove_group_members

    async def commit_before_connection_loss(*args, **kwargs):
        assert await remove(*args, **kwargs)
        raise OSError("connection lost after commit")

    with monkeypatch.context() as patch:
        patch.setattr(writer._mysql, "remove_group_members", commit_before_connection_loss)
        with pytest.raises(OSError, match="connection lost after commit"):
            await writer.remove_group_members("acme", "team", ["alice", "alice"])
    assert await writer.get_group_members("acme", "team") == []
    assert (await snapshot(reader))["group_ids"] == ()

    await writer.replace_active_user_api_key("acme", "alice", "new-key", expected_api_key="old-key")
    assert await snapshot(reader) is None
    assert (await snapshot(reader, "new-key"))["user_id"] == "alice"
    with pytest.raises(UnauthenticatedError):
        await writer.replace_active_user_api_key(
            "acme", "alice", "another-key", expected_api_key="old-key"
        )
    assert (await snapshot(reader, "new-key"))["user_id"] == "alice"
