# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for LoopScopedAsyncClientCache close semantics."""

import asyncio
import threading
import warnings

import pytest

from openviking.utils.async_client_cache import LoopScopedAsyncClientCache


class StubAsyncClient:
    """Stub async client; can simulate one bound to an already-closed loop."""

    def __init__(self, aclose_error: Exception | None = None) -> None:
        self.aclose_error = aclose_error
        self.aclose_called = False
        self.sync_close_called = False
        self.sync_close_error: Exception | None = None

    async def aclose(self) -> None:
        self.aclose_called = True
        if self.aclose_error is not None:
            raise self.aclose_error

    def close(self) -> None:
        self.sync_close_called = True
        if self.sync_close_error is not None:
            raise self.sync_close_error


def _seed_client_on_short_lived_loop(cache: LoopScopedAsyncClientCache, factory):
    """Cache one client on a loop that is closed before the function returns.

    Returns the (closed) loop object so the caller can keep a strong
    reference: the WeakKeyDictionary entry must survive until close_all is
    called, mirroring production where loops linger via reference cycles.
    """

    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def run() -> None:
        loop = asyncio.new_event_loop()
        loop_holder["loop"] = loop

        async def seed() -> None:
            cache.get(factory)

        try:
            loop.run_until_complete(seed())
        finally:
            loop.close()

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return loop_holder["loop"]


def test_get_returns_per_loop_clients_and_cleans_up():
    cache = LoopScopedAsyncClientCache()

    async def use(factory):
        return cache.get(factory)

    loop = asyncio.new_event_loop()
    a = StubAsyncClient()
    b = StubAsyncClient()
    try:
        first = loop.run_until_complete(use(lambda: a))
        second = loop.run_until_complete(use(lambda: b))
        assert first is a
        assert second is a  # same loop -> same cached client
    finally:
        loop.close()

    other = asyncio.new_event_loop()
    try:
        third = other.run_until_complete(use(lambda: b))
        assert third is b  # different loop -> fresh client
    finally:
        other.close()


def test_close_all_does_not_raise_for_client_bound_to_closed_loop():
    cache = LoopScopedAsyncClientCache()
    dead = StubAsyncClient(aclose_error=RuntimeError("Event loop is closed"))

    dead_loop = _seed_client_on_short_lived_loop(cache, lambda: dead)
    assert dead_loop.is_closed()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        # Before the fix this raised RuntimeError("Event loop is closed")
        # from asyncio.run(...) and aborted the whole shutdown sequence.
        cache.close_all_with_aclose()

    assert cache.has_clients() is False


def test_close_all_continues_with_remaining_clients_after_one_fails():
    cache = LoopScopedAsyncClientCache()
    dead = StubAsyncClient(aclose_error=RuntimeError("Event loop is closed"))

    dead_loop = _seed_client_on_short_lived_loop(cache, lambda: dead)
    assert dead_loop.is_closed()

    healthy = StubAsyncClient()
    # seed the fallback client from a sync context (no running loop)
    assert cache.get(lambda: healthy) is healthy

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cache.close_all_with_aclose()

    assert dead.aclose_called
    assert dead.sync_close_called  # fallback was attempted for the dead one
    assert healthy.aclose_called  # healthy client still closed afterwards

    leak_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
    assert len(leak_warnings) == 1
    assert "StubAsyncClient" in str(leak_warnings[0].message)


def test_sync_close_fallback_failure_is_swallowed():
    cache = LoopScopedAsyncClientCache()
    dead = StubAsyncClient(aclose_error=RuntimeError("Event loop is closed"))
    dead.sync_close_error = RuntimeError("close also failed")

    dead_loop = _seed_client_on_short_lived_loop(cache, lambda: dead)
    assert dead_loop.is_closed()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        cache.close_all_with_aclose()  # must not raise either


def test_close_all_with_close_uses_sync_close():
    cache = LoopScopedAsyncClientCache()
    client = StubAsyncClient()

    async def seed():
        cache.get(lambda: client)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(seed())
    finally:
        loop.close()

    # loop of the cached client is closed; sync path via close_all_from_loop-less call
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        cache.close_all_with_close()

    assert client.sync_close_called
    assert client.aclose_called is False
