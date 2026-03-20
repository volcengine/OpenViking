# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
PrometheusObserver: Prometheus metrics exporter via the BaseObserver pattern.

Collects metrics from the existing observer infrastructure and exposes
them in Prometheus text exposition format at the /metrics endpoint.
"""

import threading
from typing import Dict, List, Optional, Tuple

from openviking.storage.observers.base_observer import BaseObserver
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class _Counter:
    """Simple thread-safe monotonic counter."""

    def __init__(self) -> None:
        self._value: float = 0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1) -> None:
        with self._lock:
            self._value += amount

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


class _Gauge:
    """Simple thread-safe gauge."""

    def __init__(self) -> None:
        self._value: float = 0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1) -> None:
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1) -> None:
        with self._lock:
            self._value -= amount

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


class _Histogram:
    """Simple thread-safe histogram with fixed buckets."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self, buckets: Optional[Tuple[float, ...]] = None) -> None:
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._bucket_counts: List[int] = [0] * len(self._buckets)
        self._sum: float = 0
        self._count: int = 0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    self._bucket_counts[i] += 1

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def sum(self) -> float:
        with self._lock:
            return self._sum

    def snapshot(self) -> Tuple[List[Tuple[str, int]], int, float]:
        """Return ([(le, cumulative_count), ...], count, sum)."""
        with self._lock:
            cumulative = 0
            buckets = []
            for i, bound in enumerate(self._buckets):
                cumulative += self._bucket_counts[i]
                buckets.append((str(bound), cumulative))
            buckets.append(("+Inf", self._count))
            return buckets, self._count, self._sum


