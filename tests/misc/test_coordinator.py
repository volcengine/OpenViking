# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the cross-instance Coordinator abstraction.

The in-process backend is the default and is exercised in full. The Redis
backend is validated against fakeredis when available (optional dependency)
and skipped otherwise, so the suite stays green in minimal environments.
"""

import threading

import pytest

from openviking.service.coordinator import (
    InProcessCoordinator,
    get_coordinator,
    set_coordinator,
)


@pytest.fixture
def coord():
    return InProcessCoordinator()


class TestInProcessCoordinatorInt:
    def test_incr_from_unset_starts_at_delta(self, coord):
        assert coord.incr("k") == 1
        assert coord.incr("k") == 2

    def test_incr_negative_delta(self, coord):
        coord.incr("k", 5)
        assert coord.incr("k", -2) == 3

    def test_get_int_unset_is_zero(self, coord):
        assert coord.get_int("missing") == 0

    def test_get_int_reflects_incr(self, coord):
        coord.incr("k", 7)
        assert coord.get_int("k") == 7


class TestInProcessCoordinatorSet:
    def test_sadd_scard(self, coord):
        coord.sadd("s", "a")
        coord.sadd("s", "b")
        coord.sadd("s", "a")  # duplicate is a no-op
        assert coord.scard("s") == 2

    def test_srem(self, coord):
        coord.sadd("s", "a")
        coord.sadd("s", "b")
        coord.srem("s", "a")
        assert coord.scard("s") == 1

    def test_srem_missing_member_is_safe(self, coord):
        coord.sadd("s", "a")
        coord.srem("s", "nope")
        assert coord.scard("s") == 1

    def test_scard_unset_is_zero(self, coord):
        assert coord.scard("missing") == 0


class TestInProcessCoordinatorList:
    def test_rpush_lrange_order(self, coord):
        coord.rpush("l", "x")
        coord.rpush("l", "y")
        assert coord.lrange("l") == ["x", "y"]

    def test_lrange_unset_is_empty(self, coord):
        assert coord.lrange("missing") == []

    def test_lrange_returns_copy(self, coord):
        coord.rpush("l", "x")
        returned = coord.lrange("l")
        returned.append("mutated")
        assert coord.lrange("l") == ["x"]


class TestInProcessCoordinatorClaim:
    def test_set_if_absent_first_caller_wins(self, coord):
        assert coord.set_if_absent("c", 60) is True
        assert coord.set_if_absent("c", 60) is False

    def test_set_if_absent_rearms_after_expiry(self, coord, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr("openviking.service.coordinator.time.monotonic", lambda: clock["t"])
        assert coord.set_if_absent("c", 45) is True
        clock["t"] += 30  # still inside window
        assert coord.set_if_absent("c", 45) is False
        clock["t"] += 20  # now past the 45s window
        assert coord.set_if_absent("c", 45) is True

    def test_set_if_absent_distinct_keys_independent(self, coord):
        assert coord.set_if_absent("a", 60) is True
        assert coord.set_if_absent("b", 60) is True

    def test_delete_clears_claim(self, coord):
        assert coord.set_if_absent("c", 60) is True
        coord.delete("c")
        assert coord.set_if_absent("c", 60) is True

    def test_expired_one_shot_claims_do_not_leak(self, coord, monkeypatch):
        # One-shot keys (claimed once, never revisited) must not accumulate in
        # the deadline map: once it grows past the prune threshold, expired
        # entries are swept so size tracks the live set, not the total ever seen.
        from openviking.service import coordinator as mod

        clock = {"t": 1000.0}
        monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(mod, "_CLAIM_PRUNE_MIN", 16)
        coord._claim_prune_at = 16

        for i in range(16):
            assert coord.set_if_absent(f"old:{i}", 45) is True
        clock["t"] += 100  # all 16 claims now expired

        # Claiming fresh keys past the threshold triggers a sweep of the dead ones.
        for i in range(16):
            assert coord.set_if_absent(f"new:{i}", 45) is True

        # The map should hold only the live ("new") claims, not the expired ones.
        assert len(coord._claim_deadlines) == 16
        assert all(k.startswith("new:") for k in coord._claim_deadlines)


class TestInProcessCoordinatorLifecycle:
    def test_delete_clears_all_types(self, coord):
        coord.incr("k")
        coord.sadd("k", "m")
        coord.rpush("k", "v")
        coord.delete("k")
        assert coord.get_int("k") == 0
        assert coord.scard("k") == 0
        assert coord.lrange("k") == []

    def test_delete_multiple_keys(self, coord):
        coord.incr("a")
        coord.incr("b")
        coord.delete("a", "b")
        assert coord.get_int("a") == 0
        assert coord.get_int("b") == 0

    def test_expire_is_noop_in_process(self, coord):
        coord.incr("k", 3)
        coord.expire("k", 1)
        assert coord.get_int("k") == 3


class TestInProcessCoordinatorConcurrency:
    def test_concurrent_incr_is_atomic(self, coord):
        threads = [
            threading.Thread(target=lambda: [coord.incr("k") for _ in range(1000)])
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert coord.get_int("k") == 8000


class TestCoordinatorRegistry:
    def test_get_coordinator_defaults_to_in_process(self):
        import openviking.service.coordinator as mod

        mod._coordinator = None  # reset for isolation
        result = get_coordinator()
        assert isinstance(result, InProcessCoordinator)

    def test_set_coordinator_overrides(self):
        custom = InProcessCoordinator()
        set_coordinator(custom)
        assert get_coordinator() is custom


# --- Redis backend (optional; skipped when fakeredis is absent) -------------

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
def redis_coord(monkeypatch):
    """RedisCoordinator backed by fakeredis, sharing one server across clients."""
    import fakeredis

    from openviking.service.coordinator import RedisCoordinator

    server = fakeredis.FakeServer()

    def _from_url(*_args, **_kwargs):
        return fakeredis.FakeStrictRedis(server=server, decode_responses=True)

    monkeypatch.setattr("redis.Redis.from_url", staticmethod(_from_url), raising=False)
    return RedisCoordinator("redis://fake", key_prefix="t:", default_ttl_sec=0)


class TestRedisCoordinatorParity:
    def test_incr_and_get(self, redis_coord):
        assert redis_coord.incr("k") == 1
        assert redis_coord.incr("k", 4) == 5
        assert redis_coord.get_int("k") == 5

    def test_set_ops(self, redis_coord):
        redis_coord.sadd("s", "a")
        redis_coord.sadd("s", "b")
        redis_coord.sadd("s", "a")
        assert redis_coord.scard("s") == 2
        redis_coord.srem("s", "a")
        assert redis_coord.scard("s") == 1

    def test_list_ops(self, redis_coord):
        redis_coord.rpush("l", "x")
        redis_coord.rpush("l", "y")
        assert redis_coord.lrange("l") == ["x", "y"]

    def test_delete(self, redis_coord):
        redis_coord.incr("k", 9)
        redis_coord.delete("k")
        assert redis_coord.get_int("k") == 0

    def test_key_prefix_isolation(self, redis_coord):
        redis_coord.incr("k", 2)
        # raw client should see the prefixed key, not the bare one
        assert redis_coord._client.get("t:k") == "2"
        assert redis_coord._client.get("k") is None

    def test_set_if_absent_is_atomic_claim(self, redis_coord):
        assert redis_coord.set_if_absent("c", 60) is True
        assert redis_coord.set_if_absent("c", 60) is False
        # The claim carries a TTL so it self-expires (re-arms the window).
        assert 0 < redis_coord._client.ttl("t:c") <= 60

    def test_set_if_absent_rearms_after_delete(self, redis_coord):
        assert redis_coord.set_if_absent("c", 60) is True
        redis_coord.delete("c")
        assert redis_coord.set_if_absent("c", 60) is True


class TestRedisCoordinatorClientInjection:
    """RedisCoordinator accepts a pre-built client object instead of a DSN string.

    This lets callers use any Redis-compatible client (e.g. a proprietary SDK
    such as credis.python) without subclassing RedisCoordinator.
    """

    def test_injected_client_is_used_directly(self):
        import fakeredis

        from openviking.service.coordinator import RedisCoordinator

        client = fakeredis.FakeStrictRedis(decode_responses=True)
        coord = RedisCoordinator(client, key_prefix="inj:", default_ttl_sec=0)
        assert coord._client is client

    def test_injected_client_ops_work(self):
        import fakeredis

        from openviking.service.coordinator import RedisCoordinator

        client = fakeredis.FakeStrictRedis(decode_responses=True)
        coord = RedisCoordinator(client, key_prefix="inj:", default_ttl_sec=0)

        assert coord.incr("x") == 1
        coord.sadd("s", "a")
        assert coord.scard("s") == 1
        assert coord.set_if_absent("c", 60) is True
        assert coord.set_if_absent("c", 60) is False

    def test_dsn_string_still_builds_client(self, monkeypatch):
        import fakeredis

        from openviking.service.coordinator import RedisCoordinator

        server = fakeredis.FakeServer()

        def _from_url(*_args, **_kwargs):
            return fakeredis.FakeStrictRedis(server=server, decode_responses=True)

        monkeypatch.setattr("redis.Redis.from_url", staticmethod(_from_url), raising=False)
        coord = RedisCoordinator("redis://fake", key_prefix="dsn:", default_ttl_sec=0)
        assert coord.incr("k") == 1
