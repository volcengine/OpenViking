# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for event-loop scoped async client caching."""

import asyncio
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
