# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExtractLoop-backed GradientEstimator component."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from openviking.server.identity import RequestContext
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking.session.memory.agent_trajectory_context_provider import (
    extract_injected_experience_reminders,
)
from openviking.session.memory.dataclass import MemoryTypeSchema, StoredLink
from openviking.session.memory.extract_loop import ExtractLoop, PostValidationRetryDecision
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    create_default_registry,
)
from openviking.session.memory.memory_updater import render_operation_after_file
from openviking.session.train.components import (
    experience_replay_codecs as _experience_replay_codecs,  # noqa: F401
)
from openviking.session.train.domain import (
    ExperienceSet,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.gates import (
    ExperienceRootCausePreventionGate,
    GateDecision,
    GateReport,
    GateRunner,
    build_gate_retry_instruction,
)
from openviking.session.train.gradients import PatchSemanticGradient
from openviking.session.train.utils import first_uri, safe_int
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import replay, tracer
from openviking.telemetry.replay.models import EncodedValue, ReplayCodecError
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_EXPERIENCE_POST_VALIDATION_MAX_RETRIES = 2
_REPLAY_VIKING_FS_PLACEHOLDER = object()


@dataclass(slots=True)
class ExperienceGradientContext:
    """Context for ExperienceGradientEstimator."""

    request_context: RequestContext
    strict_extract_errors: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExperienceGradientEstimateRequest:
    """Serializable input for one trajectory's experience-gradient replay entry."""

    trajectory: Trajectory
    evaluation: RubricEvaluation
    experience_set: ExperienceSet
    request_context: RequestContext
    case_uri: str = ""
    case_name: str = ""
    task_signature: str = ""
    loaded_experience_uris: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _encoded_request_field(payload: dict[str, Any], name: str) -> EncodedValue:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ReplayCodecError(f"Experience replay request is missing encoded field {name!r}")
    return value


@replay.codec(
    ExperienceGradientEstimateRequest,
    name="openviking.train.experience_gradient_estimate_request",
)
class ExperienceGradientEstimateRequestReplayCodec:
    @staticmethod
    def encode(value: ExperienceGradientEstimateRequest, encode):
        return {
            "trajectory": encode(value.trajectory),
            "evaluation": encode(value.evaluation),
            "experience_set": encode(value.experience_set),
            "request_context": encode(value.request_context),
            "case_uri": encode(value.case_uri),
            "case_name": encode(value.case_name),
            "task_signature": encode(value.task_signature),
            "loaded_experience_uris": encode(value.loaded_experience_uris),
            "diagnostics": encode(value.diagnostics),
        }

    @staticmethod
    def decode(payload, decode):
        return ExperienceGradientEstimateRequest(
            trajectory=decode(_encoded_request_field(payload, "trajectory")),
            evaluation=decode(_encoded_request_field(payload, "evaluation")),
            experience_set=decode(_encoded_request_field(payload, "experience_set")),
            request_context=decode(_encoded_request_field(payload, "request_context")),
            case_uri=decode(_encoded_request_field(payload, "case_uri")),
            case_name=decode(_encoded_request_field(payload, "case_name")),
            task_signature=decode(_encoded_request_field(payload, "task_signature")),
            loaded_experience_uris=decode(
                _encoded_request_field(payload, "loaded_experience_uris")
            ),
            diagnostics=decode(_encoded_request_field(payload, "diagnostics")),
        )


@dataclass(slots=True)
class ExperienceGradientEstimator:
    """Estimate PatchSemanticGradients via experience ExtractLoop.

    This component reuses AgentExperienceContextProvider and ExtractLoop but stops
    before MemoryUpdater.apply_operations.  The resolved operations are converted
    into PatchSemanticGradient instances.
    """

    viking_fs: Any = None
    vlm: Any = None
    registry: MemoryTypeRegistry | None = None

    def _get_registry(self) -> MemoryTypeRegistry:
        if self.registry is None:
            self.registry = create_default_registry()
        return self.registry

    def _get_experience_schema(self) -> MemoryTypeSchema:
        schema = self._get_registry().get("experiences")
        if schema is None or not schema.enabled:
            raise ValueError("Memory schema not found or disabled: experiences")
        return schema

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

        requests = [
            ExperienceGradientEstimateRequest(
                trajectory=trajectory,
                evaluation=analysis.evaluation,
                experience_set=experience_set,
                request_context=context.request_context,
                case_uri=_trajectory_or_analysis_metadata(trajectory, analysis, "case_uri"),
                case_name=_trajectory_or_analysis_metadata(trajectory, analysis, "case_name"),
                task_signature=_trajectory_or_analysis_metadata(
                    trajectory, analysis, "task_signature"
                ),
                loaded_experience_uris=_loaded_experience_uris(analysis),
            )
            for trajectory in analysis.trajectories
            if _should_update_experience_from_trajectory(trajectory)
        ]

        async def estimate_one(
            request: ExperienceGradientEstimateRequest,
        ) -> list[PatchSemanticGradient]:
            try:
                return await self.estimate_trajectory_gradients(request)
            except Exception:
                logger.exception("Experience gradient estimation failed")
                if context.strict_extract_errors:
                    raise
                return []

        gradient_batches = await asyncio.gather(*(estimate_one(request) for request in requests))
        for request in requests:
            _merge_diagnostics(context.metadata, request.diagnostics)
            _merge_diagnostics(analysis.metadata, request.diagnostics)
        return [gradient for batch in gradient_batches for gradient in batch]

    @replay.entry("memory.experience.estimate_gradients")
    async def estimate_trajectory_gradients(
        self,
        request: ExperienceGradientEstimateRequest,
    ) -> list[PatchSemanticGradient]:
        analysis_metadata = {
            key: value
            for key, value in {
                "case_uri": request.case_uri,
                "case_name": request.case_name,
                "task_signature": request.task_signature,
            }.items()
            if value
        }
        analysis_metadata["loaded_experience_uris"] = list(request.loaded_experience_uris)
        analysis = RolloutAnalysis(
            evaluation=request.evaluation,
            trajectories=[request.trajectory],
            metadata=analysis_metadata,
        )
        context = ExperienceGradientContext(
            request_context=request.request_context,
            metadata={
                "current_analysis": analysis,
                "current_experience_set": request.experience_set,
            },
        )
        try:
            operations = await self._run_extract_loop(request.trajectory, context)
            if operations is None:
                return []
            return _operations_to_gradients(
                operations=operations,
                trajectory=request.trajectory,
                analysis=analysis,
                experience_set=request.experience_set,
                schema=self._get_experience_schema(),
            )
        finally:
            request.diagnostics.update(
                {
                    key: value
                    for key, value in context.metadata.items()
                    if key not in {"current_analysis", "current_experience_set"}
                }
            )

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

        analysis_obj = _analysis_from_context_metadata_optional(context)
        provider_kwargs = {
            "trajectory_summary": trajectory.content,
            "trajectory_uri": trajectory.uri,
        }
        for key in ("case_uri", "case_name", "task_signature"):
            value = _trajectory_or_analysis_metadata(trajectory, analysis_obj, key)
            if value:
                provider_kwargs[key] = value
        provider_kwargs["loaded_experience_uris"] = list(
            dict(getattr(analysis_obj, "metadata", {}) or {}).get("loaded_experience_uris", [])
        )
        provider = AgentExperienceContextProvider(**provider_kwargs)
        provider._registry = self._get_registry()
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
            try:
                _sync_prefetched_comparison_trajectories(provider, trajectory)
                analysis_obj = _analysis_from_context_metadata(context)
                experience_set = _experience_set_from_context_metadata(context)
                gradients = _operations_to_gradients(
                    operations=operations,
                    trajectory=trajectory,
                    analysis=analysis_obj,
                    experience_set=experience_set,
                    schema=self._get_experience_schema(),
                )
                _, report = await _evaluate_experience_gradients_with_trace(
                    gradients=gradients,
                    analysis=analysis_obj,
                    experience_set=experience_set,
                    semantic_vlm=vlm,
                    retry_count=retry_count,
                )
            except Exception as exc:
                logger.exception("Experience post-validation failed; discarding draft")
                analysis_obj = _analysis_from_context_metadata_optional(context)
                report = _post_validation_failure_report(exc)
                _record_gate_report(report, analysis=analysis_obj, context=context)
                event = _post_validation_retry_event(
                    stage="post_gradient",
                    retry_index=retry_count,
                    report=report.to_dict(),
                    instruction="",
                    final_outcome="discarded_after_gate_error",
                )
                _record_post_validation_event(
                    event,
                    context=context,
                    analysis=analysis_obj,
                    trajectory=trajectory,
                )
                return PostValidationRetryDecision(discard=True)

            _record_gate_report(report, analysis=analysis_obj, context=context)
            decision = _experience_post_validation_decision(report, retry_count=retry_count)
            if decision is None:
                return None

            instruction = decision.instruction
            report_dict = report.to_dict()
            context.metadata.setdefault("gate_retry_reports", []).append(report_dict)
            event = _post_validation_retry_event(
                stage="post_gradient",
                retry_index=retry_count,
                report=report_dict,
                instruction=instruction,
                final_outcome=_post_validation_final_outcome(
                    report,
                    decision=decision,
                    retry_count=retry_count,
                ),
            )
            _record_post_validation_event(
                event,
                context=context,
                analysis=analysis_obj,
                trajectory=trajectory,
            )
            return decision

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=context.request_context,
            context_provider=provider,
            isolation_handler=isolation_handler,
            thinking=True,
            post_validation_hook=post_validation_hook,
            max_post_validation_retries=_EXPERIENCE_POST_VALIDATION_MAX_RETRIES,
        )
        operations, _ = await orchestrator.run()
        _sync_prefetched_comparison_trajectories(provider, trajectory)
        return operations


