# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from collections import deque

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.pyagfs.leader import _LocalLeaderBackend


class FakeNativeLeader:
    def __init__(self, *, renew_results=()):
        self.acquire_calls = 0
        self.renew_results = deque(renew_results)
        self.released: list[str] = []

    def leader_validate(self):
        return None

    async def leader_try_acquire_async(self, key, holder_id, ttl_secs):
        del key, holder_id, ttl_secs
        self.acquire_calls += 1
        return f"lease-{self.acquire_calls}"

    async def leader_renew_async(self, lease_ref, ttl_secs):
        del lease_ref, ttl_secs
        return self.renew_results.popleft() if self.renew_results else True

    async def leader_release_async(self, lease_ref):
        self.released.append(lease_ref)


@pytest.mark.asyncio
async def test_candidate_signals_lost_term_before_reacquiring():
    native = FakeNativeLeader(renew_results=[False])
    candidate = AsyncAGFSClient(native).leader_candidate(
        "watch",
        "node-a",
        ttl_secs=0.2,
        renew_interval_secs=0.01,
        retry_interval_secs=0.01,
    )

    async with candidate:
        first = await anext(candidate)
        await asyncio.wait_for(first.stop_event.wait(), 1)
        assert native.acquire_calls == 1

        second = await anext(candidate)
        assert native.acquire_calls == 2
        assert native.released == ["lease-1"]
        assert not second.stop_event.is_set()

    assert second.stop_event.is_set()
    assert native.released == ["lease-1", "lease-2"]


@pytest.mark.asyncio
async def test_candidate_marks_term_stopped_when_renewal_errors():
    class BrokenRenew(FakeNativeLeader):
        async def leader_renew_async(self, lease_ref, ttl_secs):
            del lease_ref, ttl_secs
            raise RuntimeError("cache unavailable")

    candidate = AsyncAGFSClient(BrokenRenew()).leader_candidate(
        "watch",
        "node-a",
        ttl_secs=0.2,
        renew_interval_secs=0.01,
        retry_interval_secs=0.01,
    )

    async with candidate:
        leadership = await anext(candidate)
        await asyncio.wait_for(leadership.stop_event.wait(), 1)


@pytest.mark.asyncio
async def test_candidate_retries_transient_acquisition_error():
    class FlakyAcquire(FakeNativeLeader):
        async def leader_try_acquire_async(self, key, holder_id, ttl_secs):
            self.acquire_calls += 1
            if self.acquire_calls == 1:
                raise RuntimeError("cache unavailable")
            return await super().leader_try_acquire_async(key, holder_id, ttl_secs)

    native = FlakyAcquire()
    candidate = AsyncAGFSClient(native).leader_candidate(
        "watch",
        "node-a",
        ttl_secs=0.2,
        renew_interval_secs=0.05,
        retry_interval_secs=0.01,
    )

    async with candidate:
        leadership = await asyncio.wait_for(anext(candidate), 1)
        assert leadership.holder_id == "node-a"
        assert native.acquire_calls == 3


@pytest.mark.asyncio
async def test_candidate_stops_at_last_confirmed_deadline_when_renew_hangs():
    class HangingRenew(FakeNativeLeader):
        async def leader_renew_async(self, lease_ref, ttl_secs):
            del lease_ref, ttl_secs
            await asyncio.Event().wait()

    candidate = AsyncAGFSClient(HangingRenew()).leader_candidate(
        "watch",
        "node-a",
        ttl_secs=0.05,
        renew_interval_secs=0.01,
        retry_interval_secs=0.01,
    )

    async with candidate:
        leadership = await anext(candidate)
        await asyncio.wait_for(leadership.stop_event.wait(), 0.2)


@pytest.mark.asyncio
async def test_candidate_rejects_acquire_response_after_lease_deadline():
    class DelayedAcquire(FakeNativeLeader):
        async def leader_try_acquire_async(self, key, holder_id, ttl_secs):
            await asyncio.sleep(ttl_secs * 1.2)
            return await super().leader_try_acquire_async(key, holder_id, ttl_secs)

    native = DelayedAcquire()
    candidate = AsyncAGFSClient(native).leader_candidate(
        "watch",
        "node-a",
        ttl_secs=0.05,
        renew_interval_secs=0.02,
        retry_interval_secs=0.01,
    )

    async with candidate:
        waiting = asyncio.create_task(anext(candidate))
        await asyncio.sleep(0.08)
        assert native.released == ["lease-1"]
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting


@pytest.mark.asyncio
async def test_candidate_close_releases_inflight_acquire_instead_of_yielding():
    class GatedAcquire(FakeNativeLeader):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.finish = asyncio.Event()

        async def leader_try_acquire_async(self, key, holder_id, ttl_secs):
            self.started.set()
            await self.finish.wait()
            return await super().leader_try_acquire_async(key, holder_id, ttl_secs)

    native = GatedAcquire()
    candidate = AsyncAGFSClient(native).leader_candidate(
        "watch",
        "node-a",
        ttl_secs=1,
        renew_interval_secs=0.2,
        retry_interval_secs=0.01,
    )
    await candidate.__aenter__()
    waiting = asyncio.create_task(anext(candidate))
    await native.started.wait()

    await candidate.aclose()
    native.finish.set()

    with pytest.raises(StopAsyncIteration):
        await waiting
    assert native.released == ["lease-1"]


