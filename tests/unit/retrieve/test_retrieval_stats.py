# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.retrieve import retrieval_stats
from openviking.storage.observers.retrieval_observer import RetrievalObserver


@pytest.fixture
def collector(monkeypatch):
    collector = retrieval_stats.RetrievalStatsCollector()
    monkeypatch.setattr(retrieval_stats, "_collector", collector)
    return collector


def test_observer_reports_empty_searches_without_degrading_health(collector):
    observer = RetrievalObserver()
    assert "No retrieval queries recorded" in observer.get_status_table()
    initial = observer.get_status_json()
    assert initial["total_queries"] == 0
    assert initial["avg_score"] == initial["min_score"] == initial["max_score"] == 0.0

    collector.record_query("resource", 0, [], latency_ms=5.0)
    empty = observer.get_status_json()
    assert empty["total_queries"] == empty["zero_result_queries"] == 1
    assert empty["total_results"] == 0
    assert empty["zero_result_rate"] == 1.0
    assert empty["avg_latency_ms"] == 5.0
    assert "100.0%" in observer.get_status_table()
    assert observer.is_healthy()
    assert not observer.has_errors()


def test_observer_aggregates_results_and_keeps_snapshots_isolated(collector):
    collector.record_query("memory", 2, [0.9, 0.6], latency_ms=50.0, rerank_used=True)
    snapshot = collector.snapshot()
    collector.record_query("resource", 1, [0.3], latency_ms=20.0)
    collector.record_query("memory", 0, [], latency_ms=5.0)

    assert snapshot.total_queries == 1
    assert snapshot.queries_by_type == {"memory": 1}
    observer = RetrievalObserver()
    status = observer.get_status_json()
    assert status == {
        "total_queries": 3,
        "total_results": 3,
        "zero_result_queries": 1,
        "zero_result_rate": 0.3333,
        "avg_results_per_query": 1.0,
        "avg_score": 0.6,
        "max_score": 0.9,
        "min_score": 0.3,
        "queries_by_type": {"memory": 2, "resource": 1},
        "rerank_used": 1,
        "rerank_fallback": 0,
        "avg_latency_ms": 25.0,
        "max_latency_ms": 50.0,
    }
    table = observer.get_status_table()
    assert "33.3%" in table
    assert "0.3000 - 0.9000" in table
    assert "memory" in table and "resource" in table
