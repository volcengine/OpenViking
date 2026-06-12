# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""PolicyTrainer implementation backed by OpenViking session.commit."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openviking.session.train.components.progress import ProgressPrinter
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
from openviking_cli.client.http import AsyncHTTPClient

_TRAINING_COMMIT_MEMORY_TYPES = ("cases", "trajectories", "experiences")


@dataclass(slots=True)
class SessionCommitPolicyTrainer:
    """Train remotely by writing rollout messages to sessions and committing them."""

    client: AsyncHTTPClient
    run_id: str = ""
    keep_recent_count: int = 0
    poll_interval_seconds: float = 2.0
    timeout_seconds: float = 600.0
    commit_concurrency: int = 20
    show_progress: bool = False
    progress_label: str = "session-commit"

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = _new_run_id()
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.timeout_seconds <= 0:
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
        del context
        rollout_list = list(rollouts)
        _validate_rollouts_have_cases(rollout_list)
        if analyses is not None and len(analyses) != len(rollout_list):
            raise ValueError(
                "SessionCommitPolicyTrainer analyses length must match rollouts length when provided"
            )
        progress = ProgressPrinter(
            total=len(rollout_list),
            label=self.progress_label,
            enabled=self.show_progress,
        )
        progress.render()

        semaphore = asyncio.Semaphore(self.commit_concurrency)

        async def commit_one(rollout: Rollout, idx: int) -> dict[str, Any]:
            async with semaphore:
                progress.start_one()
                try:
                    return await self._commit_one(rollout, idx)
                finally:
                    progress.complete_one()

        try:
            commit_results = await asyncio.gather(
                *[commit_one(rollout, idx) for idx, rollout in enumerate(rollout_list)]
            )
        finally:
            progress.finish()
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
                "score": _average_score(analysis_list),
                "source": "session_commit_trainer",
                "run_id": self.run_id,
            },
        )

    async def _commit_one(
        self,
        rollout: Rollout,
        index: int,
    ) -> dict[str, Any]:
        session_id = _session_id_for_rollout(rollout, run_id=self.run_id)
        try:
            messages = (
                [_case_spec_message_to_request(rollout)]
                + [_message_to_request(message) for message in rollout.messages]
                + [_evaluation_message_to_request(rollout)]
            )
            await self.client.create_session(
                session_id=session_id,
                memory_policy=_training_commit_memory_policy(),
            )
            await self.client.batch_add_messages(session_id, messages)
            commit_result = await self.client.commit_session(
                session_id,
                keep_recent_count=self.keep_recent_count,
            )
            task_id = str(commit_result.get("task_id") or "")
            task = await self._wait_task(task_id) if task_id else None
            return {
                "index": index,
                "session_id": session_id,
                "task_id": task_id,
                "trace_id": commit_result.get("trace_id"),
                "task_status": task.get("status") if isinstance(task, dict) else None,
                "score": _rollout_score(rollout),
                "error": _task_error(task),
            }
        except Exception as exc:
            return {
                "index": index,
                "session_id": session_id,
                "task_id": "",
                "trace_id": None,
                "task_status": "failed",
                "score": _rollout_score(rollout),
                "error": str(exc),
            }

    async def _wait_task(self, task_id: str) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        while True:
            task = await self.client.get_task(task_id)
            if task and task.get("status") in {"completed", "failed"}:
                return task
            if asyncio.get_running_loop().time() >= deadline:
                return {"task_id": task_id, "status": "timeout", "error": "commit task timeout"}
            await asyncio.sleep(self.poll_interval_seconds)


def _training_commit_memory_policy() -> dict[str, Any]:
    return {"memory_types": list(_TRAINING_COMMIT_MEMORY_TYPES)}


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


def _session_id_for_rollout(rollout: Rollout, *, run_id: str) -> str:
    safe_name = _safe_session_fragment(rollout.case.name)
    metadata = rollout.metadata or {}
    epoch = metadata.get("execution_metadata", {}).get("epoch", "0")
    task_no = metadata.get("task_no", "0")
    split = metadata.get("data_split", "tau2")
    return f"tau2_train_{run_id}_{split}_e{epoch}_t{task_no}_{safe_name}"


def _safe_session_fragment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in value)[:80] or "case"


def _new_run_id() -> str:
    return f"{int(time.time())}_{uuid4().hex[:8]}"


def _case_spec_message_to_request(rollout: Rollout) -> dict[str, Any]:
    return {
        "role": "user",
        "parts": [
            {
                "type": "text",
                "text": (
                    "# OpenViking Training CaseSpec\n\n"
                    "The following structured case and rubric describe the task that "
                    "produced this rollout. Use it as task context when extracting "
                    "training memories.\n\n"
                    f"```json\n{_case_spec_payload_json(rollout)}\n```"
                ),
            }
        ],
    }


def _case_spec_payload_json(rollout: Rollout) -> str:
    import json

    case = rollout.case
    payload = {
        "case": {
            "name": case.name,
            "task_signature": case.task_signature,
            "input": _case_input_payload(case.input),
            "rubric": {
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
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _evaluation_message_to_request(rollout: Rollout) -> dict[str, Any]:
    return {
        "role": "user",
        "parts": [
            {
                "type": "text",
                "text": (
                    "# OpenViking OutcomeEvaluation\n\n"
                    "The following structured evaluation describes the outcome of the "
                    "preceding rollout. Use it as the training signal when extracting "
                    "training memories.\n\n"
                    f"```json\n{_evaluation_payload_json(rollout)}\n```"
                ),
            }
        ],
    }


def _evaluation_payload_json(rollout: Rollout) -> str:
    import json

    return json.dumps(
        {"evaluation": _evaluation_payload(rollout.evaluation)},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _case_input_payload(case_input: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        "domain",
        "split",
        "data_split",
        "task_id",
        "task_no",
        "user_query",
        "ground_truth",
    )
    return {key: case_input[key] for key in allowed_keys if key in case_input}


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


def _average_score(analyses: list[RolloutAnalysis]) -> float | None:
    if not analyses:
        return None
    return sum(float(analysis.evaluation.score) for analysis in analyses) / len(analyses)


def _validate_rollouts_have_cases(rollouts: list[Rollout]) -> None:
    missing = [
        idx for idx, rollout in enumerate(rollouts) if getattr(rollout, "case", None) is None
    ]
    if missing:
        raise ValueError(
            f"rollout training requires Rollout.case for all rollouts; missing indices={missing}"
        )
