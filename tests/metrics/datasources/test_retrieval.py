"""Module-level tests for `openviking.metrics.datasources.retrieval`."""

from __future__ import annotations

import openviking.metrics.datasources.retrieval as retrieval


def test_retrieval_stats_datasource_emits_completed_event(patch_event_emit):
    """RetrievalStatsDataSource must emit a normalized `retrieval.completed` event payload."""

    retrieval.RetrievalStatsDataSource.record_retrieval(
        context_type="context_1",
        result_count=3,
        latency_seconds=0.12,
        rerank_used=True,
        rerank_fallback=False,
    )

    assert (
        "retrieval.completed",
        {
            "context_type": "context_1",
            "result_count": 3,
            "latency_seconds": 0.12,
            "rerank_used": True,
            "rerank_fallback": False,
        },
    ) in patch_event_emit


def test_retrieval_stats_datasource_normalizes_unknown_context_type(patch_event_emit):
    """Empty/falsey context types are normalized to `unknown`."""

    retrieval.RetrievalStatsDataSource.record_retrieval(
        context_type="",
        result_count=0,
        latency_seconds=0.0,
    )

    assert any(
        event_name == "retrieval.completed" and payload.get("context_type") == "unknown"
        for event_name, payload in patch_event_emit
    )
