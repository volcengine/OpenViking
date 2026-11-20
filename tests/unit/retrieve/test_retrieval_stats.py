# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for retrieval statistics and observer."""

from openviking.retrieve import retrieval_stats
from openviking.retrieve.retrieval_stats import RetrievalStatsCollector
from openviking.storage.observers.retrieval_observer import RetrievalObserver


def test_retrieval_observer_reports_aggregates_and_keeps_empty_queries_healthy(monkeypatch):
    collector = RetrievalStatsCollector()
    monkeypatch.setattr(retrieval_stats, "_collector", collector)
    observer = RetrievalObserver()

    assert observer.get_status_json()["total_queries"] == 0
    assert "No retrieval queries recorded" in observer.get_status_table()

    collector.record_query("resource", 0, [], latency_ms=5)
    empty = observer.get_status_json()
    assert empty["zero_result_rate"] == 1
    assert empty["min_score"] == empty["max_score"] == 0
    assert observer.is_healthy()
    assert not observer.has_errors()

    collector.record_query("memory", 2, [0.8, 0.6], latency_ms=30, rerank_used=True)
    collector.record_query("resource", 1, [0.4], latency_ms=25, rerank_fallback=True)

    assert observer.get_status_json() == {
        "total_queries": 3,
        "total_results": 3,
        "zero_result_queries": 1,
        "zero_result_rate": 0.3333,
        "avg_results_per_query": 1.0,
        "avg_score": 0.6,
        "max_score": 0.8,
        "min_score": 0.4,
        "queries_by_type": {"memory": 1, "resource": 2},
        "rerank_used": 1,
        "rerank_fallback": 1,
        "avg_latency_ms": 20.0,
        "max_latency_ms": 30.0,
    }
    table = observer.get_status_table()
    assert "33.3%" in table
    assert "memory" in table and "resource" in table


def test_stats_snapshot_is_stable_across_queries_and_reset():
    collector = RetrievalStatsCollector()
    collector.record_query("memory", 1, [0.9])
    snapshot = collector.snapshot()

    collector.record_query("resource", 1, [0.5])
    collector.reset()

    assert snapshot.total_queries == 1
    assert snapshot.queries_by_type == {"memory": 1}
    assert collector.snapshot().total_queries == 0
    assert collector.snapshot().queries_by_type == {}
