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


def _consume_semantic_tree_stats(telemetry_id: str, root_uri: str | None):
    try:
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        return SemanticProcessor.consume_tree_stats(telemetry_id=telemetry_id, uri=root_uri)
    except Exception:
        return None


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
    queue_timing: Dict[str, Dict[str, float]] | None = None,
) -> None:
    """Apply queue and semantic-tree metrics to a resource operation collector."""
    if not telemetry.enabled:
        return

    semantic = _queue_metrics(_consume_semantic_request_stats(telemetry_id))
    embedding = _queue_metrics(_consume_embedding_request_stats(telemetry_id))
    timing = queue_timing or {}
    semantic_timing = timing.get("semantic", {"queue_wait_ms": 0.0, "execute_ms": 0.0})
    embedding_timing = timing.get("embedding", {"queue_wait_ms": 0.0, "execute_ms": 0.0})

    telemetry.set("queue.semantic.processed", semantic["processed"])
    telemetry.set("queue.semantic.requeue_count", semantic["requeue_count"])
    telemetry.set("queue.semantic.error_count", semantic["error_count"])
    telemetry.set("queue.semantic.queue_wait.duration_ms", semantic_timing["queue_wait_ms"])
    telemetry.set("queue.semantic.execute.duration_ms", semantic_timing["execute_ms"])
    telemetry.set("queue.embedding.processed", embedding["processed"])
    telemetry.set("queue.embedding.requeue_count", embedding["requeue_count"])
    telemetry.set("queue.embedding.error_count", embedding["error_count"])
    telemetry.set("queue.embedding.queue_wait.duration_ms", embedding_timing["queue_wait_ms"])
    telemetry.set("queue.embedding.execute.duration_ms", embedding_timing["execute_ms"])

    tree_stats = _consume_semantic_tree_stats(telemetry_id, root_uri)
    if tree_stats is not None:
        telemetry.set("semantic_nodes.total", tree_stats.total_nodes)
        telemetry.set("semantic_nodes.done", tree_stats.done_nodes)
        telemetry.set("semantic_nodes.pending", tree_stats.pending_nodes)
        telemetry.set("semantic_nodes.running", tree_stats.in_progress_nodes)


__all__ = [
    "record_resource_queue_metrics",
]
