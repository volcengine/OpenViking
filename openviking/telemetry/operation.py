# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Operation-scoped telemetry primitives."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional
from uuid import uuid4


@dataclass
class TelemetrySnapshot:
    """Final operation telemetry output."""

    telemetry_id: str
    summary: Dict[str, Any]

    def to_usage_dict(self) -> Dict[str, Any]:
        return {
            "duration_ms": self.summary.get("duration_ms", 0),
            "token_total": self.summary.get("tokens", {}).get("total", 0),
        }

    def to_dict(
        self,
        *,
        include_summary: bool = True,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"id": self.telemetry_id}
        if include_summary:
            payload["summary"] = self.summary
        return payload


class TelemetrySummaryBuilder:
    """Build normalized summary metrics from collector data."""

    @staticmethod
    def _i(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _has_metric_prefix(
        cls, prefix: str, counters: Dict[str, float], gauges: Dict[str, Any]
    ) -> bool:
        needle = f"{prefix}."
        return any(key.startswith(needle) for key in counters) or any(
            key.startswith(needle) for key in gauges
        )

    @classmethod
    def build(
        cls,
        *,
        operation: str,
        status: str,
        duration_ms: float,
        counters: Dict[str, float],
        gauges: Dict[str, Any],
        error_stage: str,
        error_code: str,
        error_message: str,
    ) -> Dict[str, Any]:
        llm_input_tokens = cls._i(counters.get("tokens.llm.input"), 0)
        llm_output_tokens = cls._i(counters.get("tokens.llm.output"), 0)
        llm_total_tokens = cls._i(counters.get("tokens.llm.total"), 0)
        embedding_total_tokens = cls._i(counters.get("tokens.embedding.total"), 0)
        vector_candidates_scored = cls._i(counters.get("vector.scored"), 0)
        vectors_scanned = gauges.get("vector.scanned")
        if vectors_scanned is None:
            vectors_scanned = cls._i(counters.get("vector.scanned"), 0)

        memories_extracted = gauges.get("memory.extracted")
        if memories_extracted is None and counters.get("memory.extracted") is not None:
            memories_extracted = cls._i(counters.get("memory.extracted"), 0)
        summary = {
            "operation": operation,
            "status": status,
            "duration_ms": round(float(duration_ms), 3),
            "tokens": {
                "total": cls._i(counters.get("tokens.total"), 0),
                "llm": {
                    "input": llm_input_tokens,
                    "output": llm_output_tokens,
                    "total": llm_total_tokens,
                },
                "embedding": {"total": embedding_total_tokens},
            },
        }

        if cls._has_metric_prefix("queue", counters, gauges):
            summary["queue"] = {
                "semantic": {
                    "processed": cls._i(gauges.get("queue.semantic.processed"), 0),
                    "error_count": cls._i(gauges.get("queue.semantic.error_count"), 0),
                },
                "embedding": {
                    "processed": cls._i(gauges.get("queue.embedding.processed"), 0),
                    "error_count": cls._i(gauges.get("queue.embedding.error_count"), 0),
                },
            }

        if cls._has_metric_prefix("vector", counters, gauges):
            summary["vector"] = {
                "searches": cls._i(counters.get("vector.searches"), 0),
                "scored": vector_candidates_scored,
                "passed": cls._i(counters.get("vector.passed"), 0),
                "returned": cls._i(
                    gauges.get("vector.returned", counters.get("vector.returned")), 0
                ),
                "scanned": vectors_scanned,
                "scan_reason": gauges.get("vector.scan_reason", ""),
            }

        if cls._has_metric_prefix("semantic_nodes", counters, gauges):
            summary["semantic_nodes"] = {
                "total": gauges.get("semantic_nodes.total"),
                "done": gauges.get("semantic_nodes.done"),
                "pending": gauges.get("semantic_nodes.pending"),
                "running": gauges.get("semantic_nodes.running"),
            }

        if cls._has_metric_prefix("memory", counters, gauges):
            summary["memory"] = {
                "extracted": memories_extracted,
            }

        if error_stage or error_code or error_message:
            summary["errors"] = {
                "stage": error_stage,
                "error_code": error_code,
                "message": error_message,
            }

        return summary


class OperationTelemetry:
    """Operation-scoped telemetry collector with low-overhead disabled mode."""

    def __init__(
        self,
        operation: str,
        enabled: bool = False,
    ):
        self.operation = operation
        self.enabled = enabled
        self.telemetry_id = f"tm_{uuid4().hex}" if enabled else ""
        self._start_time = time.perf_counter()
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, Any] = {}
        self._error_stage = ""
        self._error_code = ""
        self._error_message = ""
        self._lock = Lock()

    def count(self, key: str, delta: float = 1) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._counters[key] += delta

    def increment(self, key: str, delta: float = 1) -> None:
        self.count(key, delta)

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._gauges[key] = value

    def set_value(self, key: str, value: Any) -> None:
        self.set(key, value)

    def add_token_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.add_token_usage_by_source("llm", input_tokens, output_tokens)

    def record_token_usage(self, source: str, input_tokens: int, output_tokens: int = 0) -> None:
        self.add_token_usage_by_source(source, input_tokens, output_tokens)

    def add_token_usage_by_source(
        self, source: str, input_tokens: int, output_tokens: int = 0
    ) -> None:
        if not self.enabled:
            return

        normalized_input = max(input_tokens, 0)
        normalized_output = max(output_tokens, 0)
        normalized_total = normalized_input + normalized_output

        self.count("tokens.input", normalized_input)
        self.count("tokens.output", normalized_output)
        self.count("tokens.total", normalized_total)
        self.count(f"tokens.{source}.input", normalized_input)
        self.count(f"tokens.{source}.output", normalized_output)
        self.count(f"tokens.{source}.total", normalized_total)

    def set_error(self, stage: str, code: str, message: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._error_stage = stage
            self._error_code = code
            self._error_message = message

    def record_error(self, stage: str, code: str, message: str) -> None:
        self.set_error(stage, code, message)

    def finish(self, status: str = "ok") -> Optional[TelemetrySnapshot]:
        if not self.enabled:
            return None

        duration_ms = (time.perf_counter() - self._start_time) * 1000
        with self._lock:
            summary = TelemetrySummaryBuilder.build(
                operation=self.operation,
                status=status,
                duration_ms=duration_ms,
                counters=dict(self._counters),
                gauges=dict(self._gauges),
                error_stage=self._error_stage,
                error_code=self._error_code,
                error_message=self._error_message,
            )
        return TelemetrySnapshot(
            telemetry_id=self.telemetry_id,
            summary=summary,
        )


__all__ = [
    "OperationTelemetry",
    "TelemetrySnapshot",
    "TelemetrySummaryBuilder",
]
