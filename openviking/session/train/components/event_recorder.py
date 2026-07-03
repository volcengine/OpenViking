# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Streaming JSONL event recording for session train/eval pipelines."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openviking.session.train.components.dataset_service import jsonable
from openviking.session.train.components.reporter import NoopPipelineLifecycleHook


@dataclass(slots=True)
class JsonlEventRecorder:
    """Append train/eval events as one flushed JSON object per line."""

    path: Path
    default_fields: dict[str, Any] = field(default_factory=dict)
    _lock: asyncio.Lock = field(init=False, repr=False)
    _sequence: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.path = self.path.expanduser().resolve()
        self._lock = asyncio.Lock()

    async def record(self, event: str, **fields: Any) -> None:
        async with self._lock:
            self._sequence += 1
            payload = {
                "time": datetime.now(timezone.utc).isoformat(),
                "sequence": self._sequence,
                "event": event,
                **self.default_fields,
                **fields,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(jsonable(payload), ensure_ascii=False, sort_keys=True))
                file.write("\n")
                file.flush()


@dataclass(slots=True)
class CompositeEventRecorder:
    """Fan out event records to multiple recorder implementations."""

    recorders: tuple[Any, ...]

    async def record(self, event: str, **fields: Any) -> None:
        for recorder in self.recorders:
            record = getattr(recorder, "record", None)
            if record is None:
                continue
            result = record(event, **fields)
            if inspect.isawaitable(result):
                await result


@dataclass(slots=True)
class JsonlPipelineEventHook(NoopPipelineLifecycleHook):
    """Lifecycle hook that streams high-level pipeline reports to JSONL."""

    recorder: JsonlEventRecorder

    async def on_epoch_start(self, *, epoch: int, context: Any) -> None:
        await self.recorder.record(
            "epoch_start",
            stage="epoch_start",
            **_merge_fields(_context_fields(context), {"epoch": epoch}),
        )

    async def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        await self.recorder.record(
            "train_rollout",
            stage="train_rollout",
            **_merge_fields(_context_fields(context), _report_fields(report)),
        )

    async def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        await self.recorder.record(
            "train_result",
            stage="train",
            **_merge_fields(_context_fields(context), _report_fields(report)),
        )

    async def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        stage = str(report.get("rollout_stage") or label)
        await self.recorder.record(
            stage,
            stage=stage,
            **_merge_fields(_context_fields(context), _report_fields(report)),
        )

    async def on_run_summary(
        self,
        *,
        title: str,
        fields: dict[str, Any],
        baseline_eval: dict[str, Any] | None = None,
        final_eval: dict[str, Any] | None = None,
        accuracy_delta: float | None = None,
        output_path: str | None = None,
        rollouts_root: str | None = None,
        rollouts_index_path: str | None = None,
        latest_failed_rollout: str | None = None,
    ) -> None:
        await self.recorder.record(
            "run_summary",
            stage="run_summary",
            title=title,
            fields=dict(fields),
            baseline_eval=baseline_eval,
            final_eval=final_eval,
            accuracy_delta=accuracy_delta,
            output_path=output_path,
            rollouts_root=rollouts_root,
            rollouts_index_path=rollouts_index_path,
            latest_failed_rollout=latest_failed_rollout,
        )


def _merge_fields(*items: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for item in items:
        merged.update(item)
    return merged


def _context_fields(context: Any) -> dict[str, Any]:
    metadata = dict(getattr(context, "execution_metadata", {}) or {})
    fields: dict[str, Any] = {}
    for key in ("epoch", "training", "rollout_stage", "eval_split"):
        if key in metadata:
            fields[key] = metadata[key]
    return fields


def _report_fields(report: dict[str, Any]) -> dict[str, Any]:
    excluded_keys = {"commit_results"}
    fields = {key: value for key, value in report.items() if key not in excluded_keys}
    commit_results = report.get("commit_results")
    if isinstance(commit_results, list):
        fields["commit_trace_ids"] = _commit_field_values(commit_results, "trace_id")
        fields["commit_task_ids"] = _commit_field_values(commit_results, "task_id")
        fields["commit_telemetry_ids"] = _commit_field_values(commit_results, "telemetry_id")
    return fields


def _commit_field_values(commit_results: list[Any], key: str) -> list[str]:
    values: list[str] = []
    for item in commit_results:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "").strip()
        if value:
            values.append(value)
    return values
