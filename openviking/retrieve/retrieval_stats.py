# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Thread-safe retrieval statistics accumulator.

Collects per-query metrics from the ``HierarchicalRetriever`` so that
the ``RetrievalObserver`` can report aggregate health and quality data
via the observer API.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class RetrievalStats:
    """Accumulated retrieval statistics.

    All counters are monotonically increasing within a server lifetime.
    The observer reads them to compute rates and averages.
    """

    total_queries: int = 0
    total_results: int = 0
    zero_result_queries: int = 0
    total_score_sum: float = 0.0
    max_score: float = 0.0
    min_score: float = float("inf")
    queries_by_type: Dict[str, int] = field(default_factory=dict)
    rerank_used: int = 0
    rerank_fallback: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    @property
    def avg_results_per_query(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.total_results / self.total_queries

    @property
    def zero_result_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.zero_result_queries / self.total_queries

    @property
    def avg_score(self) -> float:
        if self.total_results == 0:
            return 0.0
        return self.total_score_sum / self.total_results

    @property
    def avg_latency_ms(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.total_latency_ms / self.total_queries

    def to_dict(self) -> dict:
        """Serialize stats for API responses."""
        return {
            "total_queries": self.total_queries,
            "total_results": self.total_results,
            "zero_result_queries": self.zero_result_queries,
            "zero_result_rate": round(self.zero_result_rate, 4),
            "avg_results_per_query": round(self.avg_results_per_query, 2),
            "avg_score": round(self.avg_score, 4),
            "max_score": round(self.max_score, 4) if self.total_results > 0 else 0.0,
            "min_score": round(self.min_score, 4) if self.total_results > 0 else 0.0,
            "queries_by_type": dict(self.queries_by_type),
            "rerank_used": self.rerank_used,
            "rerank_fallback": self.rerank_fallback,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "max_latency_ms": round(self.max_latency_ms, 1),
        }


class RetrievalStatsCollector:
    """Thread-safe singleton that accumulates retrieval metrics.

    Usage in the retriever::

        from openviking.retrieve.retrieval_stats import get_stats_collector

        collector = get_stats_collector()
        collector.record_query(
            context_type="memory",
            result_count=3,
            scores=[0.82, 0.71, 0.55],
            latency_ms=42.5,
            rerank_used=True,
        )

    Usage in the observer::

        stats = get_stats_collector().snapshot()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats = RetrievalStats()

    def record_query(
        self,
        context_type: str,
        result_count: int,
        scores: list[float],
        latency_ms: float = 0.0,
        rerank_used: bool = False,
        rerank_fallback: bool = False,
    ) -> None:
        """Record metrics from a single retrieval query."""
        with self._lock:
            self._stats.total_queries += 1
            self._stats.total_results += result_count

            if result_count == 0:
                self._stats.zero_result_queries += 1

            for s in scores:
                self._stats.total_score_sum += s
                if s > self._stats.max_score:
                    self._stats.max_score = s
                if s < self._stats.min_score:
                    self._stats.min_score = s

            self._stats.queries_by_type[context_type] = (
                self._stats.queries_by_type.get(context_type, 0) + 1
            )

            if rerank_used:
                self._stats.rerank_used += 1
            if rerank_fallback:
                self._stats.rerank_fallback += 1

            self._stats.total_latency_ms += latency_ms
            if latency_ms > self._stats.max_latency_ms:
                self._stats.max_latency_ms = latency_ms

        try:
            from openviking.metrics.datasources import RetrievalStatsDataSource

            RetrievalStatsDataSource.record_retrieval(
                context_type=str(context_type or "unknown"),
                result_count=int(result_count),
                latency_seconds=latency_ms / 1000.0,
                rerank_used=bool(rerank_used),
                rerank_fallback=bool(rerank_fallback),
            )
        except Exception:
            pass

    def snapshot(self) -> RetrievalStats:
        """Return a copy of the current stats."""
        with self._lock:
            import copy

            return copy.deepcopy(self._stats)

    def reset(self) -> None:
        """Reset all counters (useful for testing)."""
        with self._lock:
            self._stats = RetrievalStats()


# Module-level singleton.
_collector: RetrievalStatsCollector | None = None
_collector_lock = threading.Lock()


def get_stats_collector() -> RetrievalStatsCollector:
    """Return the global stats collector singleton."""
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = RetrievalStatsCollector()
    return _collector
