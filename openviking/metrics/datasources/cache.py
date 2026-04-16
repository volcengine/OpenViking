# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Cache event datasource.

This datasource standardizes cache hit/miss metrics production under the datasource layer.
It emits bounded low-cardinality events that are later consumed by `CacheCollector`.
"""

from __future__ import annotations

from .base import EventMetricDataSource

_LEVELS = ("L0", "L1", "L2")


class CacheEventDataSource(EventMetricDataSource):
    """Emit bounded cache hit/miss events for later translation by `CacheCollector`."""

    @staticmethod
    def record_hit(level: str) -> None:
        """
        Emit a cache-hit event for a supported cache level.

        Invalid levels are ignored silently so instrumentation never breaks business logic and
        never expands metrics cardinality with ad-hoc level names.
        """
        lvl = str(level)
        if lvl not in _LEVELS:
            return
        EventMetricDataSource._emit("cache.hit", {"level": lvl})

    @staticmethod
    def record_miss(level: str) -> None:
        """
        Emit a cache-miss event for a supported cache level.

        Invalid levels are ignored silently for the same bounded-cardinality reasons as
        `record_hit`.
        """
        lvl = str(level)
        if lvl not in _LEVELS:
            return
        EventMetricDataSource._emit("cache.miss", {"level": lvl})
