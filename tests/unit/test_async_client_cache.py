# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for event-loop scoped async client caching."""

import asyncio
import copy
import threading

from openviking.utils.async_client_cache import LoopScopedAsyncClientCache


def test_loop_scoped_async_client_cache_reuses_within_loop_and_isolates_between_loops():
    cache = LoopScopedAsyncClientCache()
    created = []

    class Client:
        def close(self):
            return None

    def build_client():
        client = Client()
        created.append(client)
        return client

    async def get_twice():
        return cache.get(build_client), cache.get(build_client)

    main_first, main_second = asyncio.run(get_twice())
    worker_results = []

    def run_in_thread_loop():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            worker_results.append(loop.run_until_complete(get_twice()))
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    thread = threading.Thread(target=run_in_thread_loop)
    thread.start()
    thread.join()

    assert main_first is main_second
    assert worker_results[0][0] is worker_results[0][1]
    assert main_first is not worker_results[0][0]
    assert len(created) == 2


def test_loop_scoped_async_client_cache_copies_without_live_clients():
    cache = LoopScopedAsyncClientCache()
    client = object()

    assert cache.get(lambda: client) is client

    shallow = copy.copy(cache)
    deep = copy.deepcopy(cache)

    assert cache.has_clients()
    assert not shallow.has_clients()
    assert not deep.has_clients()


class _LoopBoundClient:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.closed_on = []

    async def aclose(self):
        running = asyncio.get_running_loop()
        if running is not self.loop:
            raise RuntimeError("Event loop is closed")
        self.closed_on.append(running)


def test_close_current_loop_clients_closes_only_clients_owned_by_that_loop():
    cache = LoopScopedAsyncClientCache()
    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()

    async def get_client():
        return cache.get(_LoopBoundClient)

    try:
        client_a = loop_a.run_until_complete(get_client())
        client_b = loop_b.run_until_complete(get_client())

        loop_b.run_until_complete(LoopScopedAsyncClientCache.close_current_loop_clients())

        assert client_b.closed_on == [loop_b]
        assert client_a.closed_on == []
        assert cache.has_clients()

        loop_a.run_until_complete(LoopScopedAsyncClientCache.close_current_loop_clients())

        assert client_a.closed_on == [loop_a]
        assert not cache.has_clients()
    finally:
        loop_a.close()
        loop_b.close()


def test_worker_loop_clients_do_not_fail_when_caches_close_on_another_loop():
    caches = [LoopScopedAsyncClientCache() for _ in range(3)]
    clients = []

    async def use_and_close():
        clients.extend(cache.get(_LoopBoundClient) for cache in caches)
        await LoopScopedAsyncClientCache.close_current_loop_clients()

    worker_loop = asyncio.new_event_loop()
    worker_loop.run_until_complete(use_and_close())
    worker_loop.close()

    errors = []

    async def service_close():
        asyncio.get_running_loop().set_exception_handler(lambda _loop, ctx: errors.append(ctx))
        for cache in caches:
            cache.close_all_with_aclose()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(service_close())

    assert [client.closed_on for client in clients] == [[worker_loop]] * 3
    assert not any(cache.has_clients() for cache in caches)
    assert errors == []
