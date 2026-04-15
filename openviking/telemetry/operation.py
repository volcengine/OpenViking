# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Operation-scoped telemetry primitives."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Iterator, Optional
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

    _PRUNED = object()

    _MEMORY_EXTRACT_STAGE_KEYS = {
        "prepare_inputs_ms": "memory.extract.stage.prepare_inputs.duration_ms",
        "llm_extract_ms": "memory.extract.stage.llm_extract.duration_ms",
        "normalize_candidates_ms": "memory.extract.stage.normalize_candidates.duration_ms",
        "tool_skill_stats_ms": "memory.extract.stage.tool_skill_stats.duration_ms",
        "profile_create_ms": "memory.extract.stage.profile_create.duration_ms",
        "tool_skill_merge_ms": "memory.extract.stage.tool_skill_merge.duration_ms",
        "dedup_ms": "memory.extract.stage.dedup.duration_ms",
        "create_memory_ms": "memory.extract.stage.create_memory.duration_ms",
        "merge_existing_ms": "memory.extract.stage.merge_existing.duration_ms",
        "delete_existing_ms": "memory.extract.stage.delete_existing.duration_ms",
        "create_relations_ms": "memory.extract.stage.create_relations.duration_ms",
        "flush_semantic_ms": "memory.extract.stage.flush_semantic.duration_ms",
    }
    _RESOURCE_FLAG_KEYS = {
        "wait": "resource.flags.wait",
        "build_index": "resource.flags.build_index",
        "summarize": "resource.flags.summarize",
        "watch_enabled": "resource.flags.watch_enabled",
    }

    @staticmethod
    def _i(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return round(float(value), 3)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
        return default

    @classmethod
    def _prune_zero_metrics(cls, value: Any) -> Any:
        if isinstance(value, dict):
            pruned: Dict[str, Any] = {}
            for key, child in value.items():
                pruned_child = cls._prune_zero_metrics(child)
                if pruned_child is cls._PRUNED:
                    continue
                pruned[key] = pruned_child
            return pruned if pruned else cls._PRUNED

        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)) and value == 0:
            return cls._PRUNED

        return value

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
        embedding_duration_ms = round(float(counters.get("embedding.duration_ms", 0.0) or 0.0), 3)
        embedding_requests = cls._i(counters.get("embedding.requests"), 0)
        embedding_error_count = cls._i(counters.get("embedding.error_count"), 0)
        embedding_wall_duration_ms = gauges.get("embedding.wall_duration_ms")
        if embedding_wall_duration_ms is None:
            embedding_wall_duration_ms = embedding_duration_ms
        else:
            embedding_wall_duration_ms = round(float(embedding_wall_duration_ms or 0.0), 3)
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

        if embedding_total_tokens or embedding_duration_ms or embedding_requests or embedding_error_count:
            effective_embedding_ms = embedding_wall_duration_ms or embedding_duration_ms
            summary["embedding"] = {
                "duration_ms": embedding_duration_ms,
                "wall_duration_ms": effective_embedding_ms,
                "requests": embedding_requests,
                "error_count": embedding_error_count,
                "avg_duration_ms": round(embedding_duration_ms / embedding_requests, 3)
                if embedding_requests
                else 0.0,
                "share_of_total_pct": round((effective_embedding_ms / duration_ms) * 100.0, 3)
                if duration_ms > 0
                else 0.0,
            }

        if cls._has_metric_prefix("queue", counters, gauges):
            summary["queue"] = {
                "wait_duration_ms": round(float(gauges.get("queue.wait.duration_ms") or 0.0), 3),
                "semantic": {
                    "processed": cls._i(gauges.get("queue.semantic.processed"), 0),
                    "requeue_count": cls._i(gauges.get("queue.semantic.requeue_count"), 0),
                    "error_count": cls._i(gauges.get("queue.semantic.error_count"), 0),
                    "duration_ms": round(
                        float(gauges.get("queue.semantic.duration_ms") or 0.0), 3
                    ),
                    "wall_duration_ms": round(
                        float(gauges.get("queue.semantic.wall_duration_ms") or 0.0), 3
                    ),
                },
                "embedding": {
                    "processed": cls._i(gauges.get("queue.embedding.processed"), 0),
                    "requeue_count": cls._i(gauges.get("queue.embedding.requeue_count"), 0),
                    "error_count": cls._i(gauges.get("queue.embedding.error_count"), 0),
                    "duration_ms": round(
                        float(gauges.get("queue.embedding.duration_ms") or 0.0), 3
                    ),
                    "wall_duration_ms": round(
                        float(gauges.get("queue.embedding.wall_duration_ms") or 0.0), 3
                    ),
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
            memory_summary = {
                "extracted": memories_extracted,
            }
            if cls._has_metric_prefix("memory.extract", counters, gauges):
                memory_summary["extract"] = {
                    "duration_ms": cls._f(gauges.get("memory.extract.total.duration_ms"), 0.0),
                    "candidates": {
                        "total": cls._i(gauges.get("memory.extract.candidates.total"), 0),
                        "standard": cls._i(gauges.get("memory.extract.candidates.standard"), 0),
                        "tool_skill": cls._i(gauges.get("memory.extract.candidates.tool_skill"), 0),
                    },
                    "actions": {
                        "created": cls._i(gauges.get("memory.extract.created"), 0),
                        "merged": cls._i(gauges.get("memory.extract.merged"), 0),
                        "deleted": cls._i(gauges.get("memory.extract.deleted"), 0),
                        "skipped": cls._i(gauges.get("memory.extract.skipped"), 0),
                    },
                    "stages": {
                        public_key: cls._f(gauges.get(metric_key), 0.0)
                        for public_key, metric_key in cls._MEMORY_EXTRACT_STAGE_KEYS.items()
                    },
                }
            summary["memory"] = memory_summary

        if cls._has_metric_prefix("resource", counters, gauges):
            summary["resource"] = {
                "request": {
                    "duration_ms": cls._f(gauges.get("resource.request.duration_ms"), 0.0),
                },
                "process": {
                    "duration_ms": cls._f(gauges.get("resource.process.duration_ms"), 0.0),
                    "parse": {
                        "duration_ms": cls._f(gauges.get("resource.parse.duration_ms"), 0.0),
                        "warnings_count": cls._i(gauges.get("resource.parse.warnings_count"), 0),
                    },
                    "finalize": {
                        "duration_ms": cls._f(gauges.get("resource.finalize.duration_ms"), 0.0),
                    },
                    "summarize": {
                        "duration_ms": cls._f(gauges.get("resource.summarize.duration_ms"), 0.0),
                    },
                },
                "wait": {
                    "duration_ms": cls._f(gauges.get("resource.wait.duration_ms"), 0.0),
                },
                "watch": {
                    "duration_ms": cls._f(gauges.get("resource.watch.duration_ms"), 0.0),
                },
                "flags": {
                    public_key: cls._bool(gauges.get(metric_key), False)
                    for public_key, metric_key in cls._RESOURCE_FLAG_KEYS.items()
                },
            }

        if error_stage or error_code or error_message:
            summary["errors"] = {
                "stage": error_stage,
                "error_code": error_code,
                "message": error_message,
            }

        for key in ("tokens", "queue", "vector", "semantic_nodes", "memory", "resource", "errors"):
            if key not in summary:
                continue
            pruned_value = cls._prune_zero_metrics(summary[key])
            if pruned_value is cls._PRUNED:
                summary.pop(key, None)
            else:
                summary[key] = pruned_value

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
        self.telemetry_id = f"tm_{uuid4().hex}"
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

    def add_duration(self, key: str, duration_ms: float) -> None:
        if not self.enabled:
            return
        gauge_key = key if key.endswith(".duration_ms") else f"{key}.duration_ms"
        try:
            normalized_duration = max(float(duration_ms), 0.0)
        except (TypeError, ValueError):
            normalized_duration = 0.0
        with self._lock:
            existing = self._gauges.get(gauge_key, 0.0)
            try:
                existing_value = float(existing)
            except (TypeError, ValueError):
                existing_value = 0.0
            self._gauges[gauge_key] = existing_value + normalized_duration

    @contextmanager
    def measure(self, key: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return

        start = time.perf_counter()
        try:
            yield
        finally:
            self.add_duration(key, (time.perf_counter() - start) * 1000)

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
