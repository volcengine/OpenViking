# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""GradientEstimator adapter backed by legacy experience extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.train.domain import ExperienceSet, RolloutAnalysis, Trajectory
from openviking.session.train.gradients import ExperienceContentPatch, PatchSemanticGradient
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)


@dataclass(slots=True)
class LegacyExperienceGradientContext:
    """Context for LegacyExperienceGradientEstimator."""

    request_context: RequestContext
    messages: list[Message]
    strict_extract_errors: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LegacyExperienceGradientEstimator:
    """Estimate PatchSemanticGradients via legacy experience ExtractLoop.

    This adapter reuses AgentExperienceContextProvider and ExtractLoop but stops
    before MemoryUpdater.apply_operations.  The legacy ResolvedOperations are
    converted into PatchSemanticGradient instances.
    """

    viking_fs: Any = None
    vlm: Any = None

    @tracer(
        "train.gradient_estimator.legacy_experience.estimate",
        ignore_result=True,
        ignore_args=True,
    )
    async def estimate(
        self,
        analysis: RolloutAnalysis,
        experience_set: ExperienceSet,
        context: LegacyExperienceGradientContext,
    ) -> list[PatchSemanticGradient]:
        if context is None or context.request_context is None:
            raise ValueError("LegacyExperienceGradientContext.request_context is required")

        extract_context = _context_with_analysis_messages(context, analysis)
        gradients: list[PatchSemanticGradient] = []
        for trajectory in analysis.trajectories:
            try:
                operations = await self._run_legacy_extract_loop(trajectory, extract_context)
            except Exception:
                logger.exception("Legacy experience gradient estimation failed")
                if context.strict_extract_errors:
                    raise
                continue
            if operations is None:
                continue
            gradients.extend(
                _operations_to_gradients(
                    operations=operations,
                    trajectory=trajectory,
                    analysis=analysis,
                    experience_set=experience_set,
                )
            )
        return gradients

    @tracer(
        "train.gradient_estimator.legacy_experience.extract_loop",
        ignore_result=True,
        ignore_args=True,
    )
    async def _run_legacy_extract_loop(
        self,
        trajectory: Trajectory,
        context: LegacyExperienceGradientContext,
    ):
        config = get_openviking_config()
        vlm = self.vlm or config.vlm.get_vlm_instance()
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required for legacy experience gradient estimation")

        provider = AgentExperienceContextProvider(
            messages=context.messages,
            trajectory_summary=trajectory.content,
            trajectory_uri=trajectory.uri,
        )
        extract_context = ExtractContext(context.messages)
        isolation_handler = MemoryIsolationHandler(
            context.request_context,
            extract_context,
            allowed_memory_types={"experiences"},
        )
        isolation_handler.prepare_messages()

        provider._isolation_handler = isolation_handler
        provider._ctx = context.request_context
        provider._viking_fs = viking_fs

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=context.request_context,
            context_provider=provider,
            isolation_handler=isolation_handler,
        )
        operations, _ = await orchestrator.run()
        return operations


def _context_with_analysis_messages(
    context: LegacyExperienceGradientContext,
    analysis: RolloutAnalysis,
) -> LegacyExperienceGradientContext:
    messages = analysis.metadata.get("rollout_messages")
    if not messages:
        return context
    return LegacyExperienceGradientContext(
        request_context=context.request_context,
        messages=list(messages),
        strict_extract_errors=context.strict_extract_errors,
        metadata=dict(context.metadata),
    )


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
        after_content = str(fields.get("content") or "")
        if not after_content.strip():
            continue

        old_file = getattr(op, "old_memory_file_content", None)
        before_content = old_file.plain_content() if old_file is not None else None
        target_name = str(fields.get("experience_name") or _fallback_experience_name(op))
        target_uri = _first_uri(getattr(op, "uris", []) or [])
        base_version = _base_version(old_file, target_uri, experience_set)
        supersedes = fields.get("supersedes")

        gradients.append(
            PatchSemanticGradient(
                target_experience_name=target_name,
                target_experience_uri=target_uri,
                base_version=base_version,
                patch=ExperienceContentPatch(
                    before_content=before_content,
                    after_content=after_content,
                    metadata={
                        "supersedes": supersedes,
                    },
                ),
                rationale=(
                    "Legacy ExtractLoop proposed an experience content update "
                    f"from trajectory {trajectory.uri}."
                ),
                evidence_trajectory_uris=[trajectory.uri],
                confidence=_confidence(trajectory, analysis),
                metadata={
                    "legacy_memory_fields": fields,
                    "legacy_uris": list(getattr(op, "uris", []) or []),
                    "trajectory_outcome": trajectory.outcome,
                    "rubric_passed": analysis.evaluation.passed,
                },
            )
        )
    return gradients


def _first_uri(uris: list[str]) -> str | None:
    return uris[0] if uris else None


def _fallback_experience_name(op: Any) -> str:
    uri = _first_uri(getattr(op, "uris", []) or [])
    if uri:
        return uri.rstrip("/").split("/")[-1].removesuffix(".md")
    return "unknown_experience"


def _base_version(
    old_file: Any, target_uri: str | None, experience_set: ExperienceSet
) -> int | None:
    if old_file is not None:
        fields = getattr(old_file, "extra_fields", {}) or {}
        version = _safe_int(fields.get("version"))
        if version is not None:
            return version
    if target_uri:
        for policy in experience_set.policies:
            if policy.uri == target_uri:
                return policy.version
    return None


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
