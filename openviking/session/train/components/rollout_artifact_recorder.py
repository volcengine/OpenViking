# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rollout artifact recording for batch train/eval pipelines."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openviking.message import ToolPart
from openviking.session.train.components.dataset_service import (
    case_to_dict,
    evaluation_to_dict,
    jsonable,
)
from openviking.session.train.components.reporter import NoopPipelineLifecycleHook
from openviking.session.train.domain import (
    PipelineEpochResult,
    PipelineEvaluationResult,
    Rollout,
    RolloutAnalysis,
)


@dataclass(slots=True)
class RolloutArtifactIndex:
    """Serializable index of recorded rollout artifacts."""

    run_dir: str
    rollouts_root: str
    case_groups: list[dict[str, Any]] = field(default_factory=list)
    latest_failed_rollout: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_dir": self.run_dir,
            "rollouts_root": self.rollouts_root,
            "latest_failed_rollout": self.latest_failed_rollout,
            "case_groups": self.case_groups,
        }


class RolloutArtifactRecorder(NoopPipelineLifecycleHook):
    """Write per-case/per-rollout artifacts for all case groups.

    Each case group and all its rollouts are written to disk so success/failure
    trials can be compared by an LLM or inspected manually.

    Inherits from NoopPipelineLifecycleHook so it can be registered as a
    pipeline lifecycle hook; only on_epoch_end and on_eval_end are overridden.
    """

    def __init__(
        self,
        *,
        run_dir: Path,
        client: Any | None = None,
        latest_pointer_path: Path | None = None,
    ) -> None:
        self.run_dir = run_dir.expanduser().resolve()
        self.rollouts_root = self.run_dir / "rollouts"
        self.client = client
        self.latest_pointer_path = (
            latest_pointer_path.expanduser().resolve() if latest_pointer_path else None
        )
        self._case_groups: dict[str, dict[str, Any]] = {}
        self._latest_failed_rollout: Path | None = None

    def record_rollout_completion(
        self,
        *,
        rollout: Rollout,
        index: int,
        context: Any,
    ) -> None:
        metadata = dict(getattr(context, "metadata", {}) or {})
        training = bool(metadata.get("training"))
        epoch = int(metadata.get("epoch", 0) or 0)
        stage = _stage_from_execution_metadata(metadata)
        commit_index = index if training else None
        records = [
            _RolloutRecord(
                rollout=rollout,
                evaluation=_rollout_evaluation_or_default(rollout),
                stage=stage,
                epoch=epoch,
                commit_index=commit_index,
                artifact_state="rollout_done" if training else "complete",
            )
        ]
        for group_id, group_records in self._group_records(records).items():
            self._write_group(group_id, group_records)
        self._write_index()

    def record_eval(
        self,
        *,
        label: str,
        epoch: int,
        analyses: list[RolloutAnalysis],
    ) -> None:
        grouped = self._group_records(
            [
                _RolloutRecord(
                    rollout=rollout,
                    evaluation=analysis.evaluation,
                    stage=_stage_dir(label, epoch=epoch),
                    epoch=epoch,
                )
                for analysis in analyses
                if isinstance((rollout := analysis.metadata.get("rollout")), Rollout)
            ]
        )
        for group_id, records in grouped.items():
            self._write_group(group_id, records)

    def on_train_rollout_end(
        self,
        *,
        epoch: int,
        rollouts: list[Any],
        snapshot_id: str,
        policy_set: Any,
        context: Any,
    ) -> None:
        del snapshot_id, policy_set, context
        self.record_train_rollouts(epoch=epoch, rollouts=list(rollouts))
        self._write_index()

    def record_train_rollouts(
        self,
        *,
        epoch: int,
        rollouts: list[Rollout],
    ) -> None:
        records = [
            _RolloutRecord(
                rollout=rollout,
                evaluation=_rollout_evaluation_or_default(rollout),
                stage=_stage_dir("train_rollout", epoch=epoch),
                epoch=epoch,
                commit_index=idx,
                artifact_state="rollout_done",
            )
            for idx, rollout in enumerate(rollouts)
        ]
        grouped = self._group_records(records)
        for group_id, group_records in grouped.items():
            self._write_group(group_id, group_records)

    async def record_train_epoch(
        self,
        *,
        epoch: int,
        analyses: list[RolloutAnalysis],
        commit_results: list[dict[str, Any]],
    ) -> None:
        commit_by_index = {
            int(item["index"]): item
            for item in commit_results
            if isinstance(item, dict) and item.get("index") is not None
        }
        records: list[_RolloutRecord] = []
        for idx, analysis in enumerate(analyses):
            rollout = analysis.metadata.get("rollout")
            if not isinstance(rollout, Rollout):
                continue
            commit_result = commit_by_index.get(idx)
            records.append(
                _RolloutRecord(
                    rollout=rollout,
                    evaluation=analysis.evaluation,
                    stage=_stage_dir("train_rollout", epoch=epoch),
                    epoch=epoch,
                    commit_result=commit_result,
                    commit_index=idx,
                    artifact_state=_artifact_state_from_commit_result(commit_result),
                )
            )
        grouped = self._group_records(records)
        for group_id, group_records in grouped.items():
            self._rewrite_commit_artifact_group(group_id, group_records)
            await self._write_train_commit_artifacts(group_records)

    def record_train_commit_result(self, event: str, **fields: Any) -> None:
        if event not in {"train_commit_submitted", "train_commit_done", "train_commit_failed"}:
            return
        train_dir = self._train_rollout_dir_from_event_fields(fields)
        commit_dir = self._commit_rollout_dir_from_event_fields(fields)
        if train_dir is None or commit_dir is None:
            return
        commit_result = _commit_result_from_event(event, fields)
        _write_json(commit_dir / "commit_result.json", commit_result)
        status_path = train_dir / "status.json"
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                status = {}
            status.update(
                {
                    "artifact_state": _artifact_state_from_commit_event(event),
                    "commit_error": commit_result.get("error"),
                    "commit_task_status": commit_result.get("task_status"),
                    "archive_uri": commit_result.get("archive_uri"),
                    "commit_path": str(commit_dir),
                    "commit_result_path": str(commit_dir / "commit_result.json"),
                }
            )
            _write_json(status_path, status)

        if commit_result.get("error"):
            self._latest_failed_rollout = train_dir
        self._update_rollout_index_entry(
            path=str(train_dir),
            updates={
                "artifact_state": _artifact_state_from_commit_event(event),
                "commit_error": commit_result.get("error"),
                "archive_uri": commit_result.get("archive_uri"),
                "commit_task_status": commit_result.get("task_status"),
                "commit_path": str(commit_dir),
                "commit_result_path": str(commit_dir / "commit_result.json"),
            },
        )
        self._write_index()

    async def on_epoch_end(
        self,
        *,
        epoch_result: PipelineEpochResult,
        policy_set: Any,
        context: Any,
    ) -> None:
        """Lifecycle hook: write rollout artifacts immediately after each training epoch.

        This ensures rollouts are persisted incrementally instead of waiting
        for the full pipeline to finish, which is important for long runs and
        crash recovery.
        """
        commit_results = list(
            epoch_result.apply_result.metadata.get("commit_results", []) or []
        )
        await self.record_train_epoch(
            epoch=epoch_result.epoch,
            analyses=epoch_result.analyses,
            commit_results=commit_results,
        )
        # Also update the index file incrementally so it stays current.
        self._write_index()

    def on_eval_end(
        self,
        *,
        evaluation_result: PipelineEvaluationResult,
        policy_set: Any,
        context: Any,
    ) -> None:
        """Lifecycle hook: write eval rollout artifacts immediately after each eval pass."""
        label = str(
            evaluation_result.metadata.get("rollout_stage") or "test_rollout"
        )
        self.record_eval(
            label=label,
            epoch=evaluation_result.epoch,
            analyses=evaluation_result.analyses,
        )
        self._write_index()

    def finalize(self) -> RolloutArtifactIndex:
        return self._write_index()

    def _write_index(self) -> RolloutArtifactIndex:
        """Write rollouts_index.json with current state (incremental update)."""
        case_groups = sorted(self._case_groups.values(), key=lambda item: item["case_group_id"])
        index = RolloutArtifactIndex(
            run_dir=str(self.run_dir),
            rollouts_root=str(self.rollouts_root),
            case_groups=case_groups,
            latest_failed_rollout=str(self._latest_failed_rollout) if self._latest_failed_rollout else None,
        )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        index_path = self.run_dir / "rollouts_index.json"
        index_path.write_text(
            json.dumps(index.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if case_groups:
            self.rollouts_root.mkdir(parents=True, exist_ok=True)
        if self.latest_pointer_path is not None:
            self.latest_pointer_path.parent.mkdir(parents=True, exist_ok=True)
            self.latest_pointer_path.write_text(str(self.rollouts_root) + "\n", encoding="utf-8")
        return index

    def _group_records(self, records: list["_RolloutRecord"]) -> dict[str, list["_RolloutRecord"]]:
        grouped: dict[str, list[_RolloutRecord]] = {}
        for record in records:
            grouped.setdefault(_case_group_id(record.rollout), []).append(record)
        return grouped

    def _write_group(self, group_id: str, records: list["_RolloutRecord"]) -> None:
        if not records:
            return
        group_dir = self.rollouts_root / group_id
        group_dir.mkdir(parents=True, exist_ok=True)
        case = records[0].rollout.case
        _write_json(group_dir / "case.json", case_to_dict(case))

        group_entry = self._case_groups.setdefault(
            group_id,
            {
                "case_group_id": group_id,
                "path": str(group_dir),
                "case_name": _original_case_name(records[0].rollout),
                "task_id": _task_id(records[0].rollout),
                "task_no": _task_no(records[0].rollout),
                "split": _split(records[0].rollout),
                "rollouts": [],
            },
        )

        seen_paths = {item["path"] for item in group_entry["rollouts"]}
        for record in records:
            rollout_dir = group_dir / record.stage / _rollout_dir_name(record)
            rollout_dir.mkdir(parents=True, exist_ok=True)
            self._write_rollout_artifacts(rollout_dir, record)
            rollout_index = _rollout_index(record, rollout_dir)
            if rollout_index["path"] not in seen_paths:
                group_entry["rollouts"].append(rollout_index)
                seen_paths.add(rollout_index["path"])
            if not record.passed or _commit_failed(record.commit_result):
                self._latest_failed_rollout = rollout_dir
        self._write_group_readme(group_dir, group_entry)

    def _write_rollout_artifacts(self, rollout_dir: Path, record: "_RolloutRecord") -> None:
        rollout = record.rollout
        _write_json(rollout_dir / "status.json", _status_payload(record))
        _write_json(rollout_dir / "rollout.json", _rollout_payload(record))
        _write_json(rollout_dir / "messages.json", [message.to_dict() for message in rollout.messages])
        _write_json(rollout_dir / "tool_calls.json", _tool_calls(rollout))
        _write_json(rollout_dir / "evaluation.json", evaluation_to_dict(record.evaluation))
        (rollout_dir / "memory_context.md").write_text(_memory_context(rollout), encoding="utf-8")
        task_case_skill = _task_case_experience_skill(rollout)
        if task_case_skill:
            (rollout_dir / "task_case_experience_skill.md").write_text(
                task_case_skill,
                encoding="utf-8",
            )
        (rollout_dir / "prompt_for_llm.md").write_text(_prompt_for_llm(record), encoding="utf-8")
        # Full commit messages (as sent to session.commit)
        commit_msgs = _build_commit_messages(rollout)
        _write_json(rollout_dir / "commit_messages.json", commit_msgs)
        (rollout_dir / "commit_messages.md").write_text(
            _format_commit_messages_markdown(commit_msgs), encoding="utf-8"
        )

    async def _write_train_commit_artifacts(self, records: list["_RolloutRecord"]) -> None:
        if self.client is None:
            return
        for record in records:
            if record.commit_result is None:
                continue
            archive_uri = str(record.commit_result.get("archive_uri") or "").strip()
            if not archive_uri:
                continue
            train_dir = self._train_rollout_dir(record)
            commit_dir = self._commit_rollout_dir(record)
            try:
                memory_diff = await self.client.read(f"{archive_uri}/memory_diff.json")
            except Exception as exc:  # best-effort artifact enrichment
                _write_json(
                    commit_dir / "memory_diff_error.json",
                    {"archive_uri": archive_uri, "error": str(exc)},
                )
                self._update_rollout_status(
                    train_dir,
                    memory_diff_error=str(exc),
                    commit_path=str(commit_dir),
                )
                self._update_rollout_index_entry(
                    path=str(train_dir),
                    updates={"memory_diff_error": str(exc), "commit_path": str(commit_dir)},
                )
                self._write_index()
                continue
            (commit_dir / "memory_diff.json").write_text(str(memory_diff), encoding="utf-8")
            (commit_dir / "memory_diff.md").write_text(
                _format_memory_diff_markdown(_parse_memory_diff(memory_diff)),
                encoding="utf-8",
            )
            self._update_rollout_status(
                train_dir,
                artifact_state="memory_diff_done",
                memory_diff_path=str(commit_dir / "memory_diff.json"),
                memory_diff_markdown_path=str(commit_dir / "memory_diff.md"),
                commit_path=str(commit_dir),
            )
            self._update_rollout_index_entry(
                path=str(train_dir),
                updates={
                    "artifact_state": "memory_diff_done",
                    "memory_diff_path": str(commit_dir / "memory_diff.json"),
                    "memory_diff_markdown_path": str(commit_dir / "memory_diff.md"),
                    "commit_path": str(commit_dir),
                },
            )
            self._write_index()

    def _update_rollout_status(self, rollout_dir: Path, **updates: Any) -> None:
        status_path = rollout_dir / "status.json"
        if not status_path.exists():
            return
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {}
        status.update(updates)
        _write_json(status_path, status)

    def _rewrite_commit_artifact_group(
        self,
        group_id: str,
        records: list["_RolloutRecord"],
    ) -> None:
        group_entry = self._case_groups.get(group_id)
        if group_entry is None:
            self._write_group(group_id, records)
            group_entry = self._case_groups.get(group_id)
            if group_entry is None:
                return
        for record in records:
            train_dir = self._train_rollout_dir(record)
            commit_dir = self._commit_rollout_dir(record)
            if record.commit_result is not None:
                _write_json(commit_dir / "commit_result.json", record.commit_result)
            updates = {
                "artifact_state": record.artifact_state,
                "commit_error": (
                    record.commit_result.get("error") if record.commit_result else None
                ),
                "archive_uri": (
                    record.commit_result.get("archive_uri") if record.commit_result else None
                ),
                "commit_task_status": (
                    record.commit_result.get("task_status") if record.commit_result else None
                ),
                "commit_path": str(commit_dir),
                "commit_result_path": str(commit_dir / "commit_result.json")
                if record.commit_result is not None
                else None,
            }
            self._update_rollout_status(train_dir, **updates)
            self._update_rollout_index_entry(path=str(train_dir), updates=updates)
            if not record.passed or _commit_failed(record.commit_result):
                self._latest_failed_rollout = train_dir
        self._write_group_readme(self.rollouts_root / group_id, group_entry)

    def _update_rollout_index_entry(self, *, path: str, updates: dict[str, Any]) -> None:
        for group_entry in self._case_groups.values():
            for item in group_entry.get("rollouts", []):
                if item.get("path") == path:
                    item.update(updates)
                    return

    def _train_rollout_dir(self, record: "_RolloutRecord") -> Path:
        return (
            self.rollouts_root
            / _case_group_id(record.rollout)
            / f"epoch_{record.epoch}"
            / _stage_leaf("train_rollout")
            / _rollout_dir_name(record)
        )

    def _commit_rollout_dir(self, record: "_RolloutRecord") -> Path:
        return (
            self.rollouts_root
            / _case_group_id(record.rollout)
            / f"epoch_{record.epoch}"
            / _stage_leaf("train")
            / _rollout_dir_name(record)
        )

    def _train_rollout_dir_from_event_fields(self, fields: dict[str, Any]) -> Path | None:
        return self._rollout_dir_from_event_fields(fields, phase=_stage_leaf("train_rollout"))

    def _commit_rollout_dir_from_event_fields(self, fields: dict[str, Any]) -> Path | None:
        return self._rollout_dir_from_event_fields(fields, phase=_stage_leaf("train"))

    def _rollout_dir_from_event_fields(self, fields: dict[str, Any], *, phase: str) -> Path | None:
        split = fields.get("split")
        task_no = fields.get("task_no")
        task_id = fields.get("case_task_id") or fields.get("case_name")
        epoch = fields.get("epoch")
        index = fields.get("index")
        if split is None or task_no is None or task_id is None or epoch is None or index is None:
            return None
        group_id = (
            f"{_safe_fragment(split)}_task_"
            f"{_safe_fragment(str(task_no))}_"
            f"{_safe_fragment(task_id)}"
        )[:120]
        return self.rollouts_root / group_id / f"epoch_{epoch}" / phase / f"trial_{index}"

    def _write_group_readme(self, group_dir: Path, group_entry: dict[str, Any]) -> None:
        failed = [item for item in group_entry["rollouts"] if not item.get("passed") or item.get("commit_error")]
        lines = [
            f"# Rollout artifact group: {group_entry['case_group_id']}",
            "",
            f"- split: {group_entry.get('split')}",
            f"- task_no: {group_entry.get('task_no')}",
            f"- task_id: {group_entry.get('task_id')}",
            f"- rollouts: {len(group_entry['rollouts'])}",
            f"- failed_rollouts: {len(failed)}",
            "",
            "## Rollouts",
        ]
        for item in group_entry["rollouts"]:
            status = "FAIL" if (not item.get("passed") or item.get("commit_error")) else "PASS"
            lines.append(
                f"- [{status}] {item.get('stage')} {item.get('rollout_name')} "
                f"score={item.get('score')} path={item.get('path')}"
            )
        lines.extend(
            [
                "",
                "## Suggested LLM prompt",
                "",
                "Read this directory recursively. Compare successful and failed rollouts for the same task. ",
                "Focus on whether the injected memory_context.md was missing, misleading, ignored, or helpful.",
            ]
        )
        (group_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass(slots=True)
class _RolloutRecord:
    rollout: Rollout
    evaluation: Any
    stage: str
    epoch: int
    commit_result: dict[str, Any] | None = None
    commit_index: int | None = None
    artifact_state: str = "complete"

    @property
    def passed(self) -> bool:
        return bool(getattr(self.evaluation, "passed", False))

    @property
    def score(self) -> float:
        return float(getattr(self.evaluation, "score", 0.0) or 0.0)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(slots=True)
class RolloutArtifactEventRecorder:
    """Event recorder adapter that enriches rollout artifacts from commit events."""

    recorder: RolloutArtifactRecorder

    def record(self, event: str, **fields: Any) -> None:
        self.recorder.record_train_commit_result(event, **fields)


def _rollout_evaluation_or_default(rollout: Rollout) -> Any:
    if rollout.evaluation is not None:
        return rollout.evaluation
    from openviking.session.train.components.session_commit import (
        _rollout_evaluation_or_default as default_evaluation,
    )

    return default_evaluation(rollout)


def _commit_result_from_event(event: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": fields.get("index"),
        "session_id": fields.get("session_id"),
        "stage": fields.get("stage"),
        "task_id": fields.get("task_id"),
        "archive_uri": fields.get("archive_uri"),
        "trace_id": fields.get("trace_id"),
        "telemetry_id": fields.get("telemetry_id"),
        "task_status": fields.get("task_status"),
        "score": fields.get("score"),
        "error": fields.get("error"),
        "event": event,
        "artifact_state": _artifact_state_from_commit_event(event),
    }


def _artifact_state_from_commit_event(event: str) -> str:
    if event == "train_commit_submitted":
        return "commit_submitted"
    if event == "train_commit_done":
        return "commit_done"
    if event == "train_commit_failed":
        return "commit_failed"
    return "rollout_done"


def _artifact_state_from_commit_result(commit_result: dict[str, Any] | None) -> str:
    if not commit_result:
        return "rollout_done"
    if commit_result.get("error"):
        return "commit_failed"
    return "commit_done"


def _stage_from_execution_metadata(metadata: dict[str, Any]) -> str:
    stage = str(metadata.get("rollout_stage") or metadata.get("stage") or "")
    epoch = int(metadata.get("epoch", 0) or 0)
    if not stage:
        stage = "train_rollout" if bool(metadata.get("training")) else "test_rollout"
    return _stage_dir(stage.split(maxsplit=1)[0], epoch=epoch)


def _status_payload(record: _RolloutRecord) -> dict[str, Any]:
    rollout = record.rollout
    return {
        "stage": record.stage,
        "epoch": record.epoch,
        "rollout_name": _rollout_name(record),
        "case_group_id": _case_group_id(rollout),
        "case_name": rollout.case.name,
        "original_case_name": _original_case_name(rollout),
        "split": _split(rollout),
        "task_no": _task_no(rollout),
        "task_id": _task_id(rollout),
        "trial": _trial(rollout),
        "passed": record.passed,
        "score": record.score,
        "policy_snapshot_id": rollout.policy_snapshot_id,
        "has_memory_context": bool(_memory_context(rollout).strip()),
        "has_task_case_experience_skill": bool(_task_case_experience_skill(rollout).strip()),
        "task_case_experience_skill_path": "task_case_experience_skill.md"
        if _task_case_experience_skill(rollout).strip()
        else None,
        "artifact_state": record.artifact_state,
        "commit_error": record.commit_result.get("error") if record.commit_result else None,
        "commit_task_status": (
            record.commit_result.get("task_status") if record.commit_result else None
        ),
        "archive_uri": record.commit_result.get("archive_uri") if record.commit_result else None,
    }


def _rollout_payload(record: _RolloutRecord) -> dict[str, Any]:
    rollout = record.rollout
    return {
        "case": case_to_dict(rollout.case),
        "policy_snapshot_id": rollout.policy_snapshot_id,
        "metadata": jsonable(rollout.metadata),
        "evaluation": evaluation_to_dict(record.evaluation),
    }


def _rollout_index(record: _RolloutRecord, rollout_dir: Path) -> dict[str, Any]:
    return {
        "rollout_name": _rollout_name(record),
        "stage": record.stage,
        "epoch": record.epoch,
        "trial": _trial(record.rollout),
        "passed": record.passed,
        "score": record.score,
        "artifact_state": record.artifact_state,
        "path": str(rollout_dir),
        "commit_error": record.commit_result.get("error") if record.commit_result else None,
        "archive_uri": record.commit_result.get("archive_uri") if record.commit_result else None,
        "commit_task_status": (
            record.commit_result.get("task_status") if record.commit_result else None
        ),
    }


def _tool_calls(rollout: Rollout) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message_index, message in enumerate(rollout.messages):
        for part in message.parts:
            if isinstance(part, ToolPart):
                calls.append(
                    {
                        "message_index": message_index,
                        "message_id": message.id,
                        "role": message.role,
                        "tool_id": part.tool_id,
                        "tool_name": part.tool_name,
                        "tool_status": part.tool_status,
                        "tool_input": jsonable(part.tool_input),
                        "tool_output": part.tool_output,
                    }
                )
    return calls


def _prompt_for_llm(record: _RolloutRecord) -> str:
    status = _status_payload(record)
    return "\n".join(
        [
            "# Analyze this rollout",
            "",
            "Read all files in this directory, especially:",
            "- memory_context.md: memory injected into the agent prompt at rollout time",
            "- messages.json and tool_calls.json: trajectory",
            "- evaluation.json: failure signal",
            "- memory_diff.json: training memory update result when present",
            "",
            "## Status",
            "",
            "```json",
            json.dumps(jsonable(status), ensure_ascii=False, indent=2),
            "```",
            "",
            "Please identify whether the failure is caused by missing memory, "
            "wrong memory, ignored memory, bad tool use, or task ambiguity.",
        ]
    ) + "\n"


def _memory_context(rollout: Rollout) -> str:
    metadata = rollout.metadata or {}
    value = metadata.get("memory")
    if value is None:
        return ""
    return str(value)


def _task_case_experience_skill(rollout: Rollout) -> str:
    metadata = rollout.metadata or {}
    value = metadata.get("task_case_experience_skill")
    if value is None:
        return ""
    return str(value)


def _case_group_id(rollout: Rollout) -> str:
    split = _safe_fragment(_split(rollout) or "split")
    task_no = _safe_fragment(
        str(_task_no(rollout) if _task_no(rollout) is not None else "x")
    )
    task_id = _safe_fragment(
        str(_task_id(rollout) or _original_case_name(rollout) or rollout.case.name)
    )
    return f"{split}_task_{task_no}_{task_id}"[:120]


def _rollout_dir_name(record: _RolloutRecord) -> str:
    return _safe_fragment(_rollout_name(record))


def _rollout_name(record: _RolloutRecord) -> str:
    trial = _trial(record.rollout)
    if trial is not None:
        return f"trial_{trial}"
    if record.commit_index is not None:
        return f"trial_{record.commit_index}"
    return _safe_fragment(record.rollout.case.name)


def _stage_dir(label: str, *, epoch: int | None = None) -> str:
    stage = _stage_leaf(label)
    return stage if epoch is None else f"epoch_{epoch}/{stage}"


def _stage_leaf(label: str) -> str:
    stage = _safe_fragment(label or "rollout")
    order = {
        "train_rollout": 1,
        "train": 2,
        "eval_train_rollout": 3,
        "test_rollout": 4,
        "baseline_test_rollout": 0,
        "final_test_rollout": 5,
    }.get(stage)
    return f"{order}.{stage}" if order is not None else stage


def _original_case_name(rollout: Rollout) -> str:
    return str(
        rollout.case.input.get("original_case_name")
        or rollout.case.metadata.get("original_case_name")
        or rollout.metadata.get("original_case_name")
        or rollout.case.name
    )


def _split(rollout: Rollout) -> str | None:
    value = (
        rollout.case.input.get("data_split")
        or rollout.metadata.get("data_split")
        or rollout.case.input.get("split")
        or rollout.metadata.get("split")
    )
    return str(value) if value is not None else None


def _task_no(rollout: Rollout) -> Any:
    return rollout.case.input.get("task_no", rollout.metadata.get("task_no"))


def _task_id(rollout: Rollout) -> Any:
    return rollout.case.input.get("task_id", rollout.metadata.get("task_id"))


def _trial(rollout: Rollout) -> Any:
    for key in ("eval_trial", "train_trial"):
        if key in rollout.case.input:
            return rollout.case.input.get(key)
        if key in rollout.case.metadata:
            return rollout.case.metadata.get(key)
        if key in rollout.metadata:
            return rollout.metadata.get(key)
    return None


def _commit_failed(commit_result: dict[str, Any] | None) -> bool:
    return bool(commit_result and commit_result.get("error"))


def _safe_fragment(value: Any) -> str:
    text = str(value)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return text or "unknown"


def _build_commit_messages(rollout: Rollout) -> list[dict[str, Any]]:
    """Build the full message list as sent to session.commit.

    Matches the message assembly in session_commit._commit_one:
      [case_spec] + rollout.messages + [evaluation]
    """
    from openviking.session.train.components.session_commit import (
        _case_spec_message_to_request,
        _evaluation_message_to_request,
        _message_to_request,
    )

    messages: list[dict[str, Any]] = [_case_spec_message_to_request(rollout)]
    for msg in rollout.messages:
        messages.append(_message_to_request(msg))
    messages.append(_evaluation_message_to_request(rollout))
    return messages


def _format_commit_messages_markdown(messages: list[dict[str, Any]]) -> str:
    """Format commit messages as a readable Markdown document."""
    lines: list[str] = ["# Commit Messages", ""]
    for idx, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        parts = msg.get("parts", [])
        lines.append(f"## [{idx}] {role}")
        lines.append("")
        for part in parts:
            part_type = part.get("type", "text")
            if part_type == "text":
                text = part.get("text", "")
                # Indent to make it a blockquote / code block if needed
                lines.append(text)
            elif part_type == "tool":
                status = str(part.get("tool_status") or "")
                label = "Tool result" if status in {"completed", "error"} else "Tool call"
                lines.append(f"**{label}:** `{part.get('tool_name', '?')}` status={status or '?'}")
                if part.get("tool_input") is not None:
                    lines.append("")
                    lines.append("```json")
                    lines.append(json.dumps(part.get("tool_input"), ensure_ascii=False, indent=2))
                    lines.append("```")
                if part.get("tool_output"):
                    content = str(part.get("tool_output", ""))
                    lines.append("")
                    lines.append("```")
                    lines.append(content)
                    lines.append("```")
            elif part_type == "tool_call":
                lines.append(f"**Tool call:** `{part.get('tool_name', '?')}`")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(part.get("tool_input", {}), ensure_ascii=False, indent=2))
                lines.append("```")
            elif part_type == "tool_result":
                lines.append(f"**Tool result:** `{part.get('tool_name', '?')}`")
                lines.append("")
                content = str(part.get("text", part.get("tool_result", "")))
                lines.append("```")
                lines.append(content)
                lines.append("```")
            else:
                lines.append(f"*[{part_type} part]*")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _parse_memory_diff(memory_diff: Any) -> dict[str, Any]:
    if isinstance(memory_diff, dict):
        return memory_diff
    if isinstance(memory_diff, str):
        try:
            parsed = json.loads(memory_diff)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _format_memory_diff_markdown(memory_diff: dict[str, Any]) -> str:
    lines = ["# Memory Diff", ""]
    summary = memory_diff.get("summary") if isinstance(memory_diff, dict) else None
    if isinstance(summary, dict):
        lines.extend(
            [
                "## Summary",
                "",
                f"- adds: {summary.get('total_adds', 0)}",
                f"- updates: {summary.get('total_updates', 0)}",
                f"- deletes: {summary.get('total_deletes', 0)}",
                "",
            ]
        )
    operations = memory_diff.get("operations") if isinstance(memory_diff, dict) else None
    operations = operations if isinstance(operations, dict) else {}
    for item in operations.get("adds", []) or []:
        if isinstance(item, dict):
            lines.extend(_memory_diff_file_section("add", item.get("uri"), "", item.get("after", "")))
    for item in operations.get("updates", []) or []:
        if isinstance(item, dict):
            lines.extend(
                _memory_diff_file_section(
                    "update",
                    item.get("uri"),
                    item.get("before", ""),
                    item.get("after", ""),
                )
            )
    return "\n".join(lines).rstrip() + "\n"


def _memory_diff_file_section(kind: str, uri: Any, before: Any, after: Any) -> list[str]:
    path = str(uri or "unknown")
    old_path = "/dev/null" if kind == "add" else path
    diff = difflib.unified_diff(
        str(before or "").splitlines(),
        str(after or "").splitlines(),
        fromfile=old_path,
        tofile=path,
        lineterm="",
    )
    return [
        f"## {kind}: `{path}`",
        "",
        "```diff",
        *diff,
        "```",
        "",
    ]
