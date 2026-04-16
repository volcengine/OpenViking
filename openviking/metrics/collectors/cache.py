# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: CacheCollector.

Records cache hit/miss counters by cache level (L0/L1/L2).
The cache level label is intentionally bounded to avoid cardinality issues.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class CacheCollector(EventMetricCollector):
    """
    Translate cache hit and miss events into bounded cache-level counters.

    The collector is purely event-driven and only accepts the small set of cache levels emitted by
    the cache datasource, which keeps the resulting Prometheus series count stable.
    """

    DOMAIN: ClassVar[str] = "cache"
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_hits_total
    # e.g.: openviking_cache_hits_total
    HITS_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "hits", unit="total")
    # rule: <METRICS_NAMESPACE>_<DOMAIN>_misses_total
    # e.g.: openviking_cache_misses_total
    MISSES_TOTAL: ClassVar[str] = MetricCollector.metric_name(DOMAIN, "misses", unit="total")

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"cache.hit", "cache.miss"})

    def collect(self, registry=None) -> None:
        """Implement the collector interface as a no-op because cache metrics are push-driven."""
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Route one cache event payload to the matching hit or miss counter update.

        The payload is expected to contain a normalized `level` field supplied by the datasource.
        """
        if event_name == "cache.hit":
            self.record_hit(registry, level=str(payload["level"]))
            return
        self.record_miss(registry, level=str(payload["level"]))

    def record_hit(self, registry, *, level: str) -> None:
        """Increment the cache-hit counter for one normalized cache level value."""
        registry.inc_counter(
            self.HITS_TOTAL,
            labels={"level": str(level)},
            label_names=("level",),
        )

    def record_miss(self, registry, *, level: str) -> None:
        """Increment the cache-miss counter for one normalized cache level value."""
        registry.inc_counter(
            self.MISSES_TOTAL,
            labels={"level": str(level)},
            label_names=("level",),
        )
