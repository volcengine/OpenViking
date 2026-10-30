# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import threading

import pytest

from openviking.server.account_stores.redis_client import RedisClientHandle
from openviking.server.account_stores.redis_mysql import RedisMySQLAccountStore
from openviking.server.account_stores.redis_runtime import RedisRuntime


class _FakeMySQLStore:
    _service_name = "test"
    _engine_options = {"pool_size": 2}

    def __init__(self) -> None:
        self.loaded = False
        self.closed = False
        self.get_user_calls = 0

    async def load(self) -> None:
        self.loaded = True

    async def close(self) -> None:
        self.closed = True

    async def get_user(self, account_id: str, user_id: str):
        del account_id, user_id
        self.get_user_calls += 1
        return None


class _LoopBoundRedis:
    def __init__(self) -> None:
        self.owner_loop = None
        self.calls: list[tuple[str, asyncio.AbstractEventLoop, str]] = []
        self.closed = False

    def _record(self, method: str) -> None:
        loop = asyncio.get_running_loop()
        if self.owner_loop is None:
            self.owner_loop = loop
        assert loop is self.owner_loop
        self.calls.append((method, loop, threading.current_thread().name))

    async def ping(self) -> bool:
        self._record("ping")
        return True

    async def get(self, key: str) -> str:
        del key
        self._record("get")
        return json.dumps(
            {
                "found": True,
                "value": {"user_id": "alice", "role": "admin"},
            }
        )

    async def aclose(self) -> None:
        self._record("aclose")
        self.closed = True


class _BlockingRedis(_LoopBoundRedis):
    def __init__(self, *, block_ping: bool = False, block_close: bool = False) -> None:
        super().__init__()
        self.ping_started = threading.Event()
        self.close_started = threading.Event()
        self.continue_ping = asyncio.Event()
        self.continue_close = asyncio.Event()
        if not block_ping:
            self.continue_ping.set()
        if not block_close:
            self.continue_close.set()

    async def ping(self) -> bool:
        self._record("ping")
        self.ping_started.set()
        await self.continue_ping.wait()
        return True

    async def aclose(self) -> None:
        self._record("aclose")
        self.close_started.set()
        await self.continue_close.wait()
        self.closed = True

    def unblock(self) -> None:
        if self.owner_loop is not None and not self.owner_loop.is_closed():
            self.owner_loop.call_soon_threadsafe(self.continue_ping.set)
            self.owner_loop.call_soon_threadsafe(self.continue_close.set)


@pytest.fixture
async def lifecycle_stores(monkeypatch):
    runtime = RedisRuntime()
    pending_clients = []
    created_clients = []
    stores = []

    def create_client(params):
        del params
        redis = pending_clients.pop(0)
        created_clients.append(redis)
        return RedisClientHandle(redis)

    monkeypatch.setattr(
        "openviking.server.account_stores.redis_mysql.create_redis_client", create_client
    )
    monkeypatch.setattr(
        "openviking.server.account_stores.redis_mysql.get_shared_redis_runtime", lambda: runtime
    )

    def make_store(redis):
        mysql = _FakeMySQLStore()
        store = RedisMySQLAccountStore(service_name="test", params=_params(), mysql_store=mysql)
        pending_clients.append(redis)
        stores.append((store, redis))
        return store, mysql

    yield make_store, runtime, created_clients

    for _, redis in stores:
        redis.unblock()
    await asyncio.gather(*(store.close() for store, _ in stores), return_exceptions=True)
    # Also retire leaked leases when running these regressions against the old code.
    while runtime._leases:
        await runtime.release()
    with runtime._state_lock:
        stopped = runtime._stopped
    if stopped is not None:
        assert await asyncio.to_thread(stopped.wait, 2)


async def _wait_started(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 2)


async def _on_other_loop(operation) -> None:
    await asyncio.to_thread(asyncio.run, operation())


def _params() -> dict[str, object]:
    return {
        "cache": {
            "provider": "redis",
            "params": {
                "mode": "standalone",
                "endpoints": ["redis://127.0.0.1:6379"],
            },
        }
    }


async def test_owned_redis_client_lifecycle_uses_private_loop(monkeypatch) -> None:
    caller_loop = asyncio.get_running_loop()
    mysql = _FakeMySQLStore()
    redis = _LoopBoundRedis()
    factory_calls: list[tuple[asyncio.AbstractEventLoop, str]] = []

    def create_client(params):
        del params
        factory_calls.append((asyncio.get_running_loop(), threading.current_thread().name))
        return RedisClientHandle(redis)

    monkeypatch.setattr(
        "openviking.server.account_stores.redis_mysql.create_redis_client",
        create_client,
    )
    store = RedisMySQLAccountStore(
        service_name="test",
        params=_params(),
        mysql_store=mysql,
    )

    await store.load()
    try:
        assert await store.get_user("account", "alice") == {
            "user_id": "alice",
            "role": "admin",
        }
        assert mysql.get_user_calls == 0
    finally:
        await store.close()

    assert mysql.loaded
    assert mysql.closed
    assert redis.closed
    assert redis.owner_loop is not caller_loop
    assert factory_calls == [(redis.owner_loop, "openviking-redis")]
    assert [method for method, _, _ in redis.calls] == ["ping", "get", "aclose"]
    assert {thread_name for _, _, thread_name in redis.calls} == {"openviking-redis"}


async def test_injected_redis_client_is_not_closed() -> None:
    mysql = _FakeMySQLStore()
    redis = _LoopBoundRedis()
    store = RedisMySQLAccountStore(
        service_name="test",
        params=_params(),
        mysql_store=mysql,
        redis_client=redis,
    )

    await store.load()
    await store.close()

    assert [method for method, _, _ in redis.calls] == ["ping"]
    assert not redis.closed


