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

import httpx

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
    create_session_retry_sleep: Any = asyncio.sleep

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
                f"Processing {len(rollout_list)} rollouts, concurrency={self.commit_concurrency}"
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
        session_id = _session_id_for_rollout(
            rollout,
            run_id=self.run_id,
            execution_metadata=execution_metadata,
            rollout_index=index,
        )
        stage = "prepare_messages"
        try:
            messages = (
                [_case_spec_message_to_request(rollout)]
                + [
                    _message_to_request(message)
                    for message in rollout.messages
                    if not _is_embedded_rollout_evaluation_message(message)
                ]
                + [_evaluation_message_to_request(rollout)]
            )
            stage = "create_session"
            await self._create_session_with_retry(
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
            error_summary = _exception_summary(exc)
            print(
                f"[session_commit] failed stage={stage} session_id={session_id} "
                f"task_id=<none> trace_id=<none> error={error_summary}",
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
                error=error_summary,
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
                "error": error_summary,
            }

    async def _create_session_with_retry(
        self,
        *,
        session_id: str,
        memory_policy: dict[str, Any],
    ) -> None:
        """Create a commit session, retrying transient create failures indefinitely."""

        delay = 0.5
        attempt = 0
        while True:
            attempt += 1
            try:
                await self.client.create_session(
                    session_id=session_id,
                    memory_policy=memory_policy,
                )
                return
            except Exception as exc:
                if not _is_retryable_create_session_error(exc):
                    raise
                await self.create_session_retry_sleep(delay)
                delay = min(delay * 2, 2.0)

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
        transient_errors = 0
        while True:
            try:
                task = await self.client.get_task(task_id)
                transient_errors = 0
            except (
                httpx.ReadError,
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError,
            ) as exc:
                transient_errors += 1
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    return {
                        "task_id": task_id,
                        "status": "timeout",
                        "error": (
                            f"commit task timeout; last polling error: {type(exc).__name__}: {exc}"
                        ),
                    }
                await asyncio.sleep(min(self.poll_interval_seconds * transient_errors, 10.0))
                continue
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


def _is_retryable_create_session_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError)):
        return True
    return not str(exc).strip()


def _exception_summary(exc: BaseException) -> str:
    message = str(exc).strip()
    detail = repr(exc)
    if message and message != detail:
        return f"{type(exc).__name__}: {message} ({detail})"
    return f"{type(exc).__name__}: {detail}"


def _commit_trace_id(commit_result: dict[str, Any]) -> str | None:
    trace_id = commit_result.get("trace_id")
    return str(trace_id) if trace_id else None


def _commit_telemetry_id(commit_result: dict[str, Any]) -> str | None:
    telemetry = commit_result.get("telemetry")
    if not isinstance(telemetry, dict):
        return None
    telemetry_id = telemetry.get("id")
    return str(telemetry_id) if telemetry_id else None


