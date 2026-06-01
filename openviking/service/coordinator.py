# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cross-instance coordination primitives.

Several process-local trackers (semantic coalesce version, request-wait
tracker, embedding task tracker, text-embedding request stats) hold shared
counters/sets/lists in module- or class-level singletons. Under a single
process those singletons are consistent; across multiple load-balanced
server instances they diverge.

The ``Coordinator`` abstraction unifies these behind a small set of generic
KV primitives. The default ``Coordinator`` keeps state in an in-process dict
guarded by a lock (behaviourally identical to today's singletons, zero new
dependencies). ``RedisCoordinator`` maps each primitive onto an atomic Redis
command, making the same state visible and consistent across instances.

Selection is an explicit deployment-topology switch (``storage.coordination``)
and is NOT derived from ``queuefs.backend`` — sqlite-on-local-EBS (single
machine) and sqlite-on-shared-mount (multi instance) cannot be told apart
from config, so coordination must be opt-in.

The Coordinator is intentionally pure-data: it stores and computes, but never
executes business callbacks. Completion semantics (e.g. running a callback
when a counter hits zero) stay with the owning component.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Protocol

# In-process set_if_absent claims have no background reaper. To keep the
# deadline map from growing monotonically when many one-shot keys are claimed
# and never revisited, expired entries are swept in-line once the map exceeds
# this threshold (the threshold then re-grows relative to the live set, giving
# amortized O(1) per claim). Below it, sweeping a tiny map is not worth it.
_CLAIM_PRUNE_MIN = 128


class Coordinator(Protocol):
    """Generic cross-instance KV primitives.

    Keys are flat strings. The concrete backend decides where state lives;
    callers depend only on this protocol.
    """

    # True when state is shared across processes/instances (e.g. Redis). Owners
    # of completion callbacks use this to decide whether a remote instance might
    # drive a counter to zero (requiring a poll), or whether every decrement is
    # guaranteed in-process (the synchronous fast path is sufficient).
    is_distributed: bool

    # The configured default TTL (seconds) for mutated keys. Components that
    # need to refresh TTL on associated keys should read this rather than
    # hardcoding a value, so deployments with non-default coordination.ttl_sec
    # stay consistent.
    default_ttl_sec: int

    def incr(self, key: str, delta: int = 1) -> int:
        """Atomically add ``delta`` to the integer at ``key`` and return the new value."""
        ...

    def get_int(self, key: str) -> int:
        """Return the integer at ``key`` (0 if unset). Strongly consistent."""
        ...

    def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        """Atomically claim ``key`` for ``ttl_sec`` seconds.

        Returns ``True`` if the caller created the key (it was absent), ``False``
        if it already existed. A single round-trip (Redis ``SET NX EX``) so the
        check and the claim cannot race across instances. ``ttl_sec`` must be
        positive; the key auto-expires after it, re-arming the claim.
        """
        ...

    def sadd(self, key: str, member: str) -> None:
        """Add ``member`` to the set at ``key``."""
        ...

    def srem(self, key: str, member: str) -> None:
        """Remove ``member`` from the set at ``key``."""
        ...

    def scard(self, key: str) -> int:
        """Return the cardinality of the set at ``key`` (0 if unset)."""
        ...

    def rpush(self, key: str, value: str) -> None:
        """Append ``value`` to the list at ``key``."""
        ...

    def lrange(self, key: str) -> List[str]:
        """Return all elements of the list at ``key`` (empty if unset)."""
        ...

    def expire(self, key: str, ttl_sec: int) -> None:
        """Set a TTL on ``key``."""
        ...

    def delete(self, *keys: str) -> None:
        """Delete one or more keys."""
        ...


