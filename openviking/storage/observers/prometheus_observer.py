# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""PrometheusObserver: Prometheus metrics exporter for OpenViking."""

import threading
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple

from openviking.storage.observers.base_observer import BaseObserver


class _Histogram:
    """Thread-safe in-memory histogram for Prometheus exposition."""

    def __init__(self, buckets: Iterable[float]):
        self._buckets = sorted(float(b) for b in buckets)
        self._bucket_counts: List[int] = [0] * len(self._buckets)
        self._sum = 0.0
        self._count = 0

    def observe(self, value: float) -> None:
        self._sum += value
        self._count += 1
        for idx, upper in enumerate(self._buckets):
            if value <= upper:
                self._bucket_counts[idx] += 1
        # +Inf bucket is implied by _count.

    def snapshot(self) -> Tuple[List[float], List[int], float, int]:
        return list(self._buckets), list(self._bucket_counts), self._sum, self._count


class PrometheusObserver(BaseObserver):
    """Observer that records and renders Prometheus metrics text format."""

    DEFAULT_LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    CACHE_LEVELS = ("L0", "L1", "L2")

    def __init__(self):
        self._lock = Lock()

        self._retrieval_requests_total = 0
        self._embedding_requests_total = 0
        self._vlm_calls_total = 0

        self._cache_hits_total: Dict[str, int] = dict.fromkeys(self.CACHE_LEVELS, 0)
        self._cache_misses_total: Dict[str, int] = dict.fromkeys(self.CACHE_LEVELS, 0)

        self._retrieval_latency_seconds = _Histogram(self.DEFAULT_LATENCY_BUCKETS)
        self._embedding_latency_seconds = _Histogram(self.DEFAULT_LATENCY_BUCKETS)
        self._vlm_call_duration_seconds = _Histogram(self.DEFAULT_LATENCY_BUCKETS)

    def get_status_table(self) -> str:
        """Return Prometheus exposition for observer status views."""
        return self.render_metrics()

    def is_healthy(self) -> bool:
        return True

    def has_errors(self) -> bool:
        return False

    def record_retrieval(self, latency_seconds: float) -> None:
        with self._lock:
            self._retrieval_requests_total += 1
            self._retrieval_latency_seconds.observe(float(latency_seconds))

    def record_embedding(self, latency_seconds: float) -> None:
        with self._lock:
            self._embedding_requests_total += 1
            self._embedding_latency_seconds.observe(float(latency_seconds))

    def record_vlm_call(self, duration_seconds: float) -> None:
        with self._lock:
            self._vlm_calls_total += 1
            self._vlm_call_duration_seconds.observe(float(duration_seconds))

    def record_cache_hit(self, level: str) -> None:
        with self._lock:
            if level not in self._cache_hits_total:
                self._cache_hits_total[level] = 0
            self._cache_hits_total[level] += 1

    def record_cache_miss(self, level: str) -> None:
        with self._lock:
            if level not in self._cache_misses_total:
                self._cache_misses_total[level] = 0
            self._cache_misses_total[level] += 1

    def render_metrics(self) -> str:
        with self._lock:
            retrieval_requests_total = self._retrieval_requests_total
            embedding_requests_total = self._embedding_requests_total
            vlm_calls_total = self._vlm_calls_total
            cache_hits_total = dict(self._cache_hits_total)
            cache_misses_total = dict(self._cache_misses_total)
            retrieval_hist = self._retrieval_latency_seconds.snapshot()
            embedding_hist = self._embedding_latency_seconds.snapshot()
            vlm_hist = self._vlm_call_duration_seconds.snapshot()

        lines: List[str] = []
        lines.extend(
            [
                "# HELP openviking_retrieval_requests_total Total retrieval requests.",
                "# TYPE openviking_retrieval_requests_total counter",
                f"openviking_retrieval_requests_total {retrieval_requests_total}",
                "# HELP openviking_embedding_requests_total Total embedding requests.",
                "# TYPE openviking_embedding_requests_total counter",
                f"openviking_embedding_requests_total {embedding_requests_total}",
                "# HELP openviking_vlm_calls_total Total VLM calls.",
                "# TYPE openviking_vlm_calls_total counter",
                f"openviking_vlm_calls_total {vlm_calls_total}",
            ]
        )

        self._append_histogram(
            lines=lines,
            name="openviking_retrieval_latency_seconds",
            help_text="Retrieval request latency in seconds.",
            histogram=retrieval_hist,
        )
        self._append_histogram(
            lines=lines,
            name="openviking_embedding_latency_seconds",
            help_text="Embedding request latency in seconds.",
            histogram=embedding_hist,
        )
        self._append_histogram(
            lines=lines,
            name="openviking_vlm_call_duration_seconds",
            help_text="VLM call duration in seconds.",
            histogram=vlm_hist,
        )

        self._append_labeled_counter(
            lines=lines,
            name="openviking_cache_hits_total",
            help_text="Total cache hits by cache level.",
            counters=cache_hits_total,
        )
        self._append_labeled_counter(
            lines=lines,
            name="openviking_cache_misses_total",
            help_text="Total cache misses by cache level.",
            counters=cache_misses_total,
        )

        return "\n".join(lines) + "\n"

    @staticmethod
    def _append_labeled_counter(
        lines: List[str],
        name: str,
        help_text: str,
        counters: Dict[str, int],
    ) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} counter")
        for level in sorted(counters):
            lines.append(f'{name}{{level="{level}"}} {counters[level]}')

    @staticmethod
    def _append_histogram(
        lines: List[str],
        name: str,
        help_text: str,
        histogram: Tuple[List[float], List[int], float, int],
    ) -> None:
        buckets, bucket_counts, total_sum, total_count = histogram
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} histogram")

        for upper, count in zip(buckets, bucket_counts):
            lines.append(f'{name}_bucket{{le="{upper:g}"}} {count}')
        lines.append(f'{name}_bucket{{le="+Inf"}} {total_count}')
        lines.append(f"{name}_sum {total_sum}")
        lines.append(f"{name}_count {total_count}")


# Module-level singleton so that data-collection hooks can record metrics
# without requiring access to the FastAPI app state.
_observer: Optional[PrometheusObserver] = None
_observer_lock = threading.Lock()


def set_prometheus_observer(observer: Optional[PrometheusObserver]) -> None:
    """Set the global PrometheusObserver instance (called during app startup)."""
    global _observer
    with _observer_lock:
        _observer = observer


def get_prometheus_observer() -> Optional[PrometheusObserver]:
    """Return the global PrometheusObserver, or None when metrics are disabled."""
    return _observer
