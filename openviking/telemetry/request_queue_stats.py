# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Cross-instance request-scoped queue stats accumulator.

Both the semantic and embedding queue handlers accumulate per-request
processed/requeue/error counts keyed by telemetry_id, then a single
``consume`` at request completion folds them into the operation telemetry.
The counters previously lived in process-local class dicts, so load-balanced
instances each saw only the messages they personally handled. The counters
now live in the Coordinator, so a request whose messages fan out across
instances still aggregates correctly.

The per-instance LRU deque is a *local* memory-GC policy, not coordination
state: it evicts this instance's own abandoned (merged-but-never-consumed)
telemetry_ids from the shared store. With the in-process Coordinator the
bound is byte-for-byte identical to the prior class-dict LRU; with Redis the
backend's TTL is the real backstop and this eviction is merely redundant.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from openviking.service.coordinator import get_coordinator

_PREFIX = "rqs:"

# Per-telemetry_id key suffixes.
_PRESENT = "present"  # set marker: distinguishes "merged (maybe all-zero)" from "never merged"
_PROCESSED = "processed"
_REQUEUE = "requeue"
_ERROR = "error"
_SUFFIXES = (_PRESENT, _PROCESSED, _REQUEUE, _ERROR)


@dataclass
class RequestQueueStats:
    processed: int = 0
    requeue_count: int = 0
    error_count: int = 0


def _key(namespace: str, telemetry_id: str, suffix: str) -> str:
    return f"{_PREFIX}{namespace}:{telemetry_id}:{suffix}"


class RequestStatsAccumulator:
    """Coordinator-backed accumulator for one queue namespace (e.g. semantic/embedding)."""

    def __init__(self, namespace: str, max_tracked: int) -> None:
        self._namespace = namespace
        self._max_tracked = max_tracked
        self._order_lock = threading.Lock()
        self._order: Deque[str] = deque()

    def merge(
        self,
        telemetry_id: str,
        processed: int = 0,
        requeue_count: int = 0,
        error_count: int = 0,
    ) -> None:
        if not telemetry_id:
            return
        coord = get_coordinator()
        # Unconditional presence marker so consume() can tell "merged" from
        # "never merged" even when every delta is zero.
        coord.sadd(_key(self._namespace, telemetry_id, _PRESENT), "1")
        if processed:
            coord.incr(_key(self._namespace, telemetry_id, _PROCESSED), processed)
        if requeue_count:
            coord.incr(_key(self._namespace, telemetry_id, _REQUEUE), requeue_count)
        if error_count:
            coord.incr(_key(self._namespace, telemetry_id, _ERROR), error_count)
        self._track_for_eviction(telemetry_id, coord)

    def consume(self, telemetry_id: str) -> Optional[RequestQueueStats]:
        if not telemetry_id:
            return None
        coord = get_coordinator()
        if coord.scard(_key(self._namespace, telemetry_id, _PRESENT)) == 0:
            return None
        stats = RequestQueueStats(
            processed=coord.get_int(_key(self._namespace, telemetry_id, _PROCESSED)),
            requeue_count=coord.get_int(_key(self._namespace, telemetry_id, _REQUEUE)),
            error_count=coord.get_int(_key(self._namespace, telemetry_id, _ERROR)),
        )
        coord.delete(*(_key(self._namespace, telemetry_id, s) for s in _SUFFIXES))
        return stats

    def _track_for_eviction(self, telemetry_id: str, coord) -> None:
        with self._order_lock:
            self._order.append(telemetry_id)
            if len(self._order) <= self._max_tracked:
                return
            old = self._order.popleft()
        if old != telemetry_id and not coord.is_distributed:
            coord.delete(*(_key(self._namespace, old, s) for s in _SUFFIXES))


__all__ = ["RequestQueueStats", "RequestStatsAccumulator"]