class PrometheusObserver(BaseObserver):
    """
    PrometheusObserver: Exports system metrics in Prometheus text exposition format.

    Integrates with the existing observer chain by reading from the same
    data sources (RetrievalStatsCollector, QueueManager, VLM token tracking)
    and maintaining its own counters/histograms that are updated on each
    scrape or via explicit record_*() calls.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # Counters
        self._retrieval_requests_total = _Counter()
        self._embedding_requests_total = _Counter()
        self._vlm_calls_total = _Counter()
        self._cache_hits: Dict[str, _Counter] = {}
        self._cache_misses: Dict[str, _Counter] = {}

        # Histograms
        self._retrieval_latency = _Histogram()
        self._embedding_latency = _Histogram()
        self._vlm_call_duration = _Histogram()

        # Gauges
        self._active_sessions = _Gauge()
        self._cache_size_bytes: Dict[str, _Gauge] = {}

        # Track last-seen retrieval total to compute deltas
        self._last_retrieval_total: int = 0

    # -- Public recording API (for middleware / hooks) --

    def record_retrieval(self, latency_seconds: float) -> None:
        """Record a retrieval request with its latency."""
        self._retrieval_requests_total.inc()
        self._retrieval_latency.observe(latency_seconds)

    def record_embedding(self, latency_seconds: float) -> None:
        """Record an embedding request with its latency."""
        self._embedding_requests_total.inc()
        self._embedding_latency.observe(latency_seconds)

    def record_vlm_call(self, duration_seconds: float) -> None:
        """Record a VLM call with its duration."""
        self._vlm_calls_total.inc()
        self._vlm_call_duration.observe(duration_seconds)

    def record_cache_hit(self, level: str) -> None:
        """Record a cache hit at the given level (e.g. 'L0', 'L1', 'L2')."""
        if level not in self._cache_hits:
            self._cache_hits[level] = _Counter()
        self._cache_hits[level].inc()

    def record_cache_miss(self, level: str) -> None:
        """Record a cache miss at the given level."""
        if level not in self._cache_misses:
            self._cache_misses[level] = _Counter()
        self._cache_misses[level].inc()

    def set_active_sessions(self, count: int) -> None:
        """Update the active sessions gauge."""
        self._active_sessions.set(count)

    def set_cache_size_bytes(self, level: str, size: int) -> None:
        """Update cache size gauge for a given level."""
        if level not in self._cache_size_bytes:
            self._cache_size_bytes[level] = _Gauge()
        self._cache_size_bytes[level].set(size)

    # -- Sync from existing observers (called on /metrics scrape) --

    def _sync_from_retrieval_stats(self) -> None:
        """Pull latest metrics from the global RetrievalStatsCollector."""
        try:
            from openviking.retrieve.retrieval_stats import get_stats_collector

            stats = get_stats_collector().snapshot()
            delta = stats.total_queries - self._last_retrieval_total
            if delta > 0:
                self._retrieval_requests_total.inc(delta)
                self._last_retrieval_total = stats.total_queries
                if stats.avg_latency_ms > 0:
                    avg_latency_s = stats.avg_latency_ms / 1000.0
                    for _ in range(delta):
                        self._retrieval_latency.observe(avg_latency_s)
        except Exception:
            pass

    def _sync_from_queue_manager(self) -> None:
        """Pull queue metrics for embedding request counts."""
        try:
            from openviking.storage.queuefs import get_queue_manager

            qm = get_queue_manager()
            from openviking_cli.utils import run_async

            statuses = run_async(qm.check_status())
            if "Embedding" in statuses:
                embedding_status = statuses["Embedding"]
                current = embedding_status.processed
                if current > self._embedding_requests_total.value:
                    delta = current - self._embedding_requests_total.value
                    self._embedding_requests_total.inc(delta)
        except Exception:
            pass

    # -- Prometheus text format rendering --

    def render_metrics(self) -> str:
        """Render all metrics in Prometheus text exposition format.

        Called by the /metrics endpoint handler.
        """
        self._sync_from_retrieval_stats()
        self._sync_from_queue_manager()

        lines: List[str] = []

        # Retrieval
        lines.append("# HELP openviking_retrieval_requests_total Total retrieval requests.")
        lines.append("# TYPE openviking_retrieval_requests_total counter")
        lines.append(f"openviking_retrieval_requests_total {self._retrieval_requests_total.value}")

        lines.append(
            "# HELP openviking_retrieval_latency_seconds Retrieval request latency in seconds."
        )
        lines.append("# TYPE openviking_retrieval_latency_seconds histogram")
        buckets, count, total = self._retrieval_latency.snapshot()
        for le, cum in buckets:
            lines.append(f'openviking_retrieval_latency_seconds_bucket{{le="{le}"}} {cum}')
        lines.append(f"openviking_retrieval_latency_seconds_count {count}")
        lines.append(f"openviking_retrieval_latency_seconds_sum {total}")

        # Embedding
        lines.append("# HELP openviking_embedding_requests_total Total embedding requests.")
        lines.append("# TYPE openviking_embedding_requests_total counter")
        lines.append(f"openviking_embedding_requests_total {self._embedding_requests_total.value}")

        lines.append(
            "# HELP openviking_embedding_latency_seconds Embedding request latency in seconds."
        )
        lines.append("# TYPE openviking_embedding_latency_seconds histogram")
        buckets, count, total = self._embedding_latency.snapshot()
        for le, cum in buckets:
            lines.append(f'openviking_embedding_latency_seconds_bucket{{le="{le}"}} {cum}')
        lines.append(f"openviking_embedding_latency_seconds_count {count}")
        lines.append(f"openviking_embedding_latency_seconds_sum {total}")

        # VLM
        lines.append("# HELP openviking_vlm_calls_total Total VLM calls.")
        lines.append("# TYPE openviking_vlm_calls_total counter")
        lines.append(f"openviking_vlm_calls_total {self._vlm_calls_total.value}")

        lines.append("# HELP openviking_vlm_call_duration_seconds VLM call duration in seconds.")
        lines.append("# TYPE openviking_vlm_call_duration_seconds histogram")
        buckets, count, total = self._vlm_call_duration.snapshot()
        for le, cum in buckets:
            lines.append(f'openviking_vlm_call_duration_seconds_bucket{{le="{le}"}} {cum}')
        lines.append(f"openviking_vlm_call_duration_seconds_count {count}")
        lines.append(f"openviking_vlm_call_duration_seconds_sum {total}")

        # Cache hits/misses
        all_levels = sorted(set(list(self._cache_hits.keys()) + list(self._cache_misses.keys())))
        if all_levels:
            lines.append("# HELP openviking_cache_hits_total Cache hits by level.")
            lines.append("# TYPE openviking_cache_hits_total counter")
            for level in all_levels:
                val = self._cache_hits[level].value if level in self._cache_hits else 0
                lines.append(f'openviking_cache_hits_total{{level="{level}"}} {val}')

            lines.append("# HELP openviking_cache_misses_total Cache misses by level.")
            lines.append("# TYPE openviking_cache_misses_total counter")
            for level in all_levels:
                val = self._cache_misses[level].value if level in self._cache_misses else 0
                lines.append(f'openviking_cache_misses_total{{level="{level}"}} {val}')

        # Active sessions gauge
        lines.append("# HELP openviking_active_sessions Number of active sessions.")
        lines.append("# TYPE openviking_active_sessions gauge")
        lines.append(f"openviking_active_sessions {self._active_sessions.value}")

        # Cache size gauges
        if self._cache_size_bytes:
            lines.append("# HELP openviking_cache_size_bytes Cache size in bytes by level.")
            lines.append("# TYPE openviking_cache_size_bytes gauge")
            for level in sorted(self._cache_size_bytes.keys()):
                val = self._cache_size_bytes[level].value
                lines.append(f'openviking_cache_size_bytes{{level="{level}"}} {val}')

        lines.append("")  # Trailing newline
        return "\n".join(lines)

    # -- BaseObserver interface --

    def get_status_table(self) -> str:
        """Format Prometheus metrics status as a string."""
        retrieval = self._retrieval_requests_total.value
        embedding = self._embedding_requests_total.value
        vlm = self._vlm_calls_total.value
        return (
            f"Prometheus Metrics Exporter: active\n"
            f"  retrieval_requests_total: {retrieval}\n"
            f"  embedding_requests_total: {embedding}\n"
            f"  vlm_calls_total: {vlm}"
        )

    def is_healthy(self) -> bool:
        """Prometheus exporter is always healthy if instantiated."""
        return True

    def has_errors(self) -> bool:
        """Prometheus exporter does not track errors."""
        return False
