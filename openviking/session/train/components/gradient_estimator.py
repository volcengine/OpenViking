# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExtractLoop-backed GradientEstimator component."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.extract_loop import ExtractLoop, PostValidationRetryDecision
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.train.domain import ExperienceSet, RolloutAnalysis, Trajectory
from openviking.session.train.gates import build_gate_retry_instruction, default_policy_gate_runner
from openviking.session.train.gradients import PatchSemanticGradient
from openviking.session.train.utils import first_uri, safe_int
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass(slots=True)
class ExperienceGradientContext:
    """Context for ExperienceGradientEstimator."""

    request_context: RequestContext
    messages: list[Message]
    strict_extract_errors: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExperienceGradientEstimator:
    """Estimate PatchSemanticGradients via experience ExtractLoop.

    This component reuses AgentExperienceContextProvider and ExtractLoop but stops
    before MemoryUpdater.apply_operations.  The resolved operations are converted
    into PatchSemanticGradient instances.
    """

    viking_fs: Any = None
    vlm: Any = None

    @tracer(
        "train.gradient_estimator.experience.estimate",
        ignore_result=True,
        ignore_args=True,
    )
    async def estimate(
        self,
        analysis: RolloutAnalysis,
        experience_set: ExperienceSet,
        context: ExperienceGradientContext,
    ) -> list[PatchSemanticGradient]:
        if context is None or context.request_context is None:
            raise ValueError("ExperienceGradientContext.request_context is required")

        extract_context = _context_with_analysis_messages(context, analysis)

        async def estimate_one(trajectory: Trajectory) -> list[PatchSemanticGradient]:
            if not _should_update_experience_from_trajectory(trajectory):
                return []
            extract_context.metadata["current_analysis"] = analysis
            extract_context.metadata["current_experience_set"] = experience_set
            try:
                operations = await self._run_extract_loop(trajectory, extract_context)
            except Exception:
                logger.exception("Experience gradient estimation failed")
                if context.strict_extract_errors:
                    raise
                return []
            if operations is None:
                return []
            gradients = _operations_to_gradients(
                operations=operations,
                trajectory=trajectory,
                analysis=analysis,
                experience_set=experience_set,
            )
            gated, report = await _evaluate_experience_gradients(
                gradients=gradients,
                analysis=analysis,
                experience_set=experience_set,
            )
            _record_gate_report(report, analysis=analysis, context=context)
            return gated

        gradient_batches = await asyncio.gather(
            *(estimate_one(trajectory) for trajectory in analysis.trajectories)
        )
        return [gradient for batch in gradient_batches for gradient in batch]

    @tracer(
        "train.gradient_estimator.experience.extract_loop",
        ignore_result=True,
        ignore_args=True,
    )
    async def _run_extract_loop(
        self,
        trajectory: Trajectory,
        context: ExperienceGradientContext,
    ):
        config = get_openviking_config()
        vlm = self.vlm or config.vlm.get_vlm_instance()
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required for experience gradient estimation")

        provider = AgentExperienceContextProvider(
            messages=context.messages,
            trajectory_summary=trajectory.content,
            trajectory_uri=trajectory.uri,
        )
        if hasattr(provider, "get_extract_context"):
            extract_context = provider.get_extract_context()
        else:
            extract_context = context
        isolation_handler = MemoryIsolationHandler(
            context.request_context,
            extract_context,
            allowed_memory_types={"experiences"},
        )
        isolation_handler.prepare_messages()

        provider._isolation_handler = isolation_handler
        provider._ctx = context.request_context
        provider._viking_fs = viking_fs

        async def post_validation_hook(
            operations: Any,
            retry_count: int,
            *,
            messages: list[dict[str, Any]] | None = None,
            latest_draft: Any = None,
        ):
            if retry_count >= 1:
                return None
            analysis_obj = _analysis_from_context_metadata(context)
            experience_set = _experience_set_from_context_metadata(context)
            gradients = _operations_to_gradients(
                operations=operations,
                trajectory=trajectory,
                analysis=analysis_obj,
                experience_set=experience_set,
            )
            _, report = await _evaluate_experience_gradients(
                gradients=gradients,
                analysis=analysis_obj,
                experience_set=experience_set,
            )
            instruction = build_gate_retry_instruction(report)
            if not instruction:
                return None
            report_dict = report.to_dict()
            context.metadata.setdefault("gate_retry_reports", []).append(report_dict)
            event = _post_validation_retry_event(
                stage="post_gradient",
                retry_index=retry_count,
                report=report_dict,
                instruction=instruction,
            )
            context.metadata.setdefault("post_validation_retries", []).append(event)
            analysis_obj.metadata.setdefault("post_validation_retries", []).append(event)
            trajectory.metadata.setdefault("post_validation_retries", []).append(event)
            return PostValidationRetryDecision(retry=True, instruction=instruction)

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=context.request_context,
            context_provider=provider,
            isolation_handler=isolation_handler,
            thinking=True,
            post_validation_hook=post_validation_hook,
            max_post_validation_retries=1,
        )
        operations, _ = await orchestrator.run()
        return operations