class InProcessCoordinator:
    """In-process coordinator backed by a dict and a lock.

    Behaviourally identical to the per-component singletons it replaces.
    This is the default backend; single-machine deployments incur no new
    dependency and observe no behaviour change.
    """

    is_distributed = False
    default_ttl_sec = 0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ints: Dict[str, int] = {}
        self._sets: Dict[str, set] = {}
        self._lists: Dict[str, List[str]] = {}
        # Monotonic expiry deadlines for set_if_absent claims. In-process state
        # has no background reaper, so a claim is treated as absent once its
        # deadline passes (lazy expiry on the next claim attempt). Expired
        # entries are also swept in bulk in set_if_absent once the map grows
        # past a threshold, so one-shot keys that are never revisited cannot
        # leak unboundedly.
        self._claim_deadlines: Dict[str, float] = {}
        # Sweep trigger: when the map exceeds this, drop expired entries and
        # re-arm the threshold relative to what survives.
        self._claim_prune_at: int = _CLAIM_PRUNE_MIN

    def incr(self, key: str, delta: int = 1) -> int:
        with self._lock:
            new_value = self._ints.get(key, 0) + delta
            self._ints[key] = new_value
            return new_value

    def get_int(self, key: str) -> int:
        with self._lock:
            return self._ints.get(key, 0)

    def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        now = time.monotonic()
        with self._lock:
            deadline = self._claim_deadlines.get(key)
            if deadline is not None and deadline > now:
                return False
            if len(self._claim_deadlines) >= self._claim_prune_at:
                self._prune_expired_claims(now)
            self._claim_deadlines[key] = now + ttl_sec
            return True

    def _prune_expired_claims(self, now: float) -> None:
        # Caller must hold self._lock. Drop every claim whose deadline has
        # passed, then re-arm the threshold relative to the survivors so the
        # map can grow with the live set but expired keys never accumulate.
        self._claim_deadlines = {k: d for k, d in self._claim_deadlines.items() if d > now}
        self._claim_prune_at = max(_CLAIM_PRUNE_MIN, len(self._claim_deadlines) * 2)

    def sadd(self, key: str, member: str) -> None:
        with self._lock:
            self._sets.setdefault(key, set()).add(member)

    def srem(self, key: str, member: str) -> None:
        with self._lock:
            members = self._sets.get(key)
            if members is not None:
                members.discard(member)

    def scard(self, key: str) -> int:
        with self._lock:
            members = self._sets.get(key)
            return len(members) if members is not None else 0

    def rpush(self, key: str, value: str) -> None:
        with self._lock:
            self._lists.setdefault(key, []).append(value)

    def lrange(self, key: str) -> List[str]:
        with self._lock:
            return list(self._lists.get(key, []))

    def expire(self, key: str, ttl_sec: int) -> None:
        # In-process state is bounded by explicit delete() calls; TTL is a
        # no-op here (the multi-instance backend honours it).
        return None

    def delete(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._ints.pop(key, None)
                self._sets.pop(key, None)
                self._lists.pop(key, None)
                self._claim_deadlines.pop(key, None)


class RedisCoordinator:
    """Redis-backed coordinator for multi-instance consistency.

    Each primitive maps onto an atomic Redis command, so concurrent updates
    from different instances are serialized by Redis. Requires the optional
    ``redis`` dependency (``pip install 'openviking[coordination]'``).

    A ``key_prefix`` namespaces all keys (multi-tenant / multi-cluster
    isolation). ``default_ttl_sec`` is applied to mutated keys so abandoned
    request/message state self-expires.
    """

    is_distributed = True

    def __init__(
        self,
        dsn: str,
        *,
        key_prefix: str = "ov:coord:",
        default_ttl_sec: int = 3600,
    ) -> None:
        try:
            import redis  # noqa: PLC0415  (lazy: optional dependency)
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "storage.coordination.backend='redis' requires the 'redis' package. "
                "Install with: pip install 'openviking[coordination]'"
            ) from exc

        self._client = redis.Redis.from_url(dsn, decode_responses=True)
        self._prefix = key_prefix
        self.default_ttl_sec = default_ttl_sec

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def incr(self, key: str, delta: int = 1) -> int:
        full = self._k(key)
        new_value = int(self._client.incrby(full, delta))
        if self.default_ttl_sec > 0:
            self._client.expire(full, self.default_ttl_sec)
        return new_value

    def get_int(self, key: str) -> int:
        value = self._client.get(self._k(key))
        return int(value) if value is not None else 0

    def set_if_absent(self, key: str, ttl_sec: int) -> bool:
        # SET key 1 NX EX ttl: atomically create-with-expiry. Returns truthy
        # only when the key did not already exist, so the claim/check is a
        # single round-trip with no cross-instance TOCTOU window.
        created = self._client.set(self._k(key), "1", nx=True, ex=ttl_sec)
        return bool(created)

    def sadd(self, key: str, member: str) -> None:
        full = self._k(key)
        self._client.sadd(full, member)
        if self.default_ttl_sec > 0:
            self._client.expire(full, self.default_ttl_sec)

    def srem(self, key: str, member: str) -> None:
        self._client.srem(self._k(key), member)

    def scard(self, key: str) -> int:
        return int(self._client.scard(self._k(key)))

    def rpush(self, key: str, value: str) -> None:
        full = self._k(key)
        self._client.rpush(full, value)
        if self.default_ttl_sec > 0:
            self._client.expire(full, self.default_ttl_sec)

    def lrange(self, key: str) -> List[str]:
        return list(self._client.lrange(self._k(key), 0, -1))

    def expire(self, key: str, ttl_sec: int) -> None:
        self._client.expire(self._k(key), ttl_sec)

    def delete(self, *keys: str) -> None:
        if keys:
            self._client.delete(*(self._k(key) for key in keys))


_coordinator: Optional[Coordinator] = None
_coordinator_lock = threading.Lock()


def set_coordinator(coordinator: Coordinator) -> None:
    """Install the process-wide coordinator (called once at startup)."""
    global _coordinator
    with _coordinator_lock:
        _coordinator = coordinator


def get_coordinator() -> Coordinator:
    """Return the process-wide coordinator.

    Falls back to an in-process coordinator when none was injected, so code
    paths that run before/without explicit setup keep working unchanged.
    """
    global _coordinator
    if _coordinator is None:
        with _coordinator_lock:
            if _coordinator is None:
                _coordinator = InProcessCoordinator()
    return _coordinator


__all__ = [
    "Coordinator",
    "InProcessCoordinator",
    "RedisCoordinator",
    "set_coordinator",
    "get_coordinator",
]