@replay.component(ExperienceGradientEstimator)
def _current_experience_gradient_estimator() -> ExperienceGradientEstimator:
    return ExperienceGradientEstimator(viking_fs=_REPLAY_VIKING_FS_PLACEHOLDER)


def _sync_prefetched_comparison_trajectories(
    provider: Any,
    trajectory: Trajectory,
) -> list[dict[str, str]]:
    comparison_trajectories = list(
        getattr(provider, "prefetched_comparison_trajectories", []) or []
    )
    if not comparison_trajectories:
        return []
    compact = [
        {
            "uri": str(item.get("uri") or ""),
            "outcome": str(item.get("outcome") or ""),
            "content": str(item.get("content") or ""),
        }
        for item in comparison_trajectories
    ]
    trajectory.metadata["comparison_trajectory_uris"] = [
        item["uri"] for item in compact if item["uri"]
    ]
    trajectory.metadata["comparison_trajectories"] = compact
    return compact


async def _evaluate_experience_gradients(
    *,
    gradients: list[PatchSemanticGradient],
    analysis: RolloutAnalysis,
    experience_set: ExperienceSet,
    semantic_vlm: Any = None,
) -> tuple[list[PatchSemanticGradient], Any]:
    if semantic_vlm is None:
        return list(gradients), GateReport(stage="post_gradient")
    return await _experience_extract_gate_runner(semantic_vlm).filter_gradients(
        list(gradients),
        analyses=[analysis],
        policy_set=experience_set,
    )


