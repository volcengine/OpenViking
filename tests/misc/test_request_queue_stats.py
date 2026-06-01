# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the shared Coordinator-backed RequestStatsAccumulator.

This accumulator replaced the two identical process-local class-dict LRU
patterns in TextEmbeddingHandler and SemanticProcessor. The behaviour under
test is the consume contract that resource_summary depends on:

  * never merged           -> consume() returns None (telemetry falls back to
                              queue_status)
  * merged, even all-zero  -> consume() returns a stats object (the fallback
                              must NOT trigger)
  * cross-call accumulation -> deltas merged across instances/messages add up
  * consume is destructive  -> a second consume() sees nothing
  * per-namespace isolation -> "semantic" and "embedding" never collide
  * local LRU eviction      -> abandoned (merged-never-consumed) ids are GC'd
"""

import pytest

from openviking.service.coordinator import (
    InProcessCoordinator,
    set_coordinator,
)
from openviking.telemetry.request_queue_stats import (
    RequestQueueStats,
    RequestStatsAccumulator,
)


@pytest.fixture(autouse=True)
def fresh_coordinator():
    """Isolate every test on its own in-process coordinator store."""
    set_coordinator(InProcessCoordinator())
    yield


@pytest.fixture
def acc():
    return RequestStatsAccumulator("embedding", max_tracked=1024)


class TestConsumeContract:
    def test_never_merged_returns_none(self, acc):
        assert acc.consume("tm_never") is None

    def test_empty_telemetry_id_is_ignored_on_merge(self, acc):
        acc.merge("", processed=5)
        assert acc.consume("") is None

    def test_merged_all_zero_returns_object_not_none(self, acc):
        # The presence marker must distinguish "merged (all-zero)" from
        # "never merged"; otherwise the telemetry fallback path mis-fires.
        acc.merge("tm_zero")
        stats = acc.consume("tm_zero")
        assert stats == RequestQueueStats(0, 0, 0)

    def test_merged_with_values_returns_them(self, acc):
        acc.merge("tm_a", processed=3, requeue_count=1, error_count=2)
        stats = acc.consume("tm_a")
        assert stats == RequestQueueStats(processed=3, requeue_count=1, error_count=2)

    def test_consume_is_destructive(self, acc):
        acc.merge("tm_a", processed=3)
        assert acc.consume("tm_a") is not None
        assert acc.consume("tm_a") is None


class TestAccumulation:
    def test_cross_call_accumulation_simulates_fan_out(self, acc):
        # Two separate merges (as if two load-balanced instances handled
        # different messages of the same request) fold into one total.
        acc.merge("tm_a", processed=2, requeue_count=1)
        acc.merge("tm_a", processed=3, error_count=4)
        stats = acc.consume("tm_a")
        assert stats == RequestQueueStats(processed=5, requeue_count=1, error_count=4)


class TestNamespaceIsolation:
    def test_same_id_distinct_namespaces_do_not_collide(self):
        semantic = RequestStatsAccumulator("semantic", max_tracked=256)
        embedding = RequestStatsAccumulator("embedding", max_tracked=256)
        semantic.merge("tm_shared", processed=1)
        embedding.merge("tm_shared", processed=9)
        assert semantic.consume("tm_shared") == RequestQueueStats(processed=1)
        assert embedding.consume("tm_shared") == RequestQueueStats(processed=9)


class TestLocalEviction:
    def test_lru_evicts_oldest_abandoned_id(self):
        acc = RequestStatsAccumulator("embedding", max_tracked=2)
        acc.merge("tm_1", processed=1)
        acc.merge("tm_2", processed=1)
        # Third distinct id overflows the bound; the oldest abandoned id is
        # evicted from the shared store, so its later consume() sees nothing.
        acc.merge("tm_3", processed=1)
        assert acc.consume("tm_1") is None
        assert acc.consume("tm_2") == RequestQueueStats(processed=1)
        assert acc.consume("tm_3") == RequestQueueStats(processed=1)

    def test_repeated_merge_of_same_id_does_not_self_evict(self):
        acc = RequestStatsAccumulator("embedding", max_tracked=1)
        acc.merge("tm_1", processed=1)
        acc.merge("tm_1", processed=1)  # popleft == append target -> no delete
        assert acc.consume("tm_1") == RequestQueueStats(processed=2)


# --- Redis backend parity (optional; skipped when fakeredis is absent) -------

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
def redis_acc(monkeypatch):
    import fakeredis

    from openviking.service.coordinator import RedisCoordinator

    server = fakeredis.FakeServer()

    def _from_url(*_args, **_kwargs):
        return fakeredis.FakeStrictRedis(server=server, decode_responses=True)

    monkeypatch.setattr("redis.Redis.from_url", staticmethod(_from_url), raising=False)
    set_coordinator(RedisCoordinator("redis://fake", key_prefix="t:", default_ttl_sec=0))
    return RequestStatsAccumulator("embedding", max_tracked=1024)


class TestRedisParity:
    def test_consume_contract_over_redis(self, redis_acc):
        assert redis_acc.consume("tm_never") is None
        redis_acc.merge("tm_zero")
        assert redis_acc.consume("tm_zero") == RequestQueueStats(0, 0, 0)

    def test_accumulation_over_redis(self, redis_acc):
        redis_acc.merge("tm_a", processed=2, requeue_count=1)
        redis_acc.merge("tm_a", processed=3, error_count=4)
        assert redis_acc.consume("tm_a") == RequestQueueStats(5, 1, 4)
        assert redis_acc.consume("tm_a") is None
