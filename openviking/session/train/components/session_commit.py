# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PolicyTrainer implementation backed by OpenViking session.commit."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openviking.session.train.components.progress import run_with_progress
from openviking.session.train.context import PipelineContext
from openviking.session.train.domain import (
    CriterionResult,
    ExperienceSet,
    PolicyApplyResult,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
    RubricEvaluation,
)
from openviking.session.train.utils import average_score, validate_rollouts_have_cases
from openviking_cli.client.http import AsyncHTTPClient

_TRAINING_COMMIT_MEMORY_TYPES = ("cases", "trajectories", "experiences")
_TRAINING_CASE_SPEC_PROTOCOL = "openviking.batch_train.case_spec.v1"
_TRAINING_CASE_SPEC_HEADER = "# OpenViking Batch Training CaseSpec v1"
_SESSION_BATCH_ADD_MESSAGE_LIMIT = 100


@dataclass(slots=True)
class SessionCommitPolicyTrainer:
    """Train remotely by writing rollout messages to sessions and committing them."""

    client: AsyncHTTPClient
    run_id: str = ""
    keep_recent_count: int = 0
    poll_interval_seconds: float = 2.0
    timeout_seconds: float | None = None
    commit_concurrency: int = 20
    show_progress: bool = False
    progress_label: str = "session-commit"
    event_recorder: Any | None = None

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = _new_run_id()
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.commit_concurrency <= 0:
            raise ValueError("commit_concurrency must be > 0")

    async def train_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        context: PipelineContext | Any = None,
        analyses: list[RolloutAnalysis] | None = None,
    ) -> RolloutTrainingResult:
        rollout_list = list(rollouts)
        validate_rollouts_have_cases(rollout_list)
        if analyses is not None and len(analyses) != len(rollout_list):
            raise ValueError(
                "SessionCommitPolicyTrainer analyses length must match rollouts length when provided"
            )
        execution_metadata = dict(getattr(context, "execution_metadata", {}) or {})

        async def _commit(rollout: Rollout, idx: int) -> dict[str, Any]:
            return await self._commit_one(
                rollout,
                idx,
                execution_metadata=execution_metadata,
            )

        commit_results = await run_with_progress(
            rollout_list,
            coroutine_factory=_commit,
            label="train_start",
            enabled=self.show_progress,
            description=(
                f"Processing {len(rollout_list)} rollouts, "
                f"concurrency={self.commit_concurrency}"
            ),
            concurrency=self.commit_concurrency,
        )
        analysis_list = [_analysis_from_rollout(rollout) for rollout in rollout_list]
        errors = [item["error"] for item in commit_results if item.get("error")]
        apply_result = PolicyApplyResult(
            updated_policy_set=policy_set,
            errors=errors,
            metadata={
                "committed_rollout_count": len(commit_results),
                "commit_results": commit_results,
                "run_id": self.run_id,
            },
        )
        return RolloutTrainingResult(
            analyses=analysis_list,
            gradients=[],
            plan=PolicyUpdatePlan(metadata={"trainer": "session_commit", "run_id": self.run_id}),
            apply_result=apply_result,
            metadata={
                "policy_set_root_uri": policy_set.root_uri,
                "rollout_count": len(rollout_list),
                "analysis_count": len(analysis_list),
                "gradient_count": 0,
                "score": average_score(analysis_list),
                "source": "session_commit_trainer",
                "run_id": self.run_id,
            },
        )

    async def _commit_one(
        self,
        rollout: Rollout,
        index: int,
        *,
        execution_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = _session_id_for_rollout(rollout, run_id=self.run_id)
        stage = "prepare_messages"
        try:
            messages = (
                [_case_spec_message_to_request(rollout)]
                + [_message_to_request(message) for message in rollout.messages]
                + [_evaluation_message_to_request(rollout)]
            )
            stage = "create_session"
            await self.client.create_session(
                session_id=session_id,
                memory_policy=_training_commit_memory_policy(),
            )
            stage = "batch_add_messages"
            await self._batch_add_messages(session_id, messages)
            stage = "commit_session"
            commit_result = await self.client.commit_session(
                session_id,
                telemetry=True,
                keep_recent_count=self.keep_recent_count,
            )
            task_id = str(commit_result.get("task_id") or "")
            archive_uri = str(commit_result.get("archive_uri") or "")
            trace_id = _commit_trace_id(commit_result)
            telemetry_id = _commit_telemetry_id(commit_result)
            await self._record_event(
                "train_commit_submitted",
                rollout=rollout,
                index=index,
                session_id=session_id,
                stage=stage,
                execution_metadata=execution_metadata,
                task_id=task_id,
                archive_uri=archive_uri,
                trace_id=trace_id,
                telemetry_id=telemetry_id,
                score=_rollout_score(rollout),
            )
            stage = "wait_task"
            task = await self._wait_task(task_id) if task_id else None
            task_error = _task_error(task)
            if task_error:
                print(
                    f"[session_commit] failed stage={stage} session_id={session_id} "
                    f"task_id={task_id} trace_id={trace_id or '<none>'} "
                    f"error={task_error}",
                    flush=True,
                )
            await self._record_event(
                "train_commit_failed" if task_error else "train_commit_done",
                rollout=rollout,
                index=index,
                session_id=session_id,
                stage=stage,
                execution_metadata=execution_metadata,
                task_id=task_id,
                archive_uri=archive_uri,
                trace_id=trace_id,
                telemetry_id=telemetry_id,
                task_status=task.get("status") if isinstance(task, dict) else None,
                score=_rollout_score(rollout),
                error=task_error,
            )
            return {
                "index": index,
                "session_id": session_id,
                "stage": stage,
                "task_id": task_id,
                "archive_uri": archive_uri,
                "trace_id": trace_id,
                "telemetry_id": telemetry_id,
                "task_status": task.get("status") if isinstance(task, dict) else None,
                "score": _rollout_score(rollout),
                "error": task_error,
            }
        except Exception as exc:
            print(
                f"[session_commit] failed stage={stage} session_id={session_id} "
                f"task_id=<none> trace_id=<none> error={exc}",
                flush=True,
            )
            await self._record_event(
                "train_commit_failed",
                rollout=rollout,
                index=index,
                session_id=session_id,
                stage=stage,
                execution_metadata=execution_metadata,
                task_id="",
                archive_uri="",
                trace_id=None,
                telemetry_id=None,
                task_status="failed",
                score=_rollout_score(rollout),
                error=str(exc),
            )
            return {
                "index": index,
                "session_id": session_id,
                "stage": stage,
                "task_id": "",
                "archive_uri": "",
                "trace_id": None,
                "telemetry_id": None,
                "task_status": "failed",
                "score": _rollout_score(rollout),
                "error": str(exc),
            }


    async def _record_event(
        self,
        event: str,
        *,
        rollout: Rollout,
        index: int,
        session_id: str,
        stage: str,
        execution_metadata: dict[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        if self.event_recorder is None:
            return
        record = getattr(self.event_recorder, "record", None)
        if record is None:
            return
        payload = {
            "index": index,
            "stage": stage,
            "session_id": session_id,
            **_rollout_event_fields(
                rollout,
                execution_metadata=execution_metadata,
            ),
            **fields,
        }
        result = record(event, **payload)
        if asyncio.iscoroutine(result):
            await result

    async def _batch_add_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        for start in range(0, len(messages), _SESSION_BATCH_ADD_MESSAGE_LIMIT):
            await self.client.batch_add_messages(
                session_id, messages[start : start + _SESSION_BATCH_ADD_MESSAGE_LIMIT]
            )

    async def _wait_task(self, task_id: str) -> dict[str, Any]:
        deadline = (
            asyncio.get_running_loop().time() + self.timeout_seconds
            if self.timeout_seconds is not None
            else None
        )
        while True:
            task = await self.client.get_task(task_id)
            if task and task.get("status") in {"completed", "failed"}:
                return task
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                return {"task_id": task_id, "status": "timeout", "error": "commit task timeout"}
            await asyncio.sleep(self.poll_interval_seconds)


def _rollout_event_fields(
    rollout: Rollout,
    *,
    execution_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    case = rollout.case
    metadata = rollout.metadata or {}
    rollout_execution_metadata = metadata.get("execution_metadata", {})
    if not isinstance(rollout_execution_metadata, dict):
        rollout_execution_metadata = {}
    event_execution_metadata = dict(rollout_execution_metadata)
    event_execution_metadata.update(execution_metadata or {})
    case_input = case.input or {}
    return {
        "epoch": event_execution_metadata.get("epoch"),
        "training": event_execution_metadata.get("training"),
        "rollout_stage": event_execution_metadata.get("rollout_stage")
        or event_execution_metadata.get("stage"),
        "case_name": case.name,
        "task_signature": case.task_signature,
        "split": (
            case_input.get("data_split")
            or metadata.get("data_split")
            or case_input.get("split")
            or metadata.get("split")
        ),
        "task_no": (
            case_input.get("task_no")
            if case_input.get("task_no") is not None
            else metadata.get("task_no")
        ),
        "case_task_id": case_input.get("task_id") or metadata.get("task_id"),
        "task_id": case_input.get("task_id") or metadata.get("task_id"),
        "policy_snapshot_id": rollout.policy_snapshot_id,
        "passed": bool(rollout.evaluation.passed) if rollout.evaluation is not None else None,
    }


def _training_commit_memory_policy() -> dict[str, Any]:
    return {
        "memory_types": list(_TRAINING_COMMIT_MEMORY_TYPES),
        "working_memory": {"enabled": False},
    }


def _analysis_from_rollout(rollout: Rollout) -> RolloutAnalysis:
    return RolloutAnalysis(
        evaluation=_rollout_evaluation_or_default(rollout),
        trajectories=[],
        metadata={
            "rollout": rollout,
            "rollout_messages": rollout.messages,
            "policy_snapshot_id": rollout.policy_snapshot_id,
            "evaluation_source": "rollout"
            if rollout.evaluation is not None
            else "session_commit_default",
        },
    )


def _rollout_evaluation_or_default(rollout: Rollout) -> RubricEvaluation:
    if rollout.evaluation is not None:
        return rollout.evaluation
    return RubricEvaluation(
        passed=False,
        score=0.0,
        criterion_results=[
            CriterionResult(
                criterion_name="rollout_evaluation_provided",
                passed=False,
                score=0.0,
                feedback=["Rollout executor did not provide evaluation."],
                evidence=[],
                metadata={"source": "session_commit_default"},
            )
        ],
        feedback=["Rollout executor did not provide evaluation."],
        metadata={"source": "session_commit_default"},
    )


def _rollout_score(rollout: Rollout) -> float:
    if rollout.evaluation is None:
        return 0.0
    return float(rollout.evaluation.score)

def _task_error(task: dict[str, Any] | None) -> str | None:
    if task is None:
        return None
    if task.get("status") == "failed":
        return str(task.get("error") or "task failed")
    if task.get("status") == "timeout":
        return str(task.get("error") or "task timeout")
    return None


def _commit_trace_id(commit_result: dict[str, Any]) -> str | None:
    trace_id = commit_result.get("trace_id")
    return str(trace_id) if trace_id else None


def _commit_telemetry_id(commit_result: dict[str, Any]) -> str | None:
    telemetry = commit_result.get("telemetry")
    if not isinstance(telemetry, dict):
        return None
    telemetry_id = telemetry.get("id")
    return str(telemetry_id) if telemetry_id else None


def _session_id_for_rollout(rollout: Rollout, *, run_id: str) -> str:
    safe_name = _safe_session_fragment(rollout.case.name)
    metadata = rollout.metadata or {}
    execution_metadata = metadata.get("execution_metadata", {})
    epoch = execution_metadata.get("epoch", "0")
    task_no = metadata.get("task_no", "0")
    split = metadata.get("data_split", "tau2")
    return f"tau2_train_{run_id}_{split}_e{epoch}_t{task_no}_{safe_name}"


def _safe_session_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value)[:80] or "case"


def _new_run_id() -> str:
    return f"{int(time.time())}_{uuid4().hex[:8]}"


def _case_spec_message_to_request(rollout: Rollout) -> dict[str, Any]:
    text = (
        f"{_TRAINING_CASE_SPEC_HEADER}\n\n"
        "The following structured case and rubric describe the task that "
        "produced this rollout. It is control-plane metadata for the "
        "batch training pipeline.\n\n"
        f"```json\n{_case_spec_payload_json(rollout)}\n```"
    )
    return {
        "role": "system",
        "parts": [{"type": "text", "text": text}],
    }


def _case_spec_payload_json(rollout: Rollout) -> str:
    import json

    return json.dumps(_case_spec_payload(rollout), ensure_ascii=False, indent=2, sort_keys=True)


def _case_spec_payload(rollout: Rollout) -> dict[str, Any]:
    case = rollout.case
    return {
        "protocol": _TRAINING_CASE_SPEC_PROTOCOL,
        "case": {
            "name": _stable_case_name(rollout),
            "task_signature": _stable_task_signature(rollout),
            "input": _case_input_payload(case.input),
            "metadata": _stable_case_metadata(rollout),
            "rubric": {
                "name": case.rubric.name,
                "description": case.rubric.description,
                "criteria": [
                    {
                        "name": criterion.name,
                        "description": criterion.description,
                        "required": criterion.required,
                        "weight": criterion.weight,
                    }
                    for criterion in case.rubric.criteria
                ],
            },
        },
    }


def _stable_case_name(rollout: Rollout) -> str:
    case = rollout.case
    return str(
        case.input.get("original_case_name")
        or case.metadata.get("original_case_name")
        or rollout.metadata.get("original_case_name")
        or case.name
    )


def _stable_task_signature(rollout: Rollout) -> str:
    case = rollout.case
    if case.input.get("original_case_name") or case.metadata.get("original_case_name"):
        return str(case.task_signature).split(":trial:", 1)[0]
    return case.task_signature


def _stable_case_metadata(rollout: Rollout) -> dict[str, Any]:
    metadata = dict(rollout.case.metadata or {})
    metadata.setdefault("rollout_case_name", rollout.case.name)
    metadata.setdefault("rollout_task_signature", rollout.case.task_signature)
    return metadata


def _case_input_payload(case_input: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        "domain",
        "split",
        "data_split",
        "task_id",
        "task_no",
        "user_query",
    )
    return {key: case_input[key] for key in allowed_keys if key in case_input}


def _evaluation_message_to_request(rollout: Rollout) -> dict[str, Any]:
    text = (
        "# OpenViking OutcomeEvaluation\n\n"
        "The following structured evaluation describes the outcome of the "
        "preceding rollout. Use it as the training signal when extracting "
        "training memories.\n\n"
        f"```json\n{_evaluation_payload_json(rollout)}\n```"
    )
    return {
        "role": "user",
        "parts": [{"type": "text", "text": text}],
    }


def _evaluation_payload_json(rollout: Rollout) -> str:
    return json.dumps(
        {"evaluation": _evaluation_payload(rollout.evaluation)},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _evaluation_payload(evaluation: RubricEvaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    return {
        "passed": evaluation.passed,
        "score": evaluation.score,
        "feedback": evaluation.feedback,
        "criterion_results": [
            {
                "criterion_name": result.criterion_name,
                "passed": result.passed,
                "score": result.score,
                "feedback": result.feedback,
                "evidence": result.evidence,
                "metadata": result.metadata,
            }
            for result in evaluation.criterion_results
        ],
        "metadata": evaluation.metadata,
    }


def _message_to_request(message: Any) -> dict[str, Any]:
    data = message.to_dict()
    request = {
        "role": data["role"],
        "parts": data.get("parts", []),
        "created_at": data.get("created_at"),
    }
    if data.get("peer_id") is not None:
        request["peer_id"] = data["peer_id"]
    return request