async def _evaluate_experience_gradients_with_trace(
    *,
    gradients: list[PatchSemanticGradient],
    analysis: RolloutAnalysis,
    experience_set: ExperienceSet,
    semantic_vlm: Any,
    retry_count: int,
) -> tuple[list[PatchSemanticGradient], GateReport]:
    with tracer.start_as_current_span(
        "train.gradient_estimator.experience.post_validation"
    ) as span:
        span.set_attribute("gate.retry_count", retry_count)
        result, report = await _evaluate_experience_gradients(
            gradients=gradients,
            analysis=analysis,
            experience_set=experience_set,
            semantic_vlm=semantic_vlm,
        )
        _set_post_validation_trace_attributes(span, report)
        return result, report


def _set_post_validation_trace_attributes(span: Any, report: GateReport) -> None:
    span.set_attribute("gate.stage", report.stage)
    span.set_attribute("gate.evaluated_count", report.evaluated_count)
    span.set_attribute("gate.allowed_count", report.allowed_count)
    span.set_attribute("gate.rejected_count", report.rejected_count)
    span.set_attribute("gate.warning_count", report.warning_count)
    if report.rejected_count:
        outcome = "rejected"
    elif report.warning_count:
        outcome = "allowed_with_warning"
    elif report.allowed_count:
        outcome = "allowed"
    else:
        outcome = "empty"
    span.set_attribute("gate.outcome", outcome)
    span.set_attribute("gate.decision_count", len(report.decisions))
    for index, decision in enumerate(report.decisions):
        prefix = f"gate.decision.{index}"
        span.set_attribute(f"{prefix}.name", decision.gate_name)
        span.set_attribute(f"{prefix}.action", decision.action)
        span.set_attribute(f"{prefix}.reason", decision.reason)
        span.set_attribute(f"{prefix}.retriable", decision.retriable)
        span.set_attribute(f"{prefix}.repair_prompt", decision.repair_prompt)
        root_cause_quality = decision.evidence.get("root_cause_quality")
        if root_cause_quality:
            span.set_attribute(f"{prefix}.root_cause_quality", str(root_cause_quality))
        gate_model_reason = decision.evidence.get("gate_model_reason")
        if gate_model_reason:
            span.set_attribute(f"{prefix}.gate_model_reason", str(gate_model_reason))
        authoritative_behavior_anchor = decision.evidence.get("authoritative_behavior_anchor")
        if authoritative_behavior_anchor:
            span.set_attribute(
                f"{prefix}.authoritative_behavior_anchor",
                str(authoritative_behavior_anchor),
            )
        if "anchored_repair" in decision.evidence:
            span.set_attribute(
                f"{prefix}.anchored_repair",
                bool(decision.evidence["anchored_repair"]),
            )


