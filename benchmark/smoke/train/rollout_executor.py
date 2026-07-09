#!/usr/bin/env python3
"""Deterministic smoke RolloutExecutor for exercising the train service."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from openviking.message import Message, TextPart, ToolPart
from openviking.session.train import (
    Case,
    CriterionResult,
    ExecutionContext,
    ExperienceSet,
    Rollout,
    RubricEvaluation,
)
from openviking.session.train.components.progress import ProgressPrinter


@dataclass(slots=True)
class SmokeRolloutExecutor:
    """Execute smoke cases without external services or model calls.

    The default behavior is scripted per case. For manual sanity checks, a policy or
    direct experience containing ``smoke_pass_all`` or ``smoke_pass:<task_id>`` flips
    the corresponding scripted failure case to success.
    """

    concurrency: int = 8
    direct_experience_content: str | None = None
    show_progress: bool = False
    progress_label: str = "smoke"

    def __post_init__(self) -> None:
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        case_list = list(cases)
        if not case_list:
            return []
        progress = ProgressPrinter(
            total=len(case_list),
            label=_progress_stage_label(context.metadata.get("stage"), default=self.progress_label),
            enabled=self.show_progress,
            description=f"Running {len(case_list)} smoke rollouts",
        )
        progress.render()
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(case: Case) -> Rollout:
            async with semaphore:
                progress.start_one()
                try:
                    rollout = self._execute_one(case, policy_set, context)
                    progress.complete_one()
                    return rollout
                except Exception:
                    progress.fail_one()
                    raise

        try:
            return list(await asyncio.gather(*(run_one(case) for case in case_list)))
        finally:
            progress.finish()

    def _execute_one(
        self,
        case: Case,
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> Rollout:
        smoke_case = _smoke_case_payload(case)
        task_id = str(case.input.get("task_id") or smoke_case.get("task_id") or case.name)
        forced_success = _policy_forces_success(
            task_id=task_id,
            policy_set=policy_set,
            direct_experience_content=self.direct_experience_content,
        )
        passed = bool(smoke_case.get("passed")) or forced_success
        actual_answer = (
            str(smoke_case.get("expected_answer") or "")
            if forced_success
            else str(smoke_case.get("actual_answer") or "")
        )
        actual_actions = (
            list(smoke_case.get("expected_actions") or [])
            if forced_success
            else list(smoke_case.get("actual_actions") or [])
        )
        messages = _messages_for_case(
            case=case,
            smoke_case=smoke_case,
            actual_actions=actual_actions,
            actual_answer=actual_answer,
            forced_success=forced_success,
        )
        feedback = [] if passed else [str(item) for item in smoke_case.get("feedback", [])]
        if forced_success:
            feedback = [f"由 smoke 策略标记强制将 {task_id} 判定为成功。"]
        return Rollout(
            case=case,
            messages=messages,
            policy_snapshot_id=context.policy_snapshot_id,
            evaluation=_smoke_evaluation(
                passed=passed,
                feedback=feedback,
                expected_actions=smoke_case.get("expected_actions") or [],
                actual_actions=actual_actions,
                expected_answer=str(smoke_case.get("expected_answer") or ""),
                actual_answer=actual_answer,
                forced_success=forced_success,
            ),
            metadata={
                "rollout_backend": "smoke_scripted",
                "data_split": case.input.get("data_split"),
                "task_no": case.input.get("task_no"),
                "task_id": task_id,
                "hard": 1 if passed else 0,
                "soft": 1.0 if passed else 0.0,
                "agent_ok": True,
                "forced_success": forced_success,
                "policy_count": len(policy_set.policies),
                "execution_metadata": dict(context.metadata),
            },
        )


def _smoke_case_payload(case: Case) -> dict[str, Any]:
    payload = case.input.get("smoke_case")
    if isinstance(payload, dict):
        return dict(payload)
    return {
        "task_id": case.input.get("task_id") or case.name,
        "user_query": case.input.get("user_query") or "",
        "expected_answer": case.input.get("expected_answer") or "",
        "actual_answer": case.input.get("expected_answer") or "",
        "expected_actions": case.input.get("expected_actions") or [],
        "actual_actions": case.input.get("expected_actions") or [],
        "passed": True,
        "feedback": [],
    }


def _messages_for_case(
    *,
    case: Case,
    smoke_case: dict[str, Any],
    actual_actions: list[Any],
    actual_answer: str,
    forced_success: bool,
) -> list[Message]:
    messages = [
        _text_message(
            "user",
            str(smoke_case.get("user_query") or case.input.get("user_query") or ""),
        )
    ]
    tool_parts = [_tool_part(action, index=index) for index, action in enumerate(actual_actions)]
    if tool_parts:
        messages.append(
            Message(
                id=f"smoke-tools-{uuid4().hex}",
                role="assistant",
                parts=[
                    TextPart(
                        text=(
                            "我会使用必需的票据工具。"
                            if not forced_success
                            else "我会遵循 smoke 策略标记，并使用期望的工具。"
                        )
                    ),
                    *tool_parts,
                ],
            )
        )
    messages.append(_text_message("assistant", actual_answer))
    return messages


def _tool_part(action: Any, *, index: int) -> ToolPart:
    if not isinstance(action, dict):
        action = {"name": "unknown", "arguments": {"raw": action}, "type": "unknown"}
    name = str(action.get("name") or "unknown")
    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    return ToolPart(
        tool_id=f"smoke-tool-{index}-{uuid4().hex[:8]}",
        tool_name=name,
        tool_input=dict(arguments),
        tool_output=json.dumps(
            {
                "ok": True,
                "tool": name,
                "arguments": arguments,
                "operation_type": action.get("type") or "unknown",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        tool_status="completed",
    )


def _text_message(role: str, text: str) -> Message:
    if role not in {"user", "assistant"}:
        raise ValueError("role must be user or assistant")
    return Message(
        id=f"smoke-{role}-{uuid4().hex}",
        role=role,  # type: ignore[arg-type]
        parts=[TextPart(text=text)],
    )


def _smoke_evaluation(
    *,
    passed: bool,
    feedback: list[str],
    expected_actions: list[Any],
    actual_actions: list[Any],
    expected_answer: str,
    actual_answer: str,
    forced_success: bool,
) -> RubricEvaluation:
    score = 1.0 if passed else 0.0
    evidence = ["smoke_result=passed"] if passed else ["smoke_result=failed"]
    if not passed:
        evidence.extend(feedback)
    return RubricEvaluation(
        passed=passed,
        score=score,
        criterion_results=[
            CriterionResult(
                criterion_name="smoke_success",
                passed=passed,
                score=score,
                feedback=feedback,
                evidence=evidence,
                metadata={
                    "hard": 1 if passed else 0,
                    "soft": score,
                    "expected_actions": expected_actions,
                    "actual_actions": actual_actions,
                    "expected_answer": expected_answer,
                    "actual_answer": actual_answer,
                    "forced_success": forced_success,
                },
            )
        ],
        metadata={
            "hard": 1 if passed else 0,
            "soft": score,
            "smoke_result": {
                "expected_actions": expected_actions,
                "actual_actions": actual_actions,
                "expected_answer": expected_answer,
                "actual_answer": actual_answer,
                "forced_success": forced_success,
            },
        },
    )


def _policy_forces_success(
    *,
    task_id: str,
    policy_set: ExperienceSet,
    direct_experience_content: str | None,
) -> bool:
    markers = ["smoke_pass_all", f"smoke_pass:{task_id}"]
    haystacks = [policy.content for policy in policy_set.policies]
    if direct_experience_content:
        haystacks.append(direct_experience_content)
    combined = "\n".join(str(item) for item in haystacks).lower()
    return any(marker.lower() in combined for marker in markers)


def _progress_stage_label(stage: Any, *, default: str) -> str:
    stage_text = str(stage or "")
    stage_parts = stage_text.split(maxsplit=1)
    stage_name = stage_parts[0] if stage_parts else ""
    if stage_name.endswith("_rollout"):
        return f"{stage_name}_start"
    if stage_name.endswith("_rollout_start"):
        return stage_name
    return default
