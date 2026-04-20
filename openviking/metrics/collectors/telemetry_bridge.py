# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""
Event collector: TelemetryBridgeCollector.

This collector converts the aggregated telemetry summary of a single operation/request
into Prometheus metrics.

Input:
- A single `telemetry.summary` event per operation (best-effort).

Output:
- Low-cardinality counters/histograms for operation duration, tokens, vector search counts,
  semantic node counts, and resource stage timing.

Design notes:
- The telemetry summary is already aggregated; we avoid adding labels that would cause
  cardinality explosions (no session_id/resource_uri/etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

from openviking.metrics.core.base import MetricCollector

from .base import EventMetricCollector


@dataclass
class TelemetryBridgeCollector(EventMetricCollector):
    """
    Translate aggregated telemetry summaries into low-cardinality Prometheus metrics.

    The collector expands one summary event into multiple families that represent operation
    throughput, latency, vector retrieval work, semantic-node counts, and resource-stage timing.
    """

    DOMAIN_OPERATION: ClassVar[str] = "operation"
    DOMAIN_VECTOR: ClassVar[str] = "vector"
    DOMAIN_SEMANTIC: ClassVar[str] = "semantic"
    DOMAIN_MEMORY: ClassVar[str] = "memory"
    DOMAIN_RESOURCE: ClassVar[str] = "resource"

    # rule: <METRICS_NAMESPACE>_<DOMAIN_OPERATION>_requests_total
    # e.g.: openviking_operation_requests_total
    OPERATION_REQUESTS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_OPERATION, "requests", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_OPERATION>_duration_seconds
    # e.g.: openviking_operation_duration_seconds
    OPERATION_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_OPERATION, "duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_OPERATION>_tokens_total
    # e.g.: openviking_operation_tokens_total
    OPERATION_TOKENS_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_OPERATION, "tokens", unit="total"
    )

    # rule: <METRICS_NAMESPACE>_<DOMAIN_VECTOR>_searches_total
    # e.g.: openviking_vector_searches_total
    VECTOR_SEARCHES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_VECTOR, "searches", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_VECTOR>_scored_total
    # e.g.: openviking_vector_scored_total
    VECTOR_SCORED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_VECTOR, "scored", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_VECTOR>_passed_total
    # e.g.: openviking_vector_passed_total
    VECTOR_PASSED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_VECTOR, "passed", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_VECTOR>_returned_total
    # e.g.: openviking_vector_returned_total
    VECTOR_RETURNED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_VECTOR, "returned", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_VECTOR>_scanned_total
    # e.g.: openviking_vector_scanned_total
    VECTOR_SCANNED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_VECTOR, "scanned", unit="total"
    )

    # rule: <METRICS_NAMESPACE>_<DOMAIN_SEMANTIC>_nodes_total
    # e.g.: openviking_semantic_nodes_total
    SEMANTIC_NODES_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_SEMANTIC, "nodes", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_MEMORY>_extracted_total
    # e.g.: openviking_memory_extracted_total
    MEMORY_EXTRACTED_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_MEMORY, "extracted", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_RESOURCE>_stage_total
    # e.g.: openviking_resource_stage_total
    RESOURCE_STAGE_TOTAL: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_RESOURCE, "stage", unit="total"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_RESOURCE>_stage_duration_seconds
    # e.g.: openviking_resource_stage_duration_seconds
    RESOURCE_STAGE_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_RESOURCE, "stage_duration", unit="seconds"
    )
    # rule: <METRICS_NAMESPACE>_<DOMAIN_RESOURCE>_wait_duration_seconds
    # e.g.: openviking_resource_wait_duration_seconds
    RESOURCE_WAIT_DURATION_SECONDS: ClassVar[str] = MetricCollector.metric_name(
        DOMAIN_RESOURCE, "wait_duration", unit="seconds"
    )

    SUPPORTED_EVENTS: ClassVar[frozenset[str]] = frozenset({"telemetry.summary"})

    @staticmethod
    def _extract_total_tokens(payload: Any) -> int:
        """Extract a total token count from either an int payload or a `{total: ...}` mapping."""
        if isinstance(payload, Mapping):
            return int(payload.get("total", 0) or 0)
        return int(payload or 0)

    def _iter_stage_token_metrics(self, tokens: Mapping[str, Any]) -> list[tuple[str, str, int]]:
        """Expand telemetry token payloads into `(stage, token_type, value)` tuples."""
        stages = tokens.get("stages") or {}
        if isinstance(stages, Mapping) and stages:
            metrics: list[tuple[str, str, int]] = []
            for stage, stage_payload in stages.items():
                if not isinstance(stage_payload, Mapping):
                    continue
                llm_payload = stage_payload.get("llm") or {}
                if isinstance(llm_payload, Mapping):
                    llm_input = int(llm_payload.get("input", 0) or 0)
                    llm_output = int(llm_payload.get("output", 0) or 0)
                    if llm_input > 0:
                        metrics.append((str(stage), "llm_input", llm_input))
                    if llm_output > 0:
                        metrics.append((str(stage), "llm_output", llm_output))
                for source_name, token_type in (
                    ("embedding", "embedding"),
                    ("rerank", "rerank"),
                ):
                    total = self._extract_total_tokens(stage_payload.get(source_name, 0) or 0)
                    if total > 0:
                        metrics.append((str(stage), token_type, total))
            return metrics

        llm = tokens.get("llm") or {}
        metrics = []
        llm_input = int(llm.get("input", 0) or 0)
        llm_output = int(llm.get("output", 0) or 0)
        if llm_input > 0:
            metrics.append(("vlm", "llm_input", llm_input))
        if llm_output > 0:
            metrics.append(("vlm", "llm_output", llm_output))

        embedding_total = self._extract_total_tokens(tokens.get("embedding", 0) or 0)
        if embedding_total > 0:
            metrics.append(("embed_resource", "embedding", embedding_total))

        rerank_total = self._extract_total_tokens(tokens.get("rerank", 0) or 0)
        if rerank_total > 0:
            metrics.append(("rerank", "rerank", rerank_total))
        return metrics

    def collect(self, registry=None) -> None:
        """
        Implement the unified collector interface as a no-op for this event-driven collector.

        Telemetry summaries are pushed in from runtime code, so scrape-time collection has no
        additional work to perform.
        """
        return None

    def receive_hook(self, event_name: str, payload: dict, registry) -> None:
        """
        Translate the supported telemetry summary event into derived metric writes.

        The payload is expected to contain one already-aggregated summary for a completed logical
        operation or request.
        """
        summary = payload.get("summary")
        if not isinstance(summary, Mapping):
            return
        self.record_telemetry_summary(registry, summary)

    def record_telemetry_summary(self, registry, summary: Mapping[str, Any]) -> None:
        """
        Record counters, histograms, and gauges derived from one aggregated telemetry summary.

        This method is intentionally low-cardinality: it emits bounded labels and avoids copying
        identifiers such as session ids or resource URIs into Prometheus series.
        """
        operation = str(summary.get("operation", "unknown"))
        status = str(summary.get("status", "ok"))

        registry.inc_counter(
            self.OPERATION_REQUESTS_TOTAL,
            labels={"operation": operation, "status": status},
            label_names=("operation", "status"),
        )

        duration_seconds = float(summary.get("duration_ms", 0.0)) / 1000.0
        registry.observe_histogram(
            self.OPERATION_DURATION_SECONDS,
            duration_seconds,
            labels={"operation": operation, "status": status},
            label_names=("operation", "status"),
        )

        tokens = summary.get("tokens") or {}
        for stage, token_type, value in self._iter_stage_token_metrics(tokens):
            if value <= 0:
                continue
            registry.inc_counter(
                self.OPERATION_TOKENS_TOTAL,
                labels={"operation": operation, "stage": stage, "token_type": token_type},
                label_names=("operation", "stage", "token_type"),
                amount=value,
            )

        vector = summary.get("vector") or {}
        for metric_name, key in (
            (self.VECTOR_SEARCHES_TOTAL, "searches"),
            (self.VECTOR_SCORED_TOTAL, "scored"),
            (self.VECTOR_PASSED_TOTAL, "passed"),
            (self.VECTOR_RETURNED_TOTAL, "returned"),
            (self.VECTOR_SCANNED_TOTAL, "scanned"),
        ):
            value = int(vector.get(key, 0) or 0)
            if value <= 0:
                continue
            registry.inc_counter(
                metric_name,
                labels={"operation": operation},
                label_names=("operation",),
                amount=value,
            )

        semantic_nodes = summary.get("semantic_nodes") or {}
        for k, v in semantic_nodes.items():
            value = int(v or 0)
            if value < 0:
                continue
            registry.inc_counter(
                self.SEMANTIC_NODES_TOTAL,
                labels={"status": str(k)},
                label_names=("status",),
                amount=value,
            )

        memory = summary.get("memory") or {}
        extracted = int(memory.get("extracted", 0) or 0)
        if extracted > 0:
            registry.inc_counter(
                self.MEMORY_EXTRACTED_TOTAL,
                labels={"operation": operation},
                label_names=("operation",),
                amount=extracted,
            )

        resource = summary.get("resource") or {}
        if resource:
            self._record_resource_stage(
                registry, resource=resource, operation=operation, status=status
            )

    def _record_resource_stage(
        self, registry, *, resource: Mapping[str, Any], operation: str, status: str
    ) -> None:
        """
        Record resource-stage metrics from the nested resource timing section of a summary.

        The method walks a fixed stage map so dashboards can rely on a stable stage vocabulary
        even when some stages are absent from a particular summary payload.
        """
        stage_defs = [
            ("request", ("request",)),
            ("process", ("process",)),
            ("parse", ("process", "parse")),
            ("finalize", ("process", "finalize")),
            ("summarize", ("process", "summarize")),
            ("wait", ("wait",)),
            ("watch", ("watch",)),
        ]

        for stage, path in stage_defs:
            node: Any = resource
            for p in path:
                node = (node or {}).get(p)
            if not isinstance(node, Mapping):
                continue
            duration_ms = float(node.get("duration_ms", 0.0) or 0.0)
            stage_status = status
            if stage == "parse" and stage_status == "ok":
                warnings = int(node.get("warnings_count", 0) or 0)
                if warnings > 0:
                    stage_status = "warning"
            labels = {"stage": stage, "status": stage_status}
            registry.inc_counter(
                self.RESOURCE_STAGE_TOTAL,
                labels=labels,
                label_names=("stage", "status"),
            )
            registry.observe_histogram(
                self.RESOURCE_STAGE_DURATION_SECONDS,
                duration_ms / 1000.0,
                labels=labels,
                label_names=("stage", "status"),
            )

            if stage == "wait" and duration_ms > 0:
                registry.observe_histogram(
                    self.RESOURCE_WAIT_DURATION_SECONDS,
                    duration_ms / 1000.0,
                    labels={"operation": operation},
                    label_names=("operation",),
                )
