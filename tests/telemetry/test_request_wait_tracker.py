# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio

import pytest

from openviking.service.coordinator import InProcessCoordinator, set_coordinator
from openviking.telemetry.request_wait_tracker import (
    _PENDING_SEM,
    _SEM_PROCESSED,
    _key,
    RequestWaitTracker,
)


class _CountingDistributedCoordinator(InProcessCoordinator):
    is_distributed = True

    def __init__(self, default_ttl_sec: int = 40) -> None:
        super().__init__()
        self.default_ttl_sec = default_ttl_sec
        self.expire_calls = []

    def expire(self, key: str, ttl_sec: int) -> None:
        self.expire_calls.append((key, ttl_sec))


class _FlakyDistributedCoordinator(_CountingDistributedCoordinator):
    def __init__(self, default_ttl_sec: int = 40) -> None:
        super().__init__(default_ttl_sec=default_ttl_sec)
        self._fail_counts = {
            "scard": 0,
            "expire": 0,
            "get_int": 0,
            "lrange": 0,
            "delete": 0,
        }

    def arm_failures(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if key not in self._fail_counts:
                raise KeyError(key)
            self._fail_counts[key] = int(value)

    def _maybe_fail(self, method: str) -> None:
        remaining = self._fail_counts.get(method, 0)
        if remaining > 0:
            self._fail_counts[method] = remaining - 1
            raise RuntimeError(f"transient {method} failure")

    def scard(self, key: str) -> int:
        self._maybe_fail("scard")
        return super().scard(key)

    def expire(self, key: str, ttl_sec: int) -> None:
        self._maybe_fail("expire")
        return super().expire(key, ttl_sec)

    def get_int(self, key: str) -> int:
        self._maybe_fail("get_int")
        return super().get_int(key)

    def lrange(self, key: str):
        self._maybe_fail("lrange")
        return super().lrange(key)

    def delete(self, *keys: str) -> None:
        self._maybe_fail("delete")
        return super().delete(*keys)

    def complete_semantic(self, telemetry_id: str, semantic_msg_id: str) -> None:
        super().srem(_key(telemetry_id, _PENDING_SEM), semantic_msg_id)
        super().incr(_key(telemetry_id, _SEM_PROCESSED), 1)


@pytest.fixture(autouse=True)
def _reset_request_wait_tracker_singleton():
    RequestWaitTracker._instance = None
    RequestWaitTracker._initialized = False
    set_coordinator(InProcessCoordinator())
    yield
    RequestWaitTracker._instance = None
    RequestWaitTracker._initialized = False
    set_coordinator(InProcessCoordinator())


def test_request_wait_tracker_cleanup_prevents_state_recreation():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_cleanup"

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")
    tracker.cleanup(telemetry_id)

    tracker.mark_semantic_done(telemetry_id, "semantic-1")
    tracker.mark_embedding_done(telemetry_id, "embedding-1")

    assert tracker.build_queue_status(telemetry_id) == {
        "Semantic": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
        "Embedding": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
    }


def test_request_wait_tracker_cleanup_prevents_root_recreation():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_late_root"

    tracker.register_request(telemetry_id)
    tracker.cleanup(telemetry_id)

    tracker.register_semantic_root(telemetry_id, "semantic-1")
    tracker.register_embedding_root(telemetry_id, "embedding-1")

    assert tracker.is_complete(telemetry_id) is True
    assert tracker.build_queue_status(telemetry_id) == {
        "Semantic": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
        "Embedding": {"processed": 0, "requeue_count": 0, "error_count": 0, "errors": []},
    }


def test_request_wait_tracker_records_requeues():
    tracker = RequestWaitTracker()
    telemetry_id = "tm_requeue"

    tracker.register_request(telemetry_id)
    tracker.record_semantic_requeue(telemetry_id)
    tracker.record_embedding_requeue(telemetry_id, delta=2)

    assert tracker.build_queue_status(telemetry_id) == {
        "Semantic": {"processed": 0, "requeue_count": 1, "error_count": 0, "errors": []},
        "Embedding": {"processed": 0, "requeue_count": 2, "error_count": 0, "errors": []},
    }


def test_request_wait_tracker_throttles_ttl_refresh(monkeypatch):
    telemetry_id = "tm_touch_throttle"
    coord = _CountingDistributedCoordinator(default_ttl_sec=40)
    set_coordinator(coord)
    tracker = RequestWaitTracker()
    now = 1000.0

    monkeypatch.setattr("openviking.telemetry.request_wait_tracker.time.monotonic", lambda: now)

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")
    assert len(coord.expire_calls) == 11

    tracker.record_semantic_requeue(telemetry_id)
    tracker.record_embedding_requeue(telemetry_id)
    assert len(coord.expire_calls) == 11

    now += 10.1
    tracker.record_embedding_error(telemetry_id, "boom")
    assert len(coord.expire_calls) == 22


def test_request_wait_tracker_cleanup_resets_touch_throttle(monkeypatch):
    telemetry_id = "tm_touch_cleanup"
    coord = _CountingDistributedCoordinator(default_ttl_sec=40)
    set_coordinator(coord)
    tracker = RequestWaitTracker()
    now = 2000.0

    monkeypatch.setattr("openviking.telemetry.request_wait_tracker.time.monotonic", lambda: now)

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")
    assert len(coord.expire_calls) == 11

    tracker.cleanup(telemetry_id)
    tracker.register_request(telemetry_id)
    tracker.register_embedding_root(telemetry_id, "embedding-1")
    assert len(coord.expire_calls) == 22


@pytest.mark.asyncio
async def test_request_wait_tracker_wait_retries_transient_coordinator_errors():
    telemetry_id = "tm_wait_retry"
    semantic_msg_id = "semantic-1"
    coord = _FlakyDistributedCoordinator(default_ttl_sec=40)
    set_coordinator(coord)
    tracker = RequestWaitTracker()

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, semantic_msg_id)
    coord.arm_failures(scard=2, expire=1, get_int=2, lrange=1)

    async def complete_later():
        await asyncio.sleep(0.05)
        coord.complete_semantic(telemetry_id, semantic_msg_id)

    completion_task = asyncio.create_task(complete_later())
    try:
        status = await tracker.wait_for_request(telemetry_id, timeout=1.0, poll_interval=0.01)
    finally:
        await completion_task

    assert status is not None
    assert status["Semantic"]["processed"] == 1
    assert status["Semantic"]["error_count"] == 0
    assert status["Embedding"]["processed"] == 0


@pytest.mark.asyncio
async def test_request_wait_tracker_wait_times_out_on_persistent_coordinator_errors():
    telemetry_id = "tm_wait_retry_timeout"
    coord = _FlakyDistributedCoordinator(default_ttl_sec=40)
    set_coordinator(coord)
    tracker = RequestWaitTracker()

    tracker.register_request(telemetry_id)
    tracker.register_semantic_root(telemetry_id, "semantic-1")
    coord.arm_failures(scard=1000)

    with pytest.raises(TimeoutError, match="Request processing not complete after 0.05s"):
        await tracker.wait_for_request(telemetry_id, timeout=0.05, poll_interval=0.01)


def test_request_wait_tracker_cleanup_swallows_distributed_delete_error():
    telemetry_id = "tm_cleanup_retry"
    coord = _FlakyDistributedCoordinator(default_ttl_sec=40)
    set_coordinator(coord)
    tracker = RequestWaitTracker()

    tracker.register_request(telemetry_id)
    coord.arm_failures(delete=1)

    tracker.cleanup(telemetry_id)
