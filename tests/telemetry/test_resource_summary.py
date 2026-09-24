from openviking.telemetry.operation import OperationTelemetry


def test_record_resource_queue_metrics_collects_queue_and_tree_stats(monkeypatch):
    from openviking.telemetry.resource_summary import record_resource_queue_metrics

    telemetry = OperationTelemetry(operation="resources.add_resource", enabled=True)
    telemetry_id = telemetry.telemetry_id

    class _SemanticStats:
        processed = 7
        requeue_count = 0
        error_count = 2

    class _EmbeddingStats:
        processed = 11
        requeue_count = 0
        error_count = 1

    class _Timing:
        @staticmethod
        def get_queue_timing(_tid):
            return {
                "semantic": {"queue_wait_ms": 12.5, "execute_ms": 30.0},
                "embedding": {"queue_wait_ms": 8.0, "execute_ms": 20.0},
            }

    class _SemanticTreeStats:
        total_nodes = 9
        done_nodes = 8
        pending_nodes = 1
        in_progress_nodes = 0

    monkeypatch.setattr(
        "openviking.telemetry.resource_summary._consume_semantic_request_stats",
        lambda _tid: _SemanticStats(),
    )
    monkeypatch.setattr(
        "openviking.telemetry.resource_summary._consume_embedding_request_stats",
        lambda _tid: _EmbeddingStats(),
    )
    monkeypatch.setattr(
        "openviking.telemetry.resource_summary._consume_semantic_tree_stats",
        lambda _tid, _uri: _SemanticTreeStats(),
    )
    monkeypatch.setattr(
        "openviking.telemetry.resource_summary.get_request_wait_tracker",
        lambda: _Timing(),
    )

    record_resource_queue_metrics(
        telemetry=telemetry,
        telemetry_id=telemetry_id,
        root_uri="viking://resources/demo",
    )

    summary = telemetry.finish().summary
    assert summary["queue"]["semantic"]["processed"] == 7
    assert summary["queue"]["semantic"]["error_count"] == 2
    assert summary["queue"]["embedding"]["processed"] == 11
    assert summary["queue"]["embedding"]["error_count"] == 1
    assert summary["queue"]["semantic"]["queue_wait"]["duration_ms"] == 12.5
    assert summary["queue"]["semantic"]["execute"]["duration_ms"] == 30.0
    assert summary["queue"]["embedding"]["queue_wait"]["duration_ms"] == 8.0
    assert summary["queue"]["embedding"]["execute"]["duration_ms"] == 20.0
    assert summary["semantic_nodes"]["total"] == 9
    assert summary["semantic_nodes"]["done"] == 8
    assert summary["semantic_nodes"]["pending"] == 1
    assert "running" not in summary["semantic_nodes"]