def _analysis_from_context_metadata_optional(
    context: ExperienceGradientContext,
) -> RolloutAnalysis | None:
    value = dict(context.metadata or {}).get("current_analysis")
    return value if isinstance(value, RolloutAnalysis) else None


def _trajectory_or_analysis_metadata(
    trajectory: Trajectory,
    analysis: RolloutAnalysis | None,
    key: str,
) -> str:
    trajectory_metadata = dict(getattr(trajectory, "metadata", {}) or {})
    value = trajectory_metadata.get(key)
    if value:
        return str(value)
    analysis_metadata = (
        dict(getattr(analysis, "metadata", {}) or {}) if analysis is not None else {}
    )
    value = analysis_metadata.get(key)
    if value:
        return str(value)
    rollout = analysis_metadata.get("rollout")
    case = getattr(rollout, "case", None)
    if case is not None:
        if key == "case_name":
            return str(getattr(case, "name", "") or "")
        if key == "task_signature":
            return str(getattr(case, "task_signature", "") or "")
    return ""


def _loaded_experience_uris(analysis: RolloutAnalysis) -> list[str]:
    messages = dict(getattr(analysis, "metadata", {}) or {}).get("rollout_messages") or []
    reminders = extract_injected_experience_reminders(messages)
    return list(
        dict.fromkeys(
            str(reminder.get("experience_uri") or "")
            for reminder in reminders
            if reminder.get("experience_uri")
        )
    )


def _experience_extract_gate_runner(vlm: Any) -> GateRunner:
    return GateRunner(
        gates=[
            ExperienceRootCausePreventionGate(mode="enforce", vlm=vlm),
        ]
    )


def _post_validation_retry_event(
    *,
    stage: str,
    retry_index: int,
    report: dict[str, Any],
    instruction: str,
    final_outcome: str = "retry_requested",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "retry_index": retry_index,
        "evaluated_count": int(report.get("evaluated_count") or 0),
        "allowed_count": int(report.get("allowed_count") or 0),
        "rejected_count": int(report.get("rejected_count") or 0),
        "warning_count": int(report.get("warning_count") or 0),
        "retriable": bool(str(instruction or "").strip()),
        "final_outcome": final_outcome,
        "instruction_preview": _preview_instruction(instruction),
    }