async def _evaluate_experience_gradients(
    *,
    gradients: list[PatchSemanticGradient],
    analysis: RolloutAnalysis,
    experience_set: ExperienceSet,
) -> tuple[list[PatchSemanticGradient], Any]:
    gate_runner = default_policy_gate_runner()
    return await gate_runner.filter_gradients(
        list(gradients),
        analyses=[analysis],
        policy_set=experience_set,
    )


def _post_validation_retry_event(
    *,
    stage: str,
    retry_index: int,
    report: dict[str, Any],
    instruction: str,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "retry_index": retry_index,
        "evaluated_count": int(report.get("evaluated_count") or 0),
        "allowed_count": int(report.get("allowed_count") or 0),
        "rejected_count": int(report.get("rejected_count") or 0),
        "warning_count": int(report.get("warning_count") or 0),
        "retriable": bool(str(instruction or "").strip()),
        "final_outcome": "retry_requested",
        "instruction_preview": _preview_instruction(instruction),
    }


def _preview_instruction(instruction: str, *, limit: int = 500) -> str:
    text = " ".join(str(instruction or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _record_gate_report(
    report: Any,
    *,
    analysis: RolloutAnalysis,
    context: ExperienceGradientContext,
) -> None:
    context.metadata.setdefault("gate_reports", []).append(report.to_dict())
    analysis.metadata.setdefault("gate_reports", []).append(report.to_dict())


def _analysis_from_context_metadata(context: ExperienceGradientContext) -> RolloutAnalysis:
    analysis = context.metadata.get("current_analysis")
    if not isinstance(analysis, RolloutAnalysis):
        raise RuntimeError("Experience gate post-validation requires current_analysis metadata")
    return analysis


def _experience_set_from_context_metadata(context: ExperienceGradientContext) -> ExperienceSet:
    experience_set = context.metadata.get("current_experience_set")
    if not isinstance(experience_set, ExperienceSet):
        raise RuntimeError(
            "Experience gate post-validation requires current_experience_set metadata"
        )
    return experience_set


def _should_update_experience_from_trajectory(trajectory: Trajectory) -> bool:
    return str(getattr(trajectory, "outcome", "") or "").strip().lower() != "success"


def _context_with_analysis_messages(
    context: ExperienceGradientContext,
    analysis: RolloutAnalysis,
) -> ExperienceGradientContext:
    messages = analysis.metadata.get("rollout_messages")
    if not messages:
        return context
    return ExperienceGradientContext(
        request_context=context.request_context,
        messages=list(messages),
        strict_extract_errors=context.strict_extract_errors,
        metadata=dict(context.metadata),
    )


def _experience_constraint_text(fields: dict[str, Any]) -> str:
    return str(fields.get("constraint") or fields.get("content") or "")


def _operations_to_gradients(
    *,
    operations: Any,
    trajectory: Trajectory,
    analysis: RolloutAnalysis,
    experience_set: ExperienceSet,
) -> list[PatchSemanticGradient]:
    gradients: list[PatchSemanticGradient] = []
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != "experiences":
            continue
        fields = dict(getattr(op, "memory_fields", {}) or {})
        after_content = _experience_constraint_text(fields)
        if not after_content.strip():
            continue

        old_file = getattr(op, "old_memory_file_content", None)
        target_name = str(fields.get("experience_name") or _fallback_experience_name(op))
        target_uri = first_uri(getattr(op, "uris", []) or [])
        base_version = _base_version(old_file, target_uri, experience_set)
        after_file = _operation_after_file(
            fields=fields,
            target_name=target_name,
            target_uri=target_uri,
            old_file=old_file,
        )

        gradients.append(
            PatchSemanticGradient(
                before_file=old_file,
                after_file=after_file,
                base_version=base_version,
                rationale=(
                    "ExtractLoop proposed an experience content update "
                    f"from trajectory {trajectory.uri}."
                ),
                links=[
                    StoredLink(
                        from_uri=target_uri or "",
                        to_uri=trajectory.uri,
                        link_type="derived_from",
                        weight=1.0,
                        match_text=None,
                        description="",
                    )
                ],
                confidence=_confidence(trajectory, analysis),
                metadata={
                    "memory_fields": fields,
                    "uris": list(getattr(op, "uris", []) or []),
                    "trajectory_outcome": trajectory.outcome,
                    "rubric_passed": analysis.evaluation.passed,
                    "supersedes": fields.get("supersedes"),
                    "training_category": _trajectory_training_category(trajectory, analysis),
                },
            )
        )
    return gradients


def _trajectory_training_category(
    trajectory: Trajectory,
    analysis: RolloutAnalysis,
) -> str:
    trajectory_metadata = dict(getattr(trajectory, "metadata", {}) or {})
    for key in ("training_category", "category"):
        value = trajectory_metadata.get(key)
        if value:
            return str(value)

    analysis_metadata = dict(getattr(analysis, "metadata", {}) or {})
    for key in ("training_category", "category", "case_task_signature", "task_signature"):
        value = analysis_metadata.get(key)
        if value:
            return str(value)

    if trajectory.retrieval_anchor:
        return str(trajectory.retrieval_anchor)
    return str(trajectory.name)


def _operation_after_file(
    *,
    fields: dict[str, Any],
    target_name: str,
    target_uri: str | None,
    old_file: MemoryFile | None,
) -> MemoryFile:
    extra_fields = dict(getattr(old_file, "extra_fields", {}) or {})
    for key, value in fields.items():
        if key != "content":
            extra_fields[key] = value
    if "constraint" not in extra_fields and fields.get("content"):
        extra_fields["constraint"] = str(fields.get("content") or "")
    extra_fields["memory_type"] = "experiences"
    extra_fields["experience_name"] = target_name
    return MemoryFile(
        uri=target_uri,
        content=_experience_constraint_text(fields),
        links=list(getattr(old_file, "links", []) or []),
        backlinks=list(getattr(old_file, "backlinks", []) or []),
        memory_type="experiences",
        extra_fields=extra_fields,
    )


def _fallback_experience_name(op: Any) -> str:
    uri = first_uri(getattr(op, "uris", []) or [])
    if uri:
        return uri.rstrip("/").split("/")[-1].removesuffix(".md")
    return "unknown_experience"


def _base_version(
    old_file: Any, target_uri: str | None, experience_set: ExperienceSet
) -> int | None:
    if old_file is not None:
        fields = getattr(old_file, "extra_fields", {}) or {}
        version = safe_int(fields.get("version"))
        if version is not None:
            return version
    if target_uri:
        for policy in experience_set.policies:
            if policy.uri == target_uri:
                return policy.version
    return None


def _confidence(trajectory: Trajectory, analysis: RolloutAnalysis) -> float:
    confidence = 0.5
    if analysis.evaluation.passed:
        confidence += 0.2
    outcome = str(trajectory.outcome).lower()
    if outcome == "success":
        confidence += 0.2
    elif outcome in {"failure", "partial"}:
        confidence -= 0.2
    elif outcome == "unfinished":
        confidence -= 0.1
    return max(0.0, min(1.0, confidence))