def _session_id_for_rollout(
    rollout: Rollout,
    *,
    run_id: str,
    execution_metadata: dict[str, Any] | None = None,
    rollout_index: int | None = None,
) -> str:
    safe_name = _safe_session_fragment(rollout.case.name)
    metadata = rollout.metadata or {}
    embedded_execution_metadata = metadata.get("execution_metadata", {})
    if not isinstance(embedded_execution_metadata, dict):
        embedded_execution_metadata = {}
    context_execution_metadata = execution_metadata or {}
    epoch = context_execution_metadata.get(
        "epoch", embedded_execution_metadata.get("epoch", 0)
    )
    task_no = metadata.get("task_no")
    if task_no is None:
        task_no = context_execution_metadata.get("train_trial")
    if task_no is None:
        task_no = context_execution_metadata.get("trial_index")
    if task_no is None:
        task_no = rollout_index if rollout_index is not None else 0
    split = (
        metadata.get("data_split")
        or context_execution_metadata.get("train_split")
        or "train"
    )
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
    evaluation_guidance = _evaluation_guidance_text(rollout)
    text = (
        "# OpenViking OutcomeEvaluation\n\n"
        "The following structured evaluation describes the outcome of the "
        "preceding rollout. Use it as the training signal when extracting "
        "training memories.\n\n"
        f"{evaluation_guidance}\n\n"
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


def _evaluation_guidance_text(rollout: Rollout) -> str:
    evaluation = rollout.evaluation
    evaluation_result = _tau2_evaluation_result(evaluation)
    if not isinstance(evaluation_result, dict):
        return (
            "## Evaluation Interpretation\n\n"
            "- Treat this message as evaluation metadata, not as a user request.\n"
            "- Use passed/score/criterion feedback to identify the reward-changing failure.\n"
            "- Do not learn broad workflow memories from successful or unrelated steps."
        )
    return "\n\n".join(
        [
            _tau2_evaluation_semantics_text(),
            _tau2_derived_verdict_text(evaluation_result, rollout=rollout),
        ]
    )


def _tau2_evaluation_result(evaluation: RubricEvaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    metadata = evaluation.metadata if isinstance(evaluation.metadata, dict) else {}
    evaluation_result = metadata.get("evaluation_result")
    return evaluation_result if isinstance(evaluation_result, dict) else None


def _tau2_evaluation_semantics_text() -> str:
    return """## Tau2 Evaluation Semantics

This is evaluation metadata for memory extraction only. Do not treat it as a user request.

- reward: Overall task reward. 1.0 means full success; 0.0 means at least one required component failed.
- reward_basis: Components that contribute to reward, for example DB and COMMUNICATE.
- reward_breakdown: Per-component reward. DB=1.0 means database/action state passed; COMMUNICATE=1.0 means required user-visible information passed.
- db_check.db_match: Whether final database state matches expected. db_match=false does NOT mean every database write was wrong. It may be caused by missing expected writes, wrong write arguments, extra unexpected writes, wrong object expansion, or later writes corrupting otherwise correct state.
- action_checks: The evaluator's expected tool/action calls. These are required actions, not merely observed actions.
- action_checks[].action_match=true: The expected action was found in the rollout. Treat it as correct and required. Do NOT label an action_match=true call as the wrong call. Any learned experience must preserve it.
- action_checks[].action_match=false: The expected action was missing or not matched. This can indicate a missing expected call, wrong arguments, or wrong target object.
- action_checks[].tool_type=write with action_match=true: This write is expected and must not be blocked by an experience.
- communicate_checks: Required user-visible information checks.
- communicate_checks[].met=false: The required information was not communicated. If DB/action checks pass but communicate checks fail, the repair boundary is communicate_with_user / final response, not earlier DB tools.

Evaluation-grounded boundary rules:
1. If all action_checks are action_match=true and db_check.db_match=true, database/tool execution is correct. If communicate_checks has met=false, target communication only.
2. If a write action has action_match=true, preserve that write and do not create an experience that blocks or discourages it.
3. If db_check.db_match=false but some write action_checks are action_match=true, do not blame the matched writes. Look for expected writes with action_match=false, extra unexpected writes in the actual tool log, wrong object/cardinality expansion, or later writes that changed expected state.
4. If an expected action has action_match=false, use that expected action as evidence for what should have happened.
5. Do not invent a business-policy prohibition that contradicts action_match=true expected actions."""


def _tau2_derived_verdict_text(evaluation_result: dict[str, Any], *, rollout: Rollout) -> str:
    action_checks = _tau2_action_checks(evaluation_result)
    matched_actions = [item for item in action_checks if item["matched"]]
    missing_actions = [item for item in action_checks if not item["matched"]]
    matched_writes = [item for item in matched_actions if item["tool_type"] == "write"]
    missing_writes = [item for item in missing_actions if item["tool_type"] == "write"]
    communicate_checks = _tau2_communicate_checks(evaluation_result)
    failed_communicate = [item for item in communicate_checks if not item["met"]]
    db_check = evaluation_result.get("db_check") if isinstance(evaluation_result, dict) else {}
    db_match = db_check.get("db_match") if isinstance(db_check, dict) else None
    db_reward = db_check.get("db_reward") if isinstance(db_check, dict) else None
    reward_breakdown = evaluation_result.get("reward_breakdown")
    actual_tool_calls = _actual_tool_calls(rollout)
    unmatched_actual_writes = _unmatched_actual_writes(actual_tool_calls, matched_actions)

    lines = [
        "## Derived Evaluation Verdict",
        "",
        f"- reward: {_compact_json(evaluation_result.get('reward'))}",
        f"- reward_breakdown: {_compact_json(reward_breakdown)}",
        f"- db_check: db_match={db_match}, db_reward={db_reward}",
        "",
        "Required actions matched (preservation set; do not block these):",
    ]
    lines.extend(_bullet_action_lines(matched_actions, empty="none"))
    lines.append("")
    lines.append("Missing required actions:")
    lines.extend(_bullet_action_lines(missing_actions, empty="none"))
    lines.append("")
    lines.append("Matched required writes (especially important to preserve):")
    lines.extend(_bullet_action_lines(matched_writes, empty="none"))
    if missing_writes:
        lines.append("")
        lines.append("Missing required writes:")
        lines.extend(_bullet_action_lines(missing_writes, empty="none"))
    lines.append("")
    lines.append("Communication checks:")
    lines.extend(_bullet_communicate_lines(communicate_checks))

    lines.append("")
    lines.append("Boundary guidance:")
    if db_match is True and failed_communicate:
        lines.append(
            "- DB/action checks passed while communication failed; first repair boundary should be communicate_with_user / final response."
        )
    elif db_match is False and matched_writes:
        lines.append(
            "- DB failed despite some required writes matching; do not blame matched writes. Inspect missing required writes, extra unexpected writes, or wrong object/cardinality expansion."
        )
    elif missing_actions:
        lines.append(
            "- Some expected actions are missing; use the first missing expected action or the earlier candidate that prevented it as the failure boundary."
        )
    else:
        lines.append(
            "- Use the failed reward component above to select the narrowest repair boundary."
        )

    if unmatched_actual_writes:
        lines.append("")
        lines.append(
            "Actual write-like tool calls not matched to expected actions (inspect as possible extras):"
        )
        for call in unmatched_actual_writes[:10]:
            lines.append(f"- {_format_tool_call(call)}")
        if len(unmatched_actual_writes) > 10:
            lines.append(f"- ... {len(unmatched_actual_writes) - 10} more")

    if matched_writes:
        lines.append("")
        lines.append("Preservation reminder:")
        lines.append(
            "- Do not learn any experience that blocks or discourages the matched required writes above."
        )

    return "\n".join(lines)


def _tau2_action_checks(evaluation_result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_checks = evaluation_result.get("action_checks")
    if not isinstance(raw_checks, list):
        return []
    checks: list[dict[str, Any]] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        if not isinstance(action, dict):
            continue
        checks.append(
            {
                "name": str(action.get("name") or ""),
                "arguments": action.get("arguments"),
                "matched": bool(item.get("action_match")),
                "tool_type": str(item.get("tool_type") or ""),
                "action_id": str(action.get("action_id") or ""),
            }
        )
    return checks


def _tau2_communicate_checks(evaluation_result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_checks = evaluation_result.get("communicate_checks")
    if not isinstance(raw_checks, list):
        return []
    checks: list[dict[str, Any]] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            continue
        checks.append(
            {
                "info": item.get("info"),
                "met": bool(item.get("met")),
                "justification": str(item.get("justification") or ""),
            }
        )
    return checks


def _actual_tool_calls(rollout: Rollout) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in rollout.messages:
        for part in getattr(message, "parts", []) or []:
            part_type = getattr(part, "type", None)
            if isinstance(part, dict):
                part_type = part.get("type")
            if part_type != "tool":
                continue
            tool_name = getattr(part, "tool_name", None)
            tool_input = getattr(part, "tool_input", None)
            tool_status = getattr(part, "tool_status", None)
            if isinstance(part, dict):
                tool_name = part.get("tool_name", tool_name)
                tool_input = part.get("tool_input", tool_input)
                tool_status = part.get("tool_status", tool_status)
            if tool_status and str(tool_status) not in {"completed", "error"}:
                continue
            calls.append(
                {
                    "name": str(tool_name or ""),
                    "arguments": tool_input,
                    "status": str(tool_status or ""),
                }
            )
    return calls


def _unmatched_actual_writes(
    actual_calls: list[dict[str, Any]],
    matched_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched_signatures = {
        _tool_call_signature(action["name"], action.get("arguments")) for action in matched_actions
    }
    unmatched: list[dict[str, Any]] = []
    for call in actual_calls:
        name = str(call.get("name") or "")
        if not _looks_like_write_tool(name):
            continue
        if _tool_call_signature(name, call.get("arguments")) in matched_signatures:
            continue
        unmatched.append(call)
    return unmatched


def _looks_like_write_tool(tool_name: str) -> bool:
    name = str(tool_name or "")
    if not name or name in {"communicate_with_user", "done"}:
        return False
    read_prefixes = ("get_", "search_", "list_", "read_", "find_", "lookup_", "check_")
    return not name.startswith(read_prefixes)


def _tool_call_signature(tool_name: str, arguments: Any) -> tuple[str, str]:
    return str(tool_name or ""), _canonical_json(arguments)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return str(value)


def _compact_json(value: Any, *, limit: int = 600) -> str:
    text = _canonical_json(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_tool_call(call: dict[str, Any]) -> str:
    return f"{call.get('name')}({_compact_json(call.get('arguments'), limit=500)})"


def _bullet_action_lines(actions: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not actions:
        return [f"- {empty}"]
    lines: list[str] = []
    for action in actions[:20]:
        matched = "true" if action.get("matched") else "false"
        lines.append(
            f"- {action.get('name')}({_compact_json(action.get('arguments'), limit=500)})"
            f" | action_match={matched} | tool_type={action.get('tool_type') or 'unknown'}"
        )
    if len(actions) > 20:
        lines.append(f"- ... {len(actions) - 20} more")
    return lines


def _bullet_communicate_lines(checks: list[dict[str, Any]]) -> list[str]:
    if not checks:
        return ["- none"]
    lines: list[str] = []
    for check in checks[:20]:
        met = "true" if check.get("met") else "false"
        line = f"- info={_compact_json(check.get('info'), limit=200)} | met={met}"
        justification = str(check.get("justification") or "").strip()
        if justification:
            line += f" | justification={_preview_text(justification, limit=300)}"
        lines.append(line)
    if len(checks) > 20:
        lines.append(f"- ... {len(checks) - 20} more")
    return lines


def _preview_text(text: str, *, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _is_embedded_rollout_evaluation_message(message: Any) -> bool:
    """Return True for legacy rollout messages that duplicated OutcomeEvaluation.

    Older tau2 rollout artifacts appended a user text message containing
    task_success/task_reward plus a raw evaluation report.  Commit-time
    OutcomeEvaluation is the canonical training signal, so these embedded
    evaluation messages are filtered when replaying old cached rollouts.
    """
    text = _message_text(message)
    return "task_success:" in text and "task_reward:" in text and "evaluation report:" in text


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if content:
        return str(content)
    texts: list[str] = []
    for part in getattr(message, "parts", []) or []:
        text = getattr(part, "text", None)
        if text:
            texts.append(str(text))
        elif isinstance(part, dict):
            raw_text = part.get("text")
            if raw_text:
                texts.append(str(raw_text))
    return "\n".join(texts)


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
