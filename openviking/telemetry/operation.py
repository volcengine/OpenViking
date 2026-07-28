# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Operation-scoped telemetry primitives."""

from __future__ import annotations

import math
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Iterator, Optional
from uuid import uuid4

_CUVS_TIMING_FIELDS = (
    "total_ms",
    "preflight_ms",
    "queue_ms",
    "gpu_gate_queue_ms",
    "build_ms",
    "filter_prepare_ms",
    "gpu_search_ms",
    "batch_wait_ms",
    "native_search_ms",
)
_CUVS_ROUTE_BUCKETS = {
    "cuvs",
    "cuvs_error",
    "empty_filter",
    "native_fallback",
    "native_filter_threshold",
    "native_memory_budget",
    "native_rebuild_pending",
    "native_sparse_hybrid",
    "native_unsupported_filter",
    "unsupported_sparse_hybrid",
}
_CUVS_FILTER_BUCKETS = {"none", "scalar", "path"}
_CUVS_ALGORITHM_BUCKETS = {"brute_force", "cagra"}
_CUVS_DTYPE_BUCKETS = {"float32", "float16"}


@dataclass
class TelemetrySnapshot:
    """Final operation telemetry output."""

    telemetry_id: str
    summary: Dict[str, Any]

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
    _PRESERVED_ZERO_METRIC_PATHS = {
        ("vector", "cuvs", "memory", "estimated_peak_bytes_max"),
        ("vector", "cuvs", "memory", "free_bytes_min"),
        ("vector", "cuvs", "memory", "usable_bytes_min"),
    }

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
    _SEARCH_DURATION_KEYS = {
        "target_abstract": "search.target_abstract.duration_ms",
        "intent_analysis": "search.intent_analysis.duration_ms",
        "embed_query": "search.embed_query.duration_ms",
        "vector_retrieval": "search.vector_retrieval.duration_ms",
    }

    @staticmethod
    def _i(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        if not math.isfinite(normalized):
            return default
        return round(normalized, 3)

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
    def _prune_zero_metrics(cls, value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, dict):
            pruned: Dict[str, Any] = {}
            for key, child in value.items():
                pruned_child = cls._prune_zero_metrics(child, (*path, key))
                if pruned_child is cls._PRUNED:
                    continue
                pruned[key] = pruned_child
            return pruned if pruned else cls._PRUNED

        if isinstance(value, bool):
            return value

        if (
            isinstance(value, (int, float))
            and value == 0
            and path not in cls._PRESERVED_ZERO_METRIC_PATHS
        ):
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
    def _counter_breakdown(cls, prefix: str, counters: Dict[str, float]) -> Dict[str, int]:
        needle = f"{prefix}."
        return {
            key[len(needle) :]: cls._i(value, 0)
            for key, value in sorted(counters.items())
            if key.startswith(needle) and cls._i(value, 0) > 0
        }

    @classmethod
    def _cuvs_timing_summary(
        cls, counters: Dict[str, float], gauges: Dict[str, Any]
    ) -> Dict[str, Dict[str, float]]:
        result: Dict[str, Dict[str, float]] = {}
        for field in _CUVS_TIMING_FIELDS:
            public_name = field.removesuffix("_ms")
            total_us = cls._i(counters.get(f"vector.cuvs.timings.{field}.sum_us"), 0)
            maximum_us = cls._i(gauges.get(f"vector.cuvs.timings.{field}.max_us"), 0)
            total = cls._f(total_us / 1000.0, 0.0)
            maximum = cls._f(maximum_us / 1000.0, 0.0)
            if total or maximum:
                result[public_name] = {"sum": total, "max": maximum}
        return result

    @classmethod
    def _build_stage_token_summary(cls, counters: Dict[str, float]) -> Dict[str, Dict[str, Any]]:
        """Build a low-cardinality stage -> source -> token breakdown from counter keys."""
        summary: Dict[str, Dict[str, Any]] = {}
        prefix = "tokens.stages."
        for key, value in counters.items():
            if not key.startswith(prefix):
                continue
            parts = key.split(".")
            if len(parts) != 5:
                continue
            _, _, stage, source, token_field = parts
            normalized_value = cls._i(value, 0)
            if normalized_value <= 0:
                continue
            source_payload = summary.setdefault(stage, {}).setdefault(source, {})
            if source != "llm" and token_field != "total":
                continue
            source_payload[token_field] = normalized_value
        return summary

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
        llm_prompt_cached_tokens = cls._i(counters.get("tokens.llm.prompt_cached"), 0)
        llm_completion_reasoning_tokens = cls._i(counters.get("tokens.llm.completion_reasoning"), 0)
        embedding_total_tokens = cls._i(counters.get("tokens.embedding.total"), 0)
        rerank_total_tokens = cls._i(counters.get("tokens.rerank.total"), 0)
        stage_token_summary = cls._build_stage_token_summary(counters)
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
                    "prompt_cached": llm_prompt_cached_tokens,
                    "completion_reasoning": llm_completion_reasoning_tokens,
                },
                "embedding": {"total": embedding_total_tokens},
                "rerank": {"total": rerank_total_tokens},
            },
        }
        if stage_token_summary:
            summary["tokens"]["stages"] = stage_token_summary

        if cls._has_metric_prefix("queue", counters, gauges):
            summary["queue"] = {
                "semantic": {
                    "processed": cls._i(gauges.get("queue.semantic.processed"), 0),
                    "requeue_count": cls._i(gauges.get("queue.semantic.requeue_count"), 0),
                    "error_count": cls._i(gauges.get("queue.semantic.error_count"), 0),
                },
                "embedding": {
                    "processed": cls._i(gauges.get("queue.embedding.processed"), 0),
                    "requeue_count": cls._i(gauges.get("queue.embedding.requeue_count"), 0),
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
            if cls._has_metric_prefix("vector.cuvs", counters, gauges):
                cuvs_memory = {
                    public_name: cls._i(gauges[metric_key], 0)
                    for public_name, metric_key in (
                        (
                            "estimated_peak_bytes_max",
                            "vector.cuvs.memory_estimated_peak_bytes.max",
                        ),
                        ("free_bytes_min", "vector.cuvs.memory_free_bytes.min"),
                        ("usable_bytes_min", "vector.cuvs.memory_usable_bytes.min"),
                    )
                    if metric_key in gauges
                }
                summary["vector"]["cuvs"] = {
                    "searches": cls._i(counters.get("vector.cuvs.searches"), 0),
                    "algorithms": cls._counter_breakdown("vector.cuvs.algorithms", counters),
                    "dtypes": cls._counter_breakdown("vector.cuvs.dtypes", counters),
                    "max_concurrent_gpu_searches": cls._i(
                        gauges.get("vector.cuvs.max_concurrent_gpu_searches"), 1
                    ),
                    "auto_mode_searches": cls._i(counters.get("vector.cuvs.auto_mode_searches"), 0),
                    "routes": cls._counter_breakdown("vector.cuvs.routes", counters),
                    "filter_kinds": cls._counter_breakdown("vector.cuvs.filter_kinds", counters),
                    "filter_cache_hits": cls._i(counters.get("vector.cuvs.filter_cache_hits"), 0),
                    "filter_cache_eviction_fallbacks": cls._i(
                        counters.get("vector.cuvs.filter_cache_eviction_fallbacks"), 0
                    ),
                    "packed_filter_queries": cls._i(
                        counters.get("vector.cuvs.packed_filter_queries"), 0
                    ),
                    "native_filter_reuses": cls._i(
                        counters.get("vector.cuvs.native_filter_reuses"), 0
                    ),
                    "builds": cls._i(counters.get("vector.cuvs.builds"), 0),
                    "eligible_count_max": cls._i(gauges.get("vector.cuvs.eligible_count.max"), 0),
                    "records_generation_max": cls._i(
                        gauges.get("vector.cuvs.records_generation.max"), 0
                    ),
                    "index_size_max": cls._i(gauges.get("vector.cuvs.index_size.max"), 0),
                    "memory": cuvs_memory,
                    "timings_ms": cls._cuvs_timing_summary(counters, gauges),
                }
                micro_batching_searches = cls._i(
                    counters.get("vector.cuvs.micro_batching_searches"), 0
                )
                if micro_batching_searches:
                    summary["vector"]["cuvs"].update(
                        {
                            "micro_batching_searches": micro_batching_searches,
                            "micro_batched_searches": cls._i(
                                counters.get("vector.cuvs.micro_batched_searches"), 0
                            ),
                            "micro_batching_warm_fast_path_searches": cls._i(
                                counters.get("vector.cuvs.micro_batching_warm_fast_path_searches"),
                                0,
                            ),
                            "batch_size_max": cls._i(gauges.get("vector.cuvs.batch_size.max"), 1),
                            "searches_by_batch_size": cls._counter_breakdown(
                                "vector.cuvs.searches_by_batch_size", counters
                            ),
                        }
                    )

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

        if cls._has_metric_prefix("search", counters, gauges):
            search_summary = {
                public_key: {
                    "duration_ms": cls._f(gauges.get(metric_key), 0.0),
                }
                for public_key, metric_key in cls._SEARCH_DURATION_KEYS.items()
            }
            typed_queries_count = gauges.get("search.typed_queries_count")
            if typed_queries_count is not None:
                search_summary["typed_queries_count"] = cls._i(typed_queries_count, 0)
            summary["search"] = search_summary

        if error_stage or error_code or error_message:
            summary["errors"] = {
                "stage": error_stage,
                "error_code": error_code,
                "message": error_message,
            }

        for key in (
            "tokens",
            "queue",
            "vector",
            "semantic_nodes",
            "memory",
            "resource",
            "search",
            "errors",
        ):
            if key not in summary:
                continue
            pruned_value = cls._prune_zero_metrics(summary[key], (key,))
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

    @staticmethod
    def _metric_bucket(value: Any, allowed: set[str]) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in allowed else "other"

    @staticmethod
    def _duration_us(value: Any) -> int:
        """Normalize a duration to integer microseconds for deterministic addition."""

        try:
            duration_ms = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        if not math.isfinite(duration_ms) or duration_ms <= 0:
            return 0
        duration_us = duration_ms * 1000.0
        if not math.isfinite(duration_us):
            return 0
        return max(int(round(duration_us)), 0)

    def record_cuvs_search(self, metrics: Dict[str, Any]) -> None:
        """Aggregate one dense-search sample with order-independent operations."""

        if not self.enabled:
            return
        route = self._metric_bucket(metrics.get("route_reason"), _CUVS_ROUTE_BUCKETS)
        filter_kind = self._metric_bucket(metrics.get("filter_kind"), _CUVS_FILTER_BUCKETS)
        algorithm = self._metric_bucket(metrics.get("algorithm"), _CUVS_ALGORITHM_BUCKETS)
        dtype = self._metric_bucket(metrics.get("dtype"), _CUVS_DTYPE_BUCKETS)

        with self._lock:
            self._counters["vector.cuvs.searches"] += 1
            self._counters[f"vector.cuvs.routes.{route}"] += 1
            self._counters[f"vector.cuvs.filter_kinds.{filter_kind}"] += 1
            self._counters[f"vector.cuvs.algorithms.{algorithm}"] += 1
            self._counters[f"vector.cuvs.dtypes.{dtype}"] += 1
            if TelemetrySummaryBuilder._bool(metrics.get("auto_mode"), False):
                self._counters["vector.cuvs.auto_mode_searches"] += 1
            if TelemetrySummaryBuilder._bool(metrics.get("micro_batching_enabled"), False):
                batch_size = min(
                    max(TelemetrySummaryBuilder._i(metrics.get("batch_size"), 1), 1),
                    8,
                )
                self._counters["vector.cuvs.micro_batching_searches"] += 1
                self._counters[f"vector.cuvs.searches_by_batch_size.{batch_size}"] += 1
                if batch_size > 1:
                    self._counters["vector.cuvs.micro_batched_searches"] += 1
                self._gauges["vector.cuvs.batch_size.max"] = max(
                    TelemetrySummaryBuilder._i(self._gauges.get("vector.cuvs.batch_size.max"), 1),
                    batch_size,
                )
            if TelemetrySummaryBuilder._bool(metrics.get("micro_batching_warm_fast_path"), False):
                self._counters["vector.cuvs.micro_batching_warm_fast_path_searches"] += 1
            if TelemetrySummaryBuilder._bool(metrics.get("filter_cache_hit"), False):
                self._counters["vector.cuvs.filter_cache_hits"] += 1
            if TelemetrySummaryBuilder._bool(metrics.get("filter_cache_eviction_fallback"), False):
                self._counters["vector.cuvs.filter_cache_eviction_fallbacks"] += 1
            if TelemetrySummaryBuilder._bool(metrics.get("filter_words_packed"), False):
                self._counters["vector.cuvs.packed_filter_queries"] += 1
            if TelemetrySummaryBuilder._bool(metrics.get("native_filter_reused"), False):
                self._counters["vector.cuvs.native_filter_reuses"] += 1
            if TelemetrySummaryBuilder._bool(metrics.get("build_performed"), False):
                self._counters["vector.cuvs.builds"] += 1

            concurrency = max(
                TelemetrySummaryBuilder._i(metrics.get("max_concurrent_gpu_searches"), 1), 1
            )
            self._gauges["vector.cuvs.max_concurrent_gpu_searches"] = max(
                TelemetrySummaryBuilder._i(
                    self._gauges.get("vector.cuvs.max_concurrent_gpu_searches"), 1
                ),
                concurrency,
            )
            for field in ("eligible_count", "records_generation", "index_size"):
                raw_value = metrics.get(field)
                if raw_value is None:
                    continue
                value = max(TelemetrySummaryBuilder._i(raw_value, 0), 0)
                key = f"vector.cuvs.{field}.max"
                self._gauges[key] = max(TelemetrySummaryBuilder._i(self._gauges.get(key), 0), value)

            peak_raw = metrics.get("memory_estimated_peak_bytes")
            peak = TelemetrySummaryBuilder._i(peak_raw, -1)
            if peak_raw is not None and peak >= 0:
                key = "vector.cuvs.memory_estimated_peak_bytes.max"
                self._gauges[key] = max(TelemetrySummaryBuilder._i(self._gauges.get(key), 0), peak)
            for field in ("memory_free_bytes", "memory_usable_bytes"):
                raw_value = metrics.get(field)
                value = TelemetrySummaryBuilder._i(raw_value, -1)
                if raw_value is None or value < 0:
                    continue
                key = f"vector.cuvs.{field}.min"
                existing = self._gauges.get(key)
                self._gauges[key] = (
                    value if existing is None else min(TelemetrySummaryBuilder._i(existing), value)
                )

            for field in _CUVS_TIMING_FIELDS:
                value_us = self._duration_us(metrics.get(field))
                sum_key = f"vector.cuvs.timings.{field}.sum_us"
                max_key = f"vector.cuvs.timings.{field}.max_us"
                self._counters[sum_key] = (
                    TelemetrySummaryBuilder._i(self._counters.get(sum_key), 0) + value_us
                )
                self._gauges[max_key] = max(
                    TelemetrySummaryBuilder._i(self._gauges.get(max_key), 0), value_us
                )

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

    def add_token_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        stage: str | None = None,
        prompt_cached_tokens: int = 0,
        completion_reasoning_tokens: int = 0,
    ) -> None:
        """Record LLM token usage into aggregate and optional stage-specific counters."""
        self.add_token_usage_by_source(
            "llm",
            input_tokens,
            output_tokens,
            stage=stage,
            prompt_cached_tokens=prompt_cached_tokens,
            completion_reasoning_tokens=completion_reasoning_tokens,
        )

    def record_token_usage(
        self,
        source: str,
        input_tokens: int,
        output_tokens: int = 0,
        *,
        stage: str | None = None,
        prompt_cached_tokens: int = 0,
        completion_reasoning_tokens: int = 0,
    ) -> None:
        """Record source-scoped token usage into aggregate and optional stage-specific counters."""
        self.add_token_usage_by_source(
            source,
            input_tokens,
            output_tokens,
            stage=stage,
            prompt_cached_tokens=prompt_cached_tokens,
            completion_reasoning_tokens=completion_reasoning_tokens,
        )

    def add_token_usage_by_source(
        self,
        source: str,
        input_tokens: int,
        output_tokens: int = 0,
        *,
        stage: str | None = None,
        prompt_cached_tokens: int = 0,
        completion_reasoning_tokens: int = 0,
    ) -> None:
        """Record token usage for one source and optionally mirror it into a fixed stage bucket."""
        if not self.enabled:
            return

        normalized_input = max(input_tokens, 0)
        normalized_output = max(output_tokens, 0)
        normalized_total = normalized_input + normalized_output
        normalized_prompt_cached = max(prompt_cached_tokens, 0)
        normalized_completion_reasoning = max(completion_reasoning_tokens, 0)

        self.count("tokens.input", normalized_input)
        self.count("tokens.output", normalized_output)
        self.count("tokens.total", normalized_total)
        self.count(f"tokens.{source}.input", normalized_input)
        self.count(f"tokens.{source}.output", normalized_output)
        self.count(f"tokens.{source}.total", normalized_total)
        if source == "llm":
            self.count(f"tokens.{source}.prompt_cached", normalized_prompt_cached)
            self.count(
                f"tokens.{source}.completion_reasoning",
                normalized_completion_reasoning,
            )
        if stage is None:
            try:
                from .context import get_current_telemetry_stage

                stage = get_current_telemetry_stage()
            except Exception:
                stage = None
        if stage:
            normalized_stage = str(stage).strip()
            if normalized_stage:
                self.count(f"tokens.stages.{normalized_stage}.{source}.input", normalized_input)
                self.count(f"tokens.stages.{normalized_stage}.{source}.output", normalized_output)
                self.count(f"tokens.stages.{normalized_stage}.{source}.total", normalized_total)
                if source == "llm":
                    self.count(
                        f"tokens.stages.{normalized_stage}.{source}.prompt_cached",
                        normalized_prompt_cached,
                    )
                    self.count(
                        f"tokens.stages.{normalized_stage}.{source}.completion_reasoning",
                        normalized_completion_reasoning,
                    )

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
