# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Resource-specific telemetry summary helpers."""

from __future__ import annotations

from typing import Any, Dict

from .context import get_current_telemetry
from .operation import OperationTelemetry
from .registry import register_telemetry, unregister_telemetry


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


def register_wait_telemetry(wait: bool) -> str:
    """Register current telemetry collector for async queue consumers when needed."""
    handle = get_current_telemetry()
    if not handle.telemetry_id:
        return ""
    if handle.enabled:
        register_telemetry(handle)
    return handle.telemetry_id


def unregister_wait_telemetry(telemetry_id: str) -> None:
    """Unregister request-scoped telemetry handle."""
    unregister_telemetry(telemetry_id)


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


def _resolve_queue_group(
    *,
    explicit_stats: Any,
    fallback_status: Any,
) -> Dict[str, int]:
    if explicit_stats is not None:
        return {
            "processed": explicit_stats.processed,
            "requeue_count": getattr(explicit_stats, "requeue_count", 0),
            "error_count": explicit_stats.error_count,
        }
    if fallback_status is None:
        return {"processed": 0, "requeue_count": 0, "error_count": 0}
    if isinstance(fallback_status, dict):
        return {
            "processed": int(fallback_status.get("processed", 0) or 0),
            "requeue_count": int(fallback_status.get("requeue_count", 0) or 0),
            "error_count": int(fallback_status.get("error_count", 0) or 0),
        }
    return {
        "processed": fallback_status.processed,
        "requeue_count": getattr(fallback_status, "requeue_count", 0),
        "error_count": fallback_status.error_count,
    }


def record_resource_wait_metrics(
    *,
    telemetry: OperationTelemetry | None = None,
    telemetry_id: str,
    queue_status: Dict[str, Any],
    root_uri: str | None,
) -> Dict[str, Dict[str, int]]:
    """Apply queue and DAG metrics to a resource operation collector."""
    telemetry = telemetry or get_current_telemetry()
    if not telemetry.enabled:
        return {
            "semantic": {"processed": 0, "requeue_count": 0, "error_count": 0},
            "embedding": {"processed": 0, "requeue_count": 0, "error_count": 0},
        }

    semantic = _resolve_queue_group(
        explicit_stats=_consume_semantic_request_stats(telemetry_id),
        fallback_status=queue_status.get("Semantic"),
    )
    embedding = _resolve_queue_group(
        explicit_stats=_consume_embedding_request_stats(telemetry_id),
        fallback_status=queue_status.get("Embedding"),
    )

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

    return {
        "semantic": semantic,
        "embedding": embedding,
    }


__all__ = [
    "build_queue_status_payload",
    "record_resource_wait_metrics",
    "register_wait_telemetry",
    "unregister_wait_telemetry",
]
