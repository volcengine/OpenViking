from types import SimpleNamespace

from openviking.telemetry.operation import OperationTelemetry


def test_record_resource_wait_metrics_collects_queue_and_dag_stats(monkeypatch):
    from openviking.telemetry.resource_summary import record_resource_wait_metrics

    telemetry = OperationTelemetry(operation="resources.add_resource", enabled=True)
    telemetry_id = telemetry.telemetry_id
    queue_status = {
        "Semantic": SimpleNamespace(processed=3, error_count=1, errors=[]),
        "Embedding": SimpleNamespace(processed=5, error_count=0, errors=[]),
    }

    class _SemanticStats:
        processed = 7
        error_count = 2

    class _EmbeddingStats:
        processed = 11
        error_count = 1

    class _DagStats:
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
        "openviking.telemetry.resource_summary._consume_semantic_dag_stats",
        lambda _tid, _uri: _DagStats(),
    )

    record_resource_wait_metrics(
        telemetry=telemetry,
        telemetry_id=telemetry_id,
        queue_status=queue_status,
        root_uri="viking://resources/demo",
    )

    summary = telemetry.finish().summary
    assert summary["queue"]["semantic"]["processed"] == 7
    assert summary["queue"]["semantic"]["error_count"] == 2
    assert summary["queue"]["embedding"]["processed"] == 11
    assert summary["queue"]["embedding"]["error_count"] == 1
    assert summary["semantic_nodes"]["total"] == 9
    assert summary["semantic_nodes"]["done"] == 8
    assert summary["semantic_nodes"]["pending"] == 1
    assert summary["semantic_nodes"]["running"] == 0