async def test_loaded_stores_share_runtime_until_last_store_closes() -> None:
    first_redis = _LoopBoundRedis()
    second_redis = _LoopBoundRedis()
    first = RedisMySQLAccountStore(
        service_name="test",
        params=_params(),
        mysql_store=_FakeMySQLStore(),
        redis_client=first_redis,
    )
    second = RedisMySQLAccountStore(
        service_name="test",
        params=_params(),
        mysql_store=_FakeMySQLStore(),
        redis_client=second_redis,
    )

    await first.load()
    await second.load()
    assert first_redis.owner_loop is second_redis.owner_loop

    await first.close()
    assert await second.get_user("account", "alice") == {
        "user_id": "alice",
        "role": "admin",
    }
    await second.close()


async def test_cancel_after_redis_lease_acquisition_releases_store(lifecycle_stores, monkeypatch):
    make_store, runtime, created_clients = lifecycle_stores
    redis = _BlockingRedis(block_ping=True)
    store, mysql = make_store(redis)
    caller_loop = asyncio.get_running_loop()
    acquire = runtime._acquire_on_loop

    async def acquire_then_cancel():
        await acquire()
        caller_loop.call_soon_threadsafe(loading.cancel)

    monkeypatch.setattr(runtime, "_acquire_on_loop", acquire_then_cancel)
    loading = asyncio.create_task(store.load())

    with pytest.raises(asyncio.CancelledError):
        await loading

    assert runtime._leases == 0
    assert not store._redis_lease
    assert mysql.closed
    assert created_clients == [redis]
    assert redis.closed


async def test_repeated_load_cancellation_waits_for_client_cleanup(lifecycle_stores):
    make_store, runtime, _ = lifecycle_stores
    redis = _BlockingRedis(block_ping=True, block_close=True)
    store, mysql = make_store(redis)
    loading = asyncio.create_task(store.load())
    try:
        await _wait_started(redis.ping_started)
        loading.cancel()
        await _wait_started(redis.close_started)
        loading.cancel()
        await asyncio.sleep(0)
        assert not loading.done()
        assert not mysql.closed

        redis.unblock()
        with pytest.raises(asyncio.CancelledError):
            await loading

        assert redis.closed
        assert mysql.closed
        assert runtime._leases == 0
        assert not store._redis_lease
    finally:
        redis.unblock()
        await asyncio.gather(loading, return_exceptions=True)


@pytest.mark.parametrize("other_loops", [False, True])
async def test_concurrent_loads_acquire_one_redis_lease(lifecycle_stores, other_loops):
    make_store, runtime, created_clients = lifecycle_stores
    redis = _BlockingRedis(block_ping=True)
    store, _ = make_store(redis)
    loads = asyncio.gather(
        *(_on_other_loop(store.load) if other_loops else store.load() for _ in range(2))
    )
    try:
        await _wait_started(redis.ping_started)
        redis.unblock()
        await loads

        assert runtime._leases == 1
        assert store._redis_lease
        assert created_clients == [redis]
        await store.close()
        assert runtime._leases == 0
        assert [method for method, _, _ in redis.calls].count("aclose") == 1
    finally:
        redis.unblock()
        await asyncio.gather(loads, return_exceptions=True)


@pytest.mark.parametrize("other_loops", [False, True])
async def test_concurrent_closes_preserve_another_stores_lease(lifecycle_stores, other_loops):
    make_store, runtime, _ = lifecycle_stores
    redis = _BlockingRedis(block_close=True)
    first, _ = make_store(redis)
    second, second_mysql = make_store(_BlockingRedis())
    await first.load()
    await second.load()
    closes = asyncio.gather(
        *(_on_other_loop(first.close) if other_loops else first.close() for _ in range(2)),
        return_exceptions=True,
    )
    try:
        await _wait_started(redis.close_started)
        redis.unblock()
        assert await closes == [None, None]

        assert runtime._leases == 1
        assert second._redis_lease
        assert await second.get_user("account", "alice") == {
            "user_id": "alice",
            "role": "admin",
        }
        assert second_mysql.get_user_calls == 0
        assert [method for method, _, _ in redis.calls].count("aclose") == 1
        await second.close()
        assert runtime._leases == 0
    finally:
        redis.unblock()
        await asyncio.gather(closes, return_exceptions=True)


async def test_load_waits_for_in_progress_close(lifecycle_stores, monkeypatch):
    make_store, runtime, _ = lifecycle_stores
    first_redis = _BlockingRedis(block_close=True)
    store, _ = make_store(first_redis)
    await store.load()
    closing = asyncio.create_task(store.close())
    reloading = None
    try:
        await _wait_started(first_redis.close_started)
        second_redis = _BlockingRedis()
        monkeypatch.setattr(
            "openviking.server.account_stores.redis_mysql.create_redis_client",
            lambda params: RedisClientHandle(second_redis),
        )
        reloading = asyncio.create_task(store.load())
        await asyncio.sleep(0)

        async def barrier():
            pass

        # Admit the reload while close is still blocked on the client.
        await runtime.run(barrier)
        first_redis.unblock()
        await asyncio.gather(closing, reloading)

        assert first_redis.closed
        assert runtime._leases == 1
        assert store._redis_lease
        assert store._redis is second_redis
        assert await store.get_user("account", "alice") == {
            "user_id": "alice",
            "role": "admin",
        }
        await store.close()
        assert second_redis.closed
        assert runtime._leases == 0
    finally:
        first_redis.unblock()
        tasks = [closing] if reloading is None else [closing, reloading]
        await asyncio.gather(*tasks, return_exceptions=True)