@pytest.mark.asyncio
async def test_candidate_cancellation_does_not_wait_for_unfinished_acquire():
    class HangingAcquire(FakeNativeLeader):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.finish = asyncio.Event()

        async def leader_try_acquire_async(self, key, holder_id, ttl_secs):
            self.started.set()
            await self.finish.wait()
            return await super().leader_try_acquire_async(key, holder_id, ttl_secs)

    native = HangingAcquire()
    candidate = AsyncAGFSClient(native).leader_candidate(
        "watch",
        "node-a",
        ttl_secs=1,
        renew_interval_secs=0.2,
        retry_interval_secs=0.01,
    )
    await candidate.__aenter__()
    waiting = asyncio.create_task(anext(candidate))
    await native.started.wait()

    waiting.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, 0.1)
    finally:
        native.finish.set()
        await asyncio.sleep(0)
        await candidate.aclose()
    assert native.released == []


def test_distributed_candidate_fails_fast_without_native_capability():
    with pytest.raises(RuntimeError, match="missing leader_validate"):
        AsyncAGFSClient(object()).leader_candidate(
            "watch",
            "node-a",
            ttl_secs=1,
            renew_interval_secs=0.2,
            retry_interval_secs=0.1,
        )


@pytest.mark.asyncio
async def test_local_candidates_compete_without_native_leader_api():
    agfs = AsyncAGFSClient(object())
    first_candidate = agfs.leader_candidate(
        "watch",
        "node-a",
        ttl_secs=1,
        renew_interval_secs=0.1,
        retry_interval_secs=0.01,
        mode="local",
    )
    second_candidate = agfs.leader_candidate(
        "watch",
        "node-b",
        ttl_secs=1,
        renew_interval_secs=0.1,
        retry_interval_secs=0.01,
        mode="local",
    )

    async with first_candidate, second_candidate:
        first = await anext(first_candidate)
        waiting = asyncio.create_task(anext(second_candidate))
        await asyncio.sleep(0.03)
        assert not waiting.done()

        await first_candidate.aclose()
        second = await asyncio.wait_for(waiting, 1)
        assert first.stop_event.is_set()
        assert second.holder_id == "node-b"


@pytest.mark.asyncio
async def test_local_leader_backend_is_shared_across_client_wrappers():
    native = object()
    first_agfs = AsyncAGFSClient(native)
    second_agfs = AsyncAGFSClient(native)
    first_candidate = first_agfs.leader_candidate(
        "watch_wrappers",
        "node-a",
        ttl_secs=1,
        renew_interval_secs=0.1,
        retry_interval_secs=0.01,
        mode="local",
    )
    second_candidate = second_agfs.leader_candidate(
        "watch_wrappers",
        "node-b",
        ttl_secs=1,
        renew_interval_secs=0.1,
        retry_interval_secs=0.01,
        mode="local",
    )

    async with first_candidate, second_candidate:
        first = await anext(first_candidate)
        waiting = asyncio.create_task(anext(second_candidate))
        await asyncio.sleep(0.03)
        assert not waiting.done()

        await first_candidate.aclose()
        second = await asyncio.wait_for(waiting, 1)
        assert first.stop_event.is_set()
        assert second.holder_id == "node-b"

def test_candidate_rejects_renewal_interval_at_or_above_ttl():
    with pytest.raises(ValueError, match="less than ttl"):
        AsyncAGFSClient(object()).leader_candidate(
            "watch",
            "node-a",
            ttl_secs=1,
            renew_interval_secs=1,
            retry_interval_secs=0.1,
            mode="local",
        )


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ({"key": "watch/bad"}, "leader key"),
        ({"holder_id": "node/bad"}, "holder_id"),
        ({"ttl_secs": 0.0005}, "ttl_secs"),
        ({"ttl_secs": 1e16}, "i64::MAX"),
        ({"ttl_secs": float("nan")}, "finite positive"),
        ({"renew_interval_secs": float("inf")}, "finite positive"),
    ],
)
def test_candidate_rejects_invalid_parameters(values, match):
    params = {
        "key": "watch",
        "holder_id": "node-a",
        "ttl_secs": 1,
        "renew_interval_secs": 0.2,
        "retry_interval_secs": 0.1,
        "mode": "local",
        **values,
    }

    with pytest.raises(ValueError, match=match):
        AsyncAGFSClient(object()).leader_candidate(**params)


@pytest.mark.asyncio
async def test_local_renew_expiry_removes_owner_mapping():
    backend = _LocalLeaderBackend()
    lease_ref = await backend.acquire("expired_local_key", "node-a", 0.01)
    assert lease_ref is not None
    await asyncio.sleep(0.03)

    assert not await backend.renew(lease_ref, 0.1)
    assert backend._owners == {}
