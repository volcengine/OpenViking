# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""ExtractLoop-backed GradientEstimator component."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field, replace
from typing import Any

from openviking.config.vlm import VLMResolver
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.train.components.session_commit import _evaluation_payload
from openviking.session.train.domain import (
    ExperienceSet,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.gradients import PatchSemanticGradient
from openviking.session.train.utils import first_uri, safe_int
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ExperienceGradientContext:
    """Context for ExperienceGradientEstimator."""

    request_context: RequestContext
    # Kept for caller compatibility; raw messages are not an experience input.
    messages: list[Message] = field(default_factory=list)
    strict_extract_errors: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    evaluation: RubricEvaluation | None = None


@dataclass(slots=True)
class ExperienceGradientEstimator:
    """Estimate PatchSemanticGradients via experience ExtractLoop.

    This component reuses AgentExperienceContextProvider and ExtractLoop but stops
    before MemoryUpdater.apply_operations.  The resolved operations are converted
    into PatchSemanticGradient instances.
    """

    viking_fs: Any = None
    vlm: Any = None
    vlm_resolver: VLMResolver | None = None

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

        extract_context = replace(context, messages=[], evaluation=_task_evaluation(analysis))

        async def estimate_one(trajectory: Trajectory) -> list[PatchSemanticGradient]:
            try:
                operations = await self._run_extract_loop(trajectory, extract_context)
            except Exception:
                logger.exception("Experience gradient estimation failed")
                if context.strict_extract_errors:
                    raise
                return []
            if operations is None:
                return []
            return _operations_to_gradients(
                operations=operations,
                trajectory=trajectory,
                analysis=analysis,
                experience_set=experience_set,
            )

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
        vlm_config = None
        if self.vlm is None:
            if self.vlm_resolver is None:
                raise RuntimeError(
                    "ExperienceGradientEstimator requires a VLM resolver for account-owned work"
                )
            vlm_config = await self.vlm_resolver.get_vlm(context.request_context.account_id)
            vlm = vlm_config
        else:
            vlm = self.vlm
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required for experience gradient estimation")

        if vlm_config is None:
            provider = AgentExperienceContextProvider(
                messages=[],
                trajectory_summary=trajectory.content,
                trajectory_uri=trajectory.uri,
                trajectory_case_uris=trajectory.metadata.get("case_uris", []),
                trajectory_source_archive_uri=trajectory.metadata.get("source_archive_uri", ""),
                trajectory_outcome=trajectory.outcome,
                trajectory_task_query=trajectory.metadata.get("task_query", ""),
                trajectory_retrieval_anchor=trajectory.retrieval_anchor,
                trajectory_evaluation=_missing_trajectory_evaluation(
                    trajectory, context.evaluation
                ),
            )
        else:
            provider = AgentExperienceContextProvider(
                messages=[],
                trajectory_summary=trajectory.content,
                trajectory_uri=trajectory.uri,
                trajectory_case_uris=trajectory.metadata.get("case_uris", []),
                trajectory_source_archive_uri=trajectory.metadata.get("source_archive_uri", ""),
                trajectory_outcome=trajectory.outcome,
                trajectory_task_query=trajectory.metadata.get("task_query", ""),
                vlm_config=vlm_config,
                trajectory_retrieval_anchor=trajectory.retrieval_anchor,
                trajectory_evaluation=_missing_trajectory_evaluation(
                    trajectory, context.evaluation
                ),
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

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=context.request_context,
            context_provider=provider,
            isolation_handler=isolation_handler,
            thinking=True,
        )
        operations, _ = await orchestrator.run()
        if operations is not None:
            for op in operations.upsert_operations:
                if op.memory_type != "experiences":
                    continue
                for uri in provider.history_uris:
                    operations.resolved_links.append(
                        StoredLink(
                            from_uri=first_uri(op.uris) or "",
                            to_uri=uri,
                            link_type="derived_from",
                            weight=1.0,
                        )
                    )
        return operations


def _task_evaluation(analysis: RolloutAnalysis) -> RubricEvaluation | None:
    # A rollout-level grade cannot be assigned to separate tasks without explicit scope.
    if len(analysis.trajectories) != 1 or analysis.trajectories[0].metadata.get("case_uris") == []:
        return None
    evaluation = analysis.evaluation
    criteria = evaluation.criterion_results
    if len(criteria) == 1 and (
        (
            criteria[0].criterion_name == "trajectory_extracted"
            and "trajectory_count" in evaluation.metadata
        )
        or (
            criteria[0].criterion_name == "rollout_evaluation_provided"
            and criteria[0].metadata.get("source") == "session_commit_default"
        )
    ):
        return None
    return evaluation


_EVALUATION_SECTION = re.compile(
    r"(?im)^(?:"
    r"<!--[ \t]*POST_TASK_EVALUATION[ \t]*-->[ \t]*\n(?:[ \t]*\n)*##[ \t]+[^\n]+"
    r"|#{1,2}[ \t]+(?:Post[- ](?:task|hoc) Evaluation|事后评测(?:反馈)?|"
    r"(?:OpenViking )?OutcomeEvaluation|Rollout Evaluation)[ \t]*[:：]?[ \t]*"
    r"|\[Rollout Evaluation\]"
    r")[ \t]*\n"
)


def _missing_trajectory_evaluation(
    trajectory: Trajectory, evaluation: RubricEvaluation | None
) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    content = trajectory.content.replace("\r\n", "\n")
    # Mask code examples for heading detection, retaining offsets and real JSON evaluation bodies.
    headings = re.sub(
        r"(?ms)^[ ]{0,3}(`{3,}|~{3,})[^\n]*\n.*?^[ ]{0,3}\1[ \t]*(?=\n|$)",
        lambda match: re.sub(r"[^\n]", " ", match.group()),
        content,
    )
    # New localized records carry a stable marker; known legacy headings remain readable.
    for match in _EVALUATION_SECTION.finditer(headings):
        following = re.search(r"(?m)^#{1,2}[ \t]+", headings[match.end() :])
        end = match.end() + following.start() if following else len(content)
        section = re.sub(r"(?s)<!--.*?-->", "", content[match.end() : end])
        section = re.sub(r"(?m)^[ \t]*(?:[-*+]|`{3,}|~{3,})[ \t]*$", "", section)
        if section.strip():
            return None
    return _evaluation_payload(evaluation)


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
                    ),
                    *[
                        link
                        for link in getattr(operations, "resolved_links", [])
                        if link.link_type == "derived_from"
                        and link.from_uri == (target_uri or "")
                        and link.to_uri != trajectory.uri
                    ],
                ],
                confidence=_confidence(trajectory, analysis),
                metadata={
                    "memory_fields": fields,
                    "uris": list(getattr(op, "uris", []) or []),
                    "trajectory_outcome": trajectory.outcome,
                    "rubric_passed": analysis.evaluation.passed,
                    "supersedes": fields.get("supersedes"),
                    "training_category": _trajectory_training_category(trajectory, analysis),
                    "case_uris": list(trajectory.metadata.get("case_uris", [])),
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
    extra_fields["memory_type"] = "experiences"
    extra_fields["experience_name"] = target_name
    return MemoryFile(
        uri=target_uri,
        content=str(fields.get("content") or ""),
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