def _experience_post_validation_decision(
    report: GateReport,
    *,
    retry_count: int,
) -> PostValidationRetryDecision | None:
    if report.rejected_count == 0:
        return None
    if report.has_non_retriable_rejection():
        return PostValidationRetryDecision(discard=True)

    instruction = build_gate_retry_instruction(report)
    if not instruction or retry_count >= _EXPERIENCE_POST_VALIDATION_MAX_RETRIES:
        return PostValidationRetryDecision(discard=True)
    return PostValidationRetryDecision(
        retry=True,
        instruction=instruction,
        include_latest_draft=True,
    )


def _post_validation_final_outcome(
    report: GateReport,
    *,
    decision: PostValidationRetryDecision,
    retry_count: int,
) -> str:
    if decision.retry:
        return "retry_requested"
    if report.has_non_retriable_rejection():
        return "discarded_non_retriable"
    if retry_count >= _EXPERIENCE_POST_VALIDATION_MAX_RETRIES:
        return "discarded_after_max_retries"
    return "discarded_without_repair_instruction"


def _post_validation_failure_report(error: Exception) -> GateReport:
    return GateReport(
        stage="post_gradient",
        evaluated_count=1,
        rejected_count=1,
        decisions=[
            GateDecision(
                gate_name="experience_post_validation",
                action="reject",
                reason="experience post-validation failed closed",
                evidence={"error": str(error)},
            )
        ],
    )


def _record_post_validation_event(
    event: dict[str, Any],
    *,
    context: ExperienceGradientContext,
    analysis: RolloutAnalysis | None,
    trajectory: Trajectory,
) -> None:
    context.metadata.setdefault("post_validation_retries", []).append(event)
    if analysis is not None:
        analysis.metadata.setdefault("post_validation_retries", []).append(event)
    trajectory.metadata.setdefault("post_validation_retries", []).append(event)


def _preview_instruction(instruction: str, *, limit: int = 500) -> str:
    text = " ".join(str(instruction or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _record_gate_report(
    report: Any,
    *,
    analysis: RolloutAnalysis | None,
    context: ExperienceGradientContext,
) -> None:
    context.metadata.setdefault("gate_reports", []).append(report.to_dict())
    if analysis is not None:
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


def _merge_diagnostics(target: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    for key, value in diagnostics.items():
        if isinstance(value, list):
            target.setdefault(key, []).extend(value)
        elif isinstance(value, dict):
            target.setdefault(key, {}).update(value)
        else:
            target[key] = value


def _operations_to_gradients(
    *,
    operations: Any,
    trajectory: Trajectory,
    analysis: RolloutAnalysis,
    experience_set: ExperienceSet,
    schema: MemoryTypeSchema,
) -> list[PatchSemanticGradient]:
    gradients: list[PatchSemanticGradient] = []
    content_field_names = schema.content_field_names()
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != "experiences":
            continue
        fields = dict(getattr(op, "memory_fields", {}) or {})
        after_file = render_operation_after_file(op, schema=schema)
        if not any(
            str(
                after_file.content if name == "content" else after_file.extra_fields.get(name) or ""
            ).strip()
            for name in content_field_names
        ):
            continue

        old_file = getattr(op, "old_memory_file_content", None)
        target_uri = first_uri(getattr(op, "uris", []) or [])
        base_version = _base_version(old_file, target_uri, experience_set)

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

    analysis_metadata = (
        dict(getattr(analysis, "metadata", {}) or {}) if analysis is not None else {}
    )
    for key in ("training_category", "category", "case_task_signature", "task_signature"):
        value = analysis_metadata.get(key)
        if value:
            return str(value)

    if trajectory.retrieval_anchor:
        return str(trajectory.retrieval_anchor)
    return str(trajectory.name)


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
