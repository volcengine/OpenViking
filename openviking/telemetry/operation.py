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
    _AGENT_MEMORY_PHASE_KEYS = (
        "trajectory",
        "experience_single",
        "other",
    )
    _AGENT_MEMORY_PHASE_DURATION_KEYS = {
        "total_ms": "total.duration_ms",
        "lock_wait_ms": "lock_wait.duration_ms",
        "llm_ms": "llm.duration_ms",
        "memory_apply_ms": "memory_apply.duration_ms",
        "post_apply_ms": "post_apply.duration_ms",
        "skill_apply_ms": "skill_apply.duration_ms",
        "operation_exact_apply_window_wait_ms": ("operation_exact_apply_window_wait.duration_ms"),
        "operation_exact_apply_window_lock_wait_ms": (
            "operation_exact_apply_window_lock_wait.duration_ms"
        ),
        "operation_exact_apply_window_coalesced_memory_apply_ms": (
            "operation_exact_apply_window_coalesced_memory_apply.duration_ms"
        ),
        "operation_exact_apply_window_timeline_conflict_synthesis_ms": (
            "operation_exact_apply_window_timeline_conflict_synthesis.duration_ms"
        ),
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
    def _build_lock_acquire_summary(
        cls, counters: Dict[str, float], gauges: Dict[str, Any]
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        for mode in ("tree", "exact"):
            prefix = f"lock.acquire.{mode}"
            if not cls._has_metric_prefix(prefix, counters, gauges):
                continue
            mode_summary: Dict[str, Any] = {
                "attempts": cls._i(counters.get(f"{prefix}.attempts"), 0),
                "successes": cls._i(counters.get(f"{prefix}.successes"), 0),
                "failures": cls._i(counters.get(f"{prefix}.failures"), 0),
                "duration_ms": cls._f(gauges.get(f"{prefix}.duration_ms"), 0.0),
            }
            bucket_prefix = f"{prefix}.bucket."
            bucket_names = set()
            for key in list(counters) + list(gauges):
                if not key.startswith(bucket_prefix):
                    continue
                rest = key[len(bucket_prefix) :]
                bucket_name = rest.split(".", 1)[0]
                if bucket_name:
                    bucket_names.add(bucket_name)
            if bucket_names:
                mode_summary["buckets"] = {
                    bucket: {
                        "attempts": cls._i(counters.get(f"{bucket_prefix}{bucket}.attempts"), 0),
                        "successes": cls._i(counters.get(f"{bucket_prefix}{bucket}.successes"), 0),
                        "failures": cls._i(counters.get(f"{bucket_prefix}{bucket}.failures"), 0),
                        "duration_ms": cls._f(
                            gauges.get(f"{bucket_prefix}{bucket}.duration_ms"), 0.0
                        ),
                    }
                    for bucket in sorted(bucket_names)
                }
            summary[mode] = mode_summary
        return summary

    @classmethod
    def _build_counter_suffix_summary(
        cls,
        counters: Dict[str, float],
        prefix: str,
    ) -> Dict[str, int]:
        needle = f"{prefix}."
        summary: Dict[str, int] = {}
        for key, value in counters.items():
            if not key.startswith(needle):
                continue
            suffix = key[len(needle) :]
            if not suffix or "." in suffix:
                continue
            count = cls._i(value, 0)
            if count > 0:
                summary[suffix] = count
        return dict(sorted(summary.items()))

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

        if cls._has_metric_prefix("semantic_nodes", counters, gauges):
            summary["semantic_nodes"] = {
                "total": gauges.get("semantic_nodes.total"),
                "done": gauges.get("semantic_nodes.done"),
                "pending": gauges.get("semantic_nodes.pending"),
                "running": gauges.get("semantic_nodes.running"),
            }

        lock_acquire_summary = cls._build_lock_acquire_summary(counters, gauges)
        if lock_acquire_summary:
            summary["locks"] = {"acquire": lock_acquire_summary}

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
            if cls._has_metric_prefix("memory.agent", counters, gauges):
                phase_summary: Dict[str, Any] = {
                    "count": cls._i(counters.get("memory.agent.extract.phase.count"), 0),
                }
                for phase_key in cls._AGENT_MEMORY_PHASE_KEYS:
                    metric_prefix = f"memory.agent.extract.phase.{phase_key}"
                    if not cls._has_metric_prefix(metric_prefix, counters, gauges):
                        continue
                    phase_summary[phase_key] = {
                        "count": cls._i(counters.get(f"{metric_prefix}.count"), 0),
                        "lock_retries": cls._i(counters.get(f"{metric_prefix}.lock_retries"), 0),
                        "candidate_uri_count": cls._i(
                            counters.get(f"{metric_prefix}.candidate_uri_count"), 0
                        ),
                        "operation_target_uri_count": cls._i(
                            counters.get(f"{metric_prefix}.operation_target_uri_count"), 0
                        ),
                        "candidate_target_overlap_count": cls._i(
                            counters.get(f"{metric_prefix}.candidate_target_overlap_count"), 0
                        ),
                        "schema_exact_lock_path_count": cls._i(
                            counters.get(f"{metric_prefix}.schema_exact_lock_path_count"), 0
                        ),
                        "schema_tree_lock_path_count": cls._i(
                            counters.get(f"{metric_prefix}.schema_tree_lock_path_count"), 0
                        ),
                        "operation_exact_lock_path_count": cls._i(
                            counters.get(f"{metric_prefix}.operation_exact_lock_path_count"), 0
                        ),
                        "operation_exact_conflicts": cls._i(
                            counters.get(f"{metric_prefix}.operation_exact_conflicts"), 0
                        ),
                        "operation_exact_retries": cls._i(
                            counters.get(f"{metric_prefix}.operation_exact_retries"), 0
                        ),
                        "operation_exact_apply_window_entered": cls._i(
                            counters.get(f"{metric_prefix}.operation_exact_apply_window_entered"),
                            0,
                        ),
                        "operation_exact_apply_window_leader": cls._i(
                            counters.get(f"{metric_prefix}.operation_exact_apply_window_leader"),
                            0,
                        ),
                        "operation_exact_apply_window_follower": cls._i(
                            counters.get(f"{metric_prefix}.operation_exact_apply_window_follower"),
                            0,
                        ),
                        "operation_exact_apply_window_batch_items": cls._i(
                            counters.get(
                                f"{metric_prefix}.operation_exact_apply_window_batch_items"
                            ),
                            0,
                        ),
                        "operation_exact_apply_window_coalesce_eligible": cls._i(
                            counters.get(
                                f"{metric_prefix}.operation_exact_apply_window_coalesce_eligible"
                            ),
                            0,
                        ),
                        "operation_exact_apply_window_coalesced_groups": cls._i(
                            counters.get(
                                f"{metric_prefix}.operation_exact_apply_window_coalesced_groups"
                            ),
                            0,
                        ),
                        "operation_exact_apply_window_coalesced_items": cls._i(
                            counters.get(
                                f"{metric_prefix}.operation_exact_apply_window_coalesced_items"
                            ),
                            0,
                        ),
                        "operation_exact_apply_window_lock_path_count_total": cls._i(
                            counters.get(
                                f"{metric_prefix}.operation_exact_apply_window_lock_path_count_total"
                            ),
                            0,
                        ),
                        "operation_exact_apply_window_timeline_conflict_synthesis": cls._i(
                            counters.get(
                                f"{metric_prefix}.operation_exact_apply_window_timeline_conflict_synthesis"
                            ),
                            0,
                        ),
                        "operation_exact_apply_window_timeline_conflict_synthesis_failed": cls._i(
                            counters.get(
                                f"{metric_prefix}.operation_exact_apply_window_timeline_conflict_synthesis_failed"
                            ),
                            0,
                        ),
                        "operation_exact_stale_read_uri_count": cls._i(
                            counters.get(f"{metric_prefix}.operation_exact_stale_read_uri_count"),
                            0,
                        ),
                        "operation_exact_retry_attempt": cls._i(
                            gauges.get(f"{metric_prefix}.operation_exact_retry_attempt"), 0
                        ),
                        "operation_exact_conflict_sensitive_buckets": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_conflict_sensitive_bucket",
                            )
                        ),
                        "operation_exact_conflict_sensitive_reasons": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_conflict_sensitive_reason",
                            )
                        ),
                        "operation_exact_conflict_buckets": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_conflict_bucket",
                            )
                        ),
                        "operation_exact_conflict_reasons": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_conflict_reason",
                            )
                        ),
                        "operation_exact_retry_buckets": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_retry_bucket",
                            )
                        ),
                        "operation_exact_retry_reasons": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_retry_reason",
                            )
                        ),
                        "operation_exact_apply_window_batch_sizes": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_apply_window_batch_size",
                            )
                        ),
                        "operation_exact_apply_window_coalesce_skipped_reasons": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_apply_window_coalesce_skipped",
                            )
                        ),
                        "operation_exact_stale_base_states": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_stale_base_state",
                            )
                        ),
                        "operation_exact_stale_current_states": (
                            cls._build_counter_suffix_summary(
                                counters,
                                f"{metric_prefix}.operation_exact_stale_current_state",
                            )
                        ),
                        **{
                            public_key: cls._f(gauges.get(f"{metric_prefix}.{metric_key}"), 0.0)
                            for public_key, metric_key in (
                                cls._AGENT_MEMORY_PHASE_DURATION_KEYS.items()
                            )
                        },
                    }

                memory_summary["agent"] = {
                    "trajectories_created": cls._i(
                        gauges.get("memory.agent.trajectories.created"), 0
                    ),
                    "experience_per_trajectory": {
                        "max_concurrency": cls._i(
                            gauges.get("memory.agent.experience.per_trajectory.max_concurrency"),
                            0,
                        ),
                        "input_trajectories": cls._i(
                            gauges.get("memory.agent.experience.per_trajectory.input_trajectories"),
                            0,
                        ),
                    },
                    "phase": phase_summary,
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

        for key in (
            "tokens",
            "queue",
            "vector",
            "semantic_nodes",
            "locks",
            "memory",
            "resource",
            "errors",
        ):
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

    def add_token_usage(
        self, input_tokens: int, output_tokens: int, *, stage: str | None = None
    ) -> None:
        """Record LLM token usage into aggregate and optional stage-specific counters."""
        self.add_token_usage_by_source("llm", input_tokens, output_tokens, stage=stage)

    def record_token_usage(
        self,
        source: str,
        input_tokens: int,
        output_tokens: int = 0,
        *,
        stage: str | None = None,
    ) -> None:
        """Record source-scoped token usage into aggregate and optional stage-specific counters."""
        self.add_token_usage_by_source(source, input_tokens, output_tokens, stage=stage)

    def add_token_usage_by_source(
        self,
        source: str,
        input_tokens: int,
        output_tokens: int = 0,
        *,
        stage: str | None = None,
    ) -> None:
        """Record token usage for one source and optionally mirror it into a fixed stage bucket."""
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
