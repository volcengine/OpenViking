# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource-specific telemetry summary helpers."""

from __future__ import annotations

from typing import Any, Dict

from .operation import OperationTelemetry


def _consume_semantic_request_stats(telemetry_id: str):
    try:
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        return SemanticProcessor.consume_request_stats(telemetry_id)
    except Exception:
        return None


def _consume_embedding_request_stats(telemetry_id: str):
    try:
        from openviking.storage.collection_schemas import TextEmbeddingHandler

        return TextEmbeddingHandler.consume_request_stats(telemetry_id)
    except Exception:
        return None


def _consume_semantic_dag_stats(telemetry_id: str, root_uri: str | None):
    try:
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        return SemanticProcessor.consume_dag_stats(telemetry_id=telemetry_id, uri=root_uri)
    except Exception:
        return None


def build_queue_status_payload(status: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Convert queue status objects to response payload format."""
    return {
        name: {
            "processed": s.processed,
            "requeue_count": getattr(s, "requeue_count", 0),
            "error_count": s.error_count,
            "errors": [{"message": e.message} for e in s.errors],
        }
        for name, s in status.items()
    }


def _queue_metrics(stats: Any) -> Dict[str, int]:
    if stats is None:
        return {"processed": 0, "requeue_count": 0, "error_count": 0}
    return {
        "processed": stats.processed,
        "requeue_count": stats.requeue_count,
        "error_count": stats.error_count,
    }


def record_resource_queue_metrics(
    *,
    telemetry: OperationTelemetry,
    telemetry_id: str,
    root_uri: str | None,
) -> None:
    """Apply queue and DAG metrics to a resource operation collector."""
    if not telemetry.enabled:
        return

    semantic = _queue_metrics(_consume_semantic_request_stats(telemetry_id))
    embedding = _queue_metrics(_consume_embedding_request_stats(telemetry_id))

    telemetry.set("queue.semantic.processed", semantic["processed"])
    telemetry.set("queue.semantic.requeue_count", semantic["requeue_count"])
    telemetry.set("queue.semantic.error_count", semantic["error_count"])
    telemetry.set("queue.embedding.processed", embedding["processed"])
    telemetry.set("queue.embedding.requeue_count", embedding["requeue_count"])
    telemetry.set("queue.embedding.error_count", embedding["error_count"])

    dag_stats = _consume_semantic_dag_stats(telemetry_id, root_uri)
    if dag_stats is not None:
        telemetry.set("semantic_nodes.total", dag_stats.total_nodes)
        telemetry.set("semantic_nodes.done", dag_stats.done_nodes)
        telemetry.set("semantic_nodes.pending", dag_stats.pending_nodes)
        telemetry.set("semantic_nodes.running", dag_stats.in_progress_nodes)

__all__ = [
    "build_queue_status_payload",
    "record_resource_queue_metrics",
]
