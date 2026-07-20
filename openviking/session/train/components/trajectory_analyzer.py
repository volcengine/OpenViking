# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RolloutAnalyzer that extracts persistent trajectory memories directly."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
    extract_injected_experience_reminders,
)
from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.extract_loop import PostValidationRetryDecision
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.skill.session_skill_context_provider import (
    SESSION_SKILL_MEMORY_TYPE,
)
from openviking.session.train.components.experience_feedback import (
    record_experience_feedback_stats,
)
from openviking.session.train.domain import (
    CriterionResult,
    Rollout,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
from openviking.session.train.gradients import PatchSemanticGradient
from openviking.session.train.interfaces import RolloutEvaluator
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_TRAJECTORY_MEMORY_TYPE = "trajectories"
_TRAJECTORY_POST_VALIDATION_MAX_RETRIES = 3
_TRAJECTORY_OUTCOMES = {"success", "failure", "partial", "unfinished", "unknown"}
_TRAJECTORY_ANCHOR_RE = re.compile(
    r"^Stage: [^;]+; Boundary: [^;]+; Capability: [^;]+; Target: [^;]+; Outcome: "
    r"(success|failure|partial|unfinished|unknown);?\.?$"
)
_TRAJECTORY_COMMA_ANCHOR_RE = re.compile(
    r"^Stage:\s*(?P<stage>.+?)\s*[,，]\s*Boundary:\s*(?P<boundary>.+?)\s*[,，]\s*"
    r"Capability:\s*(?P<capability>.+?)\s*[,，]\s*Target:\s*(?P<target>.+?)\s*[,，]\s*"
    r"Outcome:\s*(?P<outcome>success|failure|partial|unfinished|unknown)[.;]?\s*$",
    re.IGNORECASE,
)
_TRAJECTORY_REQUIRED_HEADER_LABELS = (
    "Outcome",
    "Domain",
    "User Goal",
    "Injected Experiences",
)
_TRAJECTORY_REQUIRED_SECTIONS = (
    "Key Steps",
    "Evaluation",
    "Result",
)
_TRAJECTORY_STEP_FIELDS = (
    "Boundary",
    "Trigger",
    "Observed facts",
    "Decision",
    "Decision basis",
    "Action",
    "Result",
    "Evidence",
)
_TRAJECTORY_EVALUATION_FIELDS = (
    "Required outcome",
    "Failed requirements",
    "External feedback",
    "Unexplained differences",
)
_TRAJECTORY_REMOVED_DIAGNOSTIC_SECTIONS = (
    "Timeline",
    "Outcome Checks",
    "Correct Work To Preserve",
    "Observed Problem",
    "Evidence References",
    "Raw Evidence",
)


@dataclass(slots=True)
class _TrajectoryValidationIssue:
    target_name: str
    reason: str
    details: str = ""


@dataclass(slots=True)
class TrajectoryAnalyzerContext:
    """Runtime context for TrajectoryRolloutAnalyzer."""

    request_context: RequestContext
    strict_extract_errors: bool = False
    latest_archive_overview: str = ""
    evaluator_context: Any = None
    inject_evaluation_feedback: bool = True
    include_session_skills: bool = False


@dataclass(slots=True)
class TrajectoryRolloutAnalyzer:
    """Analyze rollouts by extracting persistent trajectory memory files.

    This implementation owns the trajectory extraction/apply flow directly.  It
    intentionally does not depend on SessionCompressorV2/V3, and it only exposes
    the trajectory memory schema to ExtractLoop.
    """

    viking_fs: Any = None
    vikingdb: Any = None
    vlm: Any = None
    evaluator: RolloutEvaluator | None = None

    @tracer("train.rollout_analyzer.trajectory.analyze", ignore_result=True, ignore_args=True)
    async def analyze(
        self,
        rollout: Rollout,
        context: TrajectoryAnalyzerContext,
    ) -> RolloutAnalysis:
        if context is None or context.request_context is None:
            raise ValueError("TrajectoryAnalyzerContext.request_context is required")

        evaluation = await self._evaluate_rollout(rollout, context)
        extraction_messages = list(rollout.messages)
        extraction_evaluation = evaluation if context.inject_evaluation_feedback else None
        advisory_signals = _advisory_signals_payload(
            rollout,
            extraction_evaluation,
        )
        evidence_sources = _evidence_sources_payload(rollout, extraction_evaluation)
        result = await self.extract_trajectory_memories(
            messages=extraction_messages,
            ctx=context.request_context,
            strict_extract_errors=context.strict_extract_errors,
            latest_archive_overview=context.latest_archive_overview,
            include_session_skills=context.include_session_skills,
            case_name=getattr(rollout.case, "name", ""),
            evidence_sources=evidence_sources,
            advisory_signals=advisory_signals,
        )
        contexts = list((result or {}).get("contexts", []))
        skill_gradients = list((result or {}).get("skill_gradients", []))
        trajectory_retry_events = list((result or {}).get("trajectory_post_validation_retries", []))
        trajectory_uris = [
            item.uri
            for item in contexts
            if getattr(item, "category", "") == "memory_write"
            and "/memories/trajectories/" in getattr(item, "uri", "")
        ]
        trajectory_uris = list(dict.fromkeys(trajectory_uris))
        trajectories = await self._read_trajectories(
            trajectory_uris,
            ctx=context.request_context,
        )
        experience_feedback_stats = await self._record_experience_feedback_stats(
            trajectories=trajectories,
            messages=extraction_messages,
            ctx=context.request_context,
        )
        evaluation = evaluation or _evaluation_from_trajectories(trajectories)
        return RolloutAnalysis(
            evaluation=evaluation,
            trajectories=trajectories,
            gradients=skill_gradients,
            metadata={
                "context_count": len(contexts),
                "policy_snapshot_id": rollout.policy_snapshot_id,
                "rollout": rollout,
                "rollout_messages": rollout.messages,
                "case_name": getattr(rollout.case, "name", ""),
                "task_signature": getattr(rollout.case, "task_signature", ""),
                "extraction_message_count": len(extraction_messages),
                "evidence_source_summary": {
                    "direct_available": bool(evidence_sources.get("direct_available")),
                    "source_count": len(evidence_sources.get("items") or []),
                    "advisory_signal_count": len(advisory_signals.get("items") or []),
                },
                "trajectory_post_validation_retries": trajectory_retry_events,
                "experience_feedback_stats": experience_feedback_stats,
            },
        )

    async def _evaluate_rollout(
        self,
        rollout: Rollout,
        context: TrajectoryAnalyzerContext,
    ) -> RubricEvaluation | None:
        if rollout.evaluation is not None:
            return rollout.evaluation
        if self.evaluator is None:
            return None
        return await self.evaluator.evaluate(rollout, context.evaluator_context)

    async def extract_trajectory_memories(
        self,
        *,
        messages: list[Message],
        ctx: RequestContext | None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        include_session_skills: bool = False,
        case_name: str = "",
        evidence_sources: dict[str, Any] | None = None,
        advisory_signals: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract and persist trajectory memories from rollout messages.

        When ``include_session_skills`` is True, session skill patches are
        co-extracted in the same ExtractLoop pass and returned as
        ``PatchSemanticGradient`` instances in the ``"skill_gradients"`` key.
        Skill patches are *not* applied to disk by this method — they are
        returned as gradient signals for downstream policy training.
        """
        empty_result: dict[str, Any] = {
            "contexts": [],
            "skill_gradients": [],
            "trajectory_post_validation_retries": [],
        }
        if not messages or ctx is None:
            return empty_result

        provider = AgentTrajectoryContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            include_trajectories=True,
            include_session_skills=include_session_skills,
            evidence_sources=evidence_sources,
            advisory_signals=advisory_signals,
        )
        phase_result = await self._run_trajectory_extract_phase(
            provider=provider,
            messages=messages,
            ctx=ctx,
            strict_extract_errors=strict_extract_errors,
            include_session_skills=include_session_skills,
            case_name=case_name,
            evidence_sources=evidence_sources,
        )
        if phase_result is None:
            return empty_result

        _, _, contexts, skill_gradients, retry_events = phase_result
        return {
            "contexts": contexts,
            "skill_gradients": skill_gradients,
            "trajectory_post_validation_retries": retry_events,
        }

    async def _run_trajectory_extract_phase(
        self,
        *,
        provider: AgentTrajectoryContextProvider,
        messages: list[Message],
        ctx: RequestContext,
        strict_extract_errors: bool,
        include_session_skills: bool = False,
        case_name: str = "",
        evidence_sources: dict[str, Any] | None = None,
    ) -> (
        tuple[
            list[str],
            list[str],
            list[Context],
            list[PatchSemanticGradient],
            list[dict[str, Any]],
        ]
        | None
    ):
        config = get_openviking_config()
        vlm = self.vlm or config.vlm.get_vlm_instance()
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to extract trajectory memories")

        extract_context = provider.get_extract_context()
        allowed_types: set[str] = {_TRAJECTORY_MEMORY_TYPE}
        if include_session_skills:
            allowed_types.add(SESSION_SKILL_MEMORY_TYPE)
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types=allowed_types,
        )
        isolation_handler.prepare_messages()

        provider._isolation_handler = isolation_handler
        provider._ctx = ctx
        provider._viking_fs = viking_fs
        validation_events: list[dict[str, Any]] = []

        async def post_validation_hook(
            operations: Any,
            retry_count: int,
            *,
            messages: list[dict[str, Any]] | None = None,
            latest_draft: Any = None,
        ):
            _ensure_trajectory_case_name(operations, case_name=case_name)
            issues = _trajectory_validation_issues(
                operations,
                evidence_sources=evidence_sources,
            )
            if not issues:
                if retry_count:
                    validation_events.append(
                        {
                            "retry_index": retry_count,
                            "final_outcome": "passed_after_retry",
                            "issues": [],
                        }
                    )
                return None
            final_outcome = (
                "discarded_after_max_retries"
                if retry_count >= _TRAJECTORY_POST_VALIDATION_MAX_RETRIES
                else "retry_requested"
            )
            validation_events.append(
                {
                    "retry_index": retry_count,
                    "final_outcome": final_outcome,
                    "issues": [
                        {
                            "target_name": issue.target_name,
                            "reason": issue.reason,
                            "details": issue.details,
                        }
                        for issue in issues
                    ],
                }
            )
            if retry_count >= _TRAJECTORY_POST_VALIDATION_MAX_RETRIES:
                return PostValidationRetryDecision(discard=True)
            return PostValidationRetryDecision(
                retry=True,
                instruction=_trajectory_validation_retry_instruction(issues),
                include_latest_draft=True,
            )

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=provider,
            isolation_handler=isolation_handler,
            thinking=True,
            post_validation_hook=post_validation_hook,
            max_post_validation_retries=_TRAJECTORY_POST_VALIDATION_MAX_RETRIES,
        )

        try:
            provider._transaction_handle = None
            orchestrator._transaction_handle = None
            operations, _ = await orchestrator.run()
            if operations is None:
                tracer.info("[trajectory] No memory operations generated")
                if not validation_events:
                    validation_events.append(
                        {
                            "retry_index": 0,
                            "final_outcome": "no_operation_generated",
                            "issues": [],
                        }
                    )
                return [], [], [], [], validation_events

            _log_operations(operations)

            # Split operations into trajectory (applied to disk) and skill
            # (returned as gradients).  Skill ops are *not* written here —
            # they flow through the patch-merge trainer.
            traj_ops, skill_ops = _split_operations_by_type(
                operations, target_type=_TRAJECTORY_MEMORY_TYPE
            )
            traj_ops = _filter_invalid_trajectory_operations(
                traj_ops,
                evidence_sources=evidence_sources,
            )
            skill_gradients = _skill_operations_to_gradients(
                skill_ops,
                viking_fs=viking_fs,
                ctx=ctx,
            )

            _ensure_trajectory_case_name(traj_ops, case_name=case_name)

            memory_result = await self._apply_trajectory_operations(
                operations=traj_ops,
                provider=provider,
                ctx=ctx,
                extract_context=extract_context,
                isolation_handler=isolation_handler,
            )
            tracer.info(
                "[trajectory] Applied memory ops: "
                f"written={len(memory_result.written_uris)}, "
                f"edited={len(memory_result.edited_uris)}, "
                f"deleted={len(memory_result.deleted_uris)}, "
                f"errors={len(memory_result.errors)}"
            )
            contexts = _contexts_from_memory_result(memory_result)
            return (
                list(memory_result.written_uris),
                list(memory_result.edited_uris),
                contexts,
                skill_gradients,
                validation_events,
            )
        except Exception as exc:
            logger.error("[trajectory] Failed to extract: %s", exc, exc_info=True)
            if strict_extract_errors:
                raise
            validation_events.append(
                {
                    "retry_index": len(validation_events),
                    "final_outcome": "extraction_error",
                    "issues": [
                        {
                            "target_name": case_name,
                            "reason": type(exc).__name__,
                            "details": str(exc),
                        }
                    ],
                }
            )
            return [], [], [], [], validation_events

    async def _apply_trajectory_operations(
        self,
        *,
        operations: ResolvedOperations,
        provider: AgentTrajectoryContextProvider,
        ctx: RequestContext,
        extract_context: ExtractContext,
        isolation_handler: MemoryIsolationHandler,
    ) -> MemoryUpdateResult:
        updater = MemoryUpdater(
            registry=provider._get_registry(),
            vikingdb=self.vikingdb,
            transaction_handle=None,
        )
        updater._viking_fs = self.viking_fs or get_viking_fs()
        return await updater.apply_operations(
            operations,
            ctx,
            extract_context=extract_context,
            isolation_handler=isolation_handler,
        )

    @tracer(
        "train.rollout_analyzer.trajectory.read_trajectories", ignore_result=True, ignore_args=True
    )
    async def _read_trajectories(
        self,
        trajectory_uris: list[str],
        *,
        ctx: RequestContext,
    ) -> list[Trajectory]:
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to read extracted trajectories")

        trajectories: list[Trajectory] = []
        for uri in dict.fromkeys(trajectory_uris):
            try:
                raw = await viking_fs.read_file(uri, ctx=ctx) or ""
                mf = MemoryFileUtils.read(raw, uri=uri)
            except Exception as exc:
                logger.warning("Failed to read trajectory %s: %s", uri, exc)
                continue
            fields = dict(mf.extra_fields or {})
            name = str(
                fields.get("trajectory_name") or uri.rstrip("/").split("/")[-1].removesuffix(".md")
            )
            outcome = str(fields.get("outcome") or "unknown")
            retrieval_anchor = str(fields.get("retrieval_anchor") or "")
            case_name = str(fields.get("case_name") or "")
            metadata = dict(fields)
            metadata.setdefault("memory_type", mf.memory_type or fields.get("memory_type"))
            metadata.setdefault("case_name", case_name)
            trajectories.append(
                Trajectory(
                    name=name,
                    uri=uri,
                    content=mf.plain_content(),
                    outcome=outcome,
                    retrieval_anchor=retrieval_anchor,
                    metadata=metadata,
                )
            )
        return trajectories

    async def _record_experience_feedback_stats(
        self,
        *,
        trajectories: list[Trajectory],
        messages: list[Message],
        ctx: RequestContext,
    ) -> dict[str, Any]:
        injected_reminders = extract_injected_experience_reminders(messages)
        if not trajectories or not injected_reminders:
            return {"updated_uris": [], "skipped_uris": [], "errors": []}
        try:
            result = await record_experience_feedback_stats(
                trajectories=trajectories,
                injected_reminders=injected_reminders,
                viking_fs=self.viking_fs or get_viking_fs(),
                ctx=ctx,
            )
        except Exception as exc:  # pragma: no cover - defensive; stats must not fail analysis
            logger.warning("Failed to record experience feedback stats: %s", exc, exc_info=True)
            return {"updated_uris": [], "skipped_uris": [], "errors": [str(exc)]}
        return {
            "updated_uris": list(result.updated_uris),
            "skipped_uris": list(result.skipped_uris),
            "errors": list(result.errors),
        }


def _trajectory_validation_issues(
    operations: Any,
    *,
    evidence_sources: dict[str, Any] | None = None,
) -> list[_TrajectoryValidationIssue]:
    issues: list[_TrajectoryValidationIssue] = []
    trajectory_operations = [
        op
        for op in getattr(operations, "upsert_operations", []) or []
        if getattr(op, "memory_type", None) == _TRAJECTORY_MEMORY_TYPE
    ]
    if len(trajectory_operations) != 1:
        issues.append(
            _TrajectoryValidationIssue(
                target_name="trajectory_batch",
                reason="trajectory extraction must produce exactly one trajectory",
                details=f"count={len(trajectory_operations)}",
            )
        )
    for op in trajectory_operations:
        raw_fields = getattr(op, "memory_fields", {}) or {}
        if isinstance(raw_fields, dict):
            _normalize_trajectory_retrieval_anchor(raw_fields)
        fields = dict(raw_fields)
        name = str(fields.get("trajectory_name") or _fallback_trajectory_name(op))
        issues.extend(
            _trajectory_operation_validation_issues(
                name,
                fields,
                evidence_sources=evidence_sources,
            )
        )
    return issues


def _normalize_trajectory_retrieval_anchor(fields: dict[str, Any]) -> None:
    """Canonicalize an otherwise valid label-ordered anchor before validation."""

    anchor = str(fields.get("retrieval_anchor") or "").strip()
    if not anchor or _TRAJECTORY_ANCHOR_RE.fullmatch(anchor):
        return
    match = _TRAJECTORY_COMMA_ANCHOR_RE.fullmatch(anchor)
    if match is None:
        return
    values = {key: value.strip() for key, value in match.groupdict().items()}
    fields["retrieval_anchor"] = (
        f"Stage: {values['stage']}; Boundary: {values['boundary']}; "
        f"Capability: {values['capability']}; Target: {values['target']}; "
        f"Outcome: {values['outcome'].lower()}."
    )


def _trajectory_operation_validation_issues(
    target_name: str,
    fields: dict[str, Any],
    *,
    evidence_sources: dict[str, Any] | None = None,
) -> list[_TrajectoryValidationIssue]:
    issues: list[_TrajectoryValidationIssue] = []
    required_fields = (
        "trajectory_name",
        "outcome",
        "retrieval_anchor",
        "experience_effects",
        "content",
    )
    missing_fields = [name for name in required_fields if not str(fields.get(name) or "").strip()]
    if missing_fields:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory is missing required fields",
                details=", ".join(missing_fields),
            )
        )

    outcome = str(fields.get("outcome") or "").strip().lower()
    if outcome and outcome not in _TRAJECTORY_OUTCOMES:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory outcome is invalid",
                details=outcome,
            )
        )
    direct_evaluation = _direct_rollout_evaluation(evidence_sources)
    if direct_evaluation is not None:
        evaluation_passed = bool(direct_evaluation.get("passed"))
        if evaluation_passed and outcome and outcome != "success":
            issues.append(
                _TrajectoryValidationIssue(
                    target_name=target_name,
                    reason="trajectory outcome disagrees with direct evaluation",
                    details=f"evaluation_passed=true, outcome={outcome}",
                )
            )
        elif not evaluation_passed and outcome == "success":
            issues.append(
                _TrajectoryValidationIssue(
                    target_name=target_name,
                    reason="trajectory outcome disagrees with direct evaluation",
                    details="evaluation_passed=false, outcome=success",
                )
            )

    content = str(fields.get("content") or "")
    content_outcome_match = re.search(r"(?m)^-\s+Outcome\s*:\s*([^\n]+)", content)
    if outcome and content_outcome_match:
        content_outcome = content_outcome_match.group(1).strip().lower()
        if content_outcome != outcome:
            issues.append(
                _TrajectoryValidationIssue(
                    target_name=target_name,
                    reason="trajectory outcome disagrees with content outcome",
                    details=f"field={outcome}, content={content_outcome}",
                )
            )

    anchor = str(fields.get("retrieval_anchor") or "").strip()
    if anchor and not _TRAJECTORY_ANCHOR_RE.fullmatch(anchor):
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory retrieval_anchor has invalid structure",
                details=anchor[:300],
            )
        )
    elif anchor and outcome:
        anchor_outcome = re.search(r"; Outcome: ([a-z]+)\.?$", anchor)
        if anchor_outcome and anchor_outcome.group(1) != outcome:
            issues.append(
                _TrajectoryValidationIssue(
                    target_name=target_name,
                    reason="trajectory outcome disagrees with retrieval anchor",
                    details=f"field={outcome}, anchor={anchor_outcome.group(1)}",
                )
            )

    effects = str(fields.get("experience_effects") or "").strip()
    if effects:
        issues.extend(_experience_effects_validation_issues(target_name, effects))

    issues.extend(_trajectory_content_validation_issues(target_name, content))
    issues.extend(
        _trajectory_evidence_validation_issues(
            target_name,
            content,
            evidence_sources,
        )
    )
    return issues


def _experience_effects_validation_issues(
    target_name: str,
    raw: str,
) -> list[_TrajectoryValidationIssue]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory experience_effects is not valid JSON",
                details=str(exc),
            )
        ]
    expected = {"positive_ids", "negative_ids", "weak_ids"}
    if not isinstance(value, dict) or set(value) != expected:
        return [
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory experience_effects has invalid keys",
                details=f"expected={sorted(expected)}",
            )
        ]
    invalid = [
        key
        for key in sorted(expected)
        if not isinstance(value.get(key), list)
        or any(
            not isinstance(item, str) or not re.fullmatch(r"E[1-9]\d*", item) for item in value[key]
        )
    ]
    if not invalid:
        return []
    return [
        _TrajectoryValidationIssue(
            target_name=target_name,
            reason="trajectory experience_effects IDs are invalid",
            details=", ".join(invalid),
        )
    ]


def _trajectory_content_validation_issues(
    target_name: str,
    content: str,
) -> list[_TrajectoryValidationIssue]:
    issues: list[_TrajectoryValidationIssue] = []
    if not content.strip():
        return [
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory content is empty",
            )
        ]
    if not re.search(r"(?m)^#\s+\S", content):
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory content is missing its title",
            )
        )
    missing_labels = [
        label
        for label in _TRAJECTORY_REQUIRED_HEADER_LABELS
        if not re.search(rf"(?m)^-\s+{re.escape(label)}\s*:", content)
    ]
    missing_labels.extend(
        label
        for label in _TRAJECTORY_REQUIRED_SECTIONS
        if not re.search(rf"(?m)^##\s+{re.escape(label)}\s*$", content)
    )
    if missing_labels:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory content is missing required sections",
                details=", ".join(missing_labels),
            )
        )
    duplicate_labels = [
        label
        for label in _TRAJECTORY_REQUIRED_HEADER_LABELS
        if len(re.findall(rf"(?m)^-\s+{re.escape(label)}\s*:", content)) > 1
    ]
    duplicate_labels.extend(
        label
        for label in _TRAJECTORY_REQUIRED_SECTIONS
        if len(re.findall(rf"(?m)^##\s+{re.escape(label)}\s*$", content)) > 1
    )
    if duplicate_labels:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory content repeats required sections",
                details=", ".join(duplicate_labels),
            )
        )
    top_level_sections = [
        label.strip() for label in re.findall(r"(?m)^##\s+([^\n]+?)\s*$", content)
    ]
    unexpected_sections = [
        label for label in top_level_sections if label not in _TRAJECTORY_REQUIRED_SECTIONS
    ]
    if unexpected_sections:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory content contains unexpected top-level sections",
                details=", ".join(unexpected_sections),
            )
        )
    removed_sections = [
        label
        for label in _TRAJECTORY_REMOVED_DIAGNOSTIC_SECTIONS
        if re.search(rf"(?mi)^\s*(?:##\s+|-\s+){re.escape(label)}\s*:", content)
    ]
    if removed_sections:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory content contains removed diagnostic sections",
                details=", ".join(removed_sections),
            )
        )
    forbidden_patterns = (
        ("Counterfactual Ideal Experience", r"(?mi)^\s*-?\s*Counterfactual Ideal Experience\s*:"),
        ("Runtime experience content", r"(?mi)^\s*-?\s*Runtime experience content\s*:"),
        ("Experience Repair Signal", r"(?mi)^\s*-?\s*Experience Repair Signal\s*:"),
        ("Diagnostic Hints", r"(?mi)^\s*-?\s*Diagnostic Hints\s*:"),
        ("Ambiguous references", r"(?mi)^\s*-\s*Ambiguous references\s*:"),
        ("Recommended operation", r"(?mi)^\s*-\s*Recommended operation\s*:"),
        ("Selected candidate", r"(?mi)^\s*-\s*Selected candidate\s*:"),
        ("Candidate Cx", r"(?mi)^\s*-\s*Candidate\s+C\d+\s*:"),
    )
    found = [name for name, pattern in forbidden_patterns if re.search(pattern, content or "")]
    if found:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory contains experience-generation sections",
                details=", ".join(found),
            )
        )
    issues.extend(_trajectory_key_step_validation_issues(target_name, content))
    issues.extend(_trajectory_evaluation_validation_issues(target_name, content))
    return issues


def _trajectory_key_step_validation_issues(
    target_name: str,
    content: str,
) -> list[_TrajectoryValidationIssue]:
    section_match = re.search(
        r"(?ms)^##\s+Key Steps\s*$\n(?P<body>.*?)(?=^##\s+Evaluation\s*$)",
        content,
    )
    if section_match is None:
        return []
    body = section_match.group("body")
    step_matches = list(re.finditer(r"(?m)^###\s+Step\s+(?P<number>\d+)\s*$", body))
    if not step_matches:
        return [
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory content has no key steps",
            )
        ]

    issues: list[_TrajectoryValidationIssue] = []
    numbers = [int(match.group("number")) for match in step_matches]
    if numbers != list(range(1, len(numbers) + 1)):
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory key step numbers are not consecutive",
                details=f"found={numbers}",
            )
        )

    missing: list[str] = []
    duplicate: list[str] = []
    out_of_order: list[str] = []
    unexpected: list[str] = []
    for index, step_match in enumerate(step_matches):
        step_number = step_match.group("number")
        block_end = step_matches[index + 1].start() if index + 1 < len(step_matches) else len(body)
        block = body[step_match.end() : block_end]
        first_positions: list[int] = []
        complete = True
        for field in _TRAJECTORY_STEP_FIELDS:
            matches = list(re.finditer(rf"(?m)^-\s+{re.escape(field)}\s*:\s*(.*)$", block))
            if not matches or not matches[0].group(1).strip():
                missing.append(f"Step {step_number}: {field}")
                complete = False
                continue
            if len(matches) > 1:
                duplicate.append(f"Step {step_number}: {field}")
            first_positions.append(matches[0].start())
        if complete and first_positions != sorted(first_positions):
            out_of_order.append(f"Step {step_number}")
        field_names = [field.strip() for field in re.findall(r"(?m)^-\s+([^:\n]+?)\s*:", block)]
        unexpected.extend(
            f"Step {step_number}: {field}"
            for field in field_names
            if field not in _TRAJECTORY_STEP_FIELDS
        )

    if missing:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory key steps are missing required fields",
                details=", ".join(missing),
            )
        )
    if duplicate:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory key steps repeat required fields",
                details=", ".join(duplicate),
            )
        )
    if out_of_order:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory key step fields are out of order",
                details=", ".join(out_of_order),
            )
        )
    if unexpected:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory key steps contain unexpected fields",
                details=", ".join(unexpected),
            )
        )
    return issues


def _trajectory_evaluation_validation_issues(
    target_name: str,
    content: str,
) -> list[_TrajectoryValidationIssue]:
    section_match = re.search(
        r"(?ms)^##\s+Evaluation\s*$\n(?P<body>.*?)(?=^##\s+Result\s*$)",
        content,
    )
    if section_match is None:
        return []
    body = section_match.group("body")
    missing: list[str] = []
    duplicate: list[str] = []
    positions: list[int] = []
    for field in _TRAJECTORY_EVALUATION_FIELDS:
        matches = list(re.finditer(rf"(?m)^-\s+{re.escape(field)}\s*:\s*(.*)$", body))
        if not matches or not matches[0].group(1).strip():
            missing.append(field)
            continue
        if len(matches) > 1:
            duplicate.append(field)
        positions.append(matches[0].start())

    issues: list[_TrajectoryValidationIssue] = []
    if missing:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory evaluation is missing required fields",
                details=", ".join(missing),
            )
        )
    if duplicate:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory evaluation repeats required fields",
                details=", ".join(duplicate),
            )
        )
    if not missing and positions != sorted(positions):
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory evaluation fields are out of order",
            )
        )
    field_names = [field.strip() for field in re.findall(r"(?m)^-\s+([^:\n]+?)\s*:", body)]
    unexpected = [field for field in field_names if field not in _TRAJECTORY_EVALUATION_FIELDS]
    if unexpected:
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory evaluation contains unexpected fields",
                details=", ".join(unexpected),
            )
        )
    return issues


def _trajectory_evidence_validation_issues(
    target_name: str,
    content: str,
    evidence_sources: dict[str, Any] | None,
) -> list[_TrajectoryValidationIssue]:
    issues: list[_TrajectoryValidationIssue] = []
    direct_available = bool((evidence_sources or {}).get("direct_available"))
    runtime_grounded = any(
        _has_substantive_evidence(value)
        for value in re.findall(r"(?m)^-\s+Evidence\s*:\s*(.*)$", content)
    )
    external_feedback_match = re.search(
        r"(?m)^-\s+External feedback\s*:\s*(.*)$",
        content,
    )
    external_feedback = external_feedback_match.group(1).strip() if external_feedback_match else ""
    external_grounded = direct_available and _has_substantive_evidence(external_feedback)
    if not direct_available and _has_substantive_evidence(external_feedback):
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory claims direct external evidence when none was supplied",
                details=external_feedback[:300],
            )
        )
    if _has_material_failure_claim(content) and not (runtime_grounded or external_grounded):
        issues.append(
            _TrajectoryValidationIssue(
                target_name=target_name,
                reason="trajectory material failure claim lacks direct evidence",
                details="advisory signals cannot serve as proof",
            )
        )
    return issues


def _direct_rollout_evaluation(
    evidence_sources: dict[str, Any] | None,
) -> dict[str, Any] | None:
    for item in (evidence_sources or {}).get("items", []) or []:
        if (
            isinstance(item, dict)
            and item.get("direct") is True
            and item.get("source") == "rollout_evaluation"
        ):
            return item
    return None


def _has_substantive_evidence(value: str) -> bool:
    normalized = str(value or "").strip().lower().rstrip(".")
    return normalized not in {
        "",
        "none",
        "unknown",
        "unavailable",
        "unverified",
        "none/unknown",
        "无",
        "未知",
        "不可用",
        "未验证",
    }


def _has_material_failure_claim(content: str) -> bool:
    if re.search(
        r"(?mi)^\s*-\s+Required outcome\s*:\s*(?:failed|partial)\b",
        content,
    ):
        return True
    match = re.search(r"(?mi)^\s*-\s+Failed requirements\s*:\s*([^\n]+)", content)
    return bool(
        match
        and match.group(1).strip().lower() not in {"none", "unknown", "none/unknown", "无", "未知"}
    )


def _trajectory_validation_retry_instruction(issues: list[_TrajectoryValidationIssue]) -> str:
    detail_lines = [
        f"- target={issue.target_name}: {issue.reason}"
        + (f" ({issue.details})" if issue.details else "")
        for issue in issues
    ]
    return "\n".join(
        [
            "Your previous trajectory extraction was rejected by training validation.",
            "Retry once. Regenerate the complete trajectory memory operations as factual execution records only; do not add unrelated memories.",
            "",
            "Invalid trajectory content:",
            *detail_lines,
            "",
            "Required repair:",
            "- Do not output Counterfactual Ideal Experience, Runtime experience content, Experience Repair Signal, Diagnostic Hints, Recommended operation, Selected candidate, or C1/C2/C3 sections.",
            "- Do not output Timeline, Outcome Checks, Correct Work To Preserve, Observed Problem, Evidence References, or Raw Evidence sections.",
            "- Record observed execution facts only in normalized Key Steps, followed by factual Evaluation and Result sections.",
            "- Every Step must include Boundary, Trigger, Observed facts, Decision, Decision basis, Action, Result, and Evidence exactly once in that order. Use none or unknown rather than omitting a field.",
            "- Include every required field and section from the trajectory schema. Use the exact retrieval_anchor and experience_effects formats.",
            "- Treat Advisory Signals as hints only. Never present them as runtime observations or causal facts; write unknown when runtime or direct external evidence does not support a cause.",
            "Output ONLY the complete JSON object as an instance of OUTPUT_SCHEMA; "
            "do not output the OUTPUT_SCHEMA definition itself.",
        ]
    )


def _filter_invalid_trajectory_operations(
    operations: ResolvedOperations,
    *,
    evidence_sources: dict[str, Any] | None = None,
) -> ResolvedOperations:
    valid_upserts = []
    rejected_names: list[str] = []
    for op in operations.upsert_operations or []:
        if op.memory_type != _TRAJECTORY_MEMORY_TYPE:
            valid_upserts.append(op)
            continue
        fields = dict(op.memory_fields or {})
        name = str(fields.get("trajectory_name") or _fallback_trajectory_name(op))
        issues = _trajectory_operation_validation_issues(
            name,
            fields,
            evidence_sources=evidence_sources,
        )
        if issues:
            rejected_names.append(name)
            continue
        valid_upserts.append(op)
    if rejected_names:
        tracer.info(
            f"[trajectory] Dropped invalid trajectory ops after validation retry: {rejected_names}"
        )
    return operations.model_copy(update={"upsert_operations": valid_upserts})


def _fallback_trajectory_name(op: Any) -> str:
    uri = (getattr(op, "uris", None) or [""])[0]
    return str(uri).rstrip("/").split("/")[-1].removesuffix(".md") or "unknown_trajectory"


def _log_operations(operations: ResolvedOperations) -> None:
    op_items = [
        f"{op.memory_type}(uris={op.uris!r})" for op in getattr(operations, "upsert_operations", [])
    ]
    delete_uris = [dc.uri for dc in getattr(operations, "delete_file_contents", [])]
    tracer.info(f"[trajectory] LLM operations: ops={op_items}, delete_uris={delete_uris}")


def _contexts_from_memory_result(memory_result: MemoryUpdateResult) -> list[Context]:
    contexts: list[Context] = []
    for uri in memory_result.written_uris:
        contexts.append(Context(uri=uri, category="memory_write", context_type="memory"))
    for uri in memory_result.edited_uris:
        contexts.append(Context(uri=uri, category="memory_edit", context_type="memory"))
    for uri in memory_result.deleted_uris:
        contexts.append(Context(uri=uri, category="memory_delete", context_type="memory"))
    return contexts


def _evaluation_from_trajectories(trajectories: list[Trajectory]) -> RubricEvaluation:
    passed = bool(trajectories)
    return RubricEvaluation(
        passed=passed,
        score=1.0 if passed else 0.0,
        criterion_results=[
            CriterionResult(
                criterion_name="trajectory_extracted",
                passed=passed,
                score=1.0 if passed else 0.0,
                feedback=[] if passed else ["No trajectory was extracted from the rollout."],
                evidence=[trajectory.uri for trajectory in trajectories],
            )
        ],
        feedback=[] if passed else ["No trajectory was extracted from the rollout."],
        metadata={"trajectory_count": len(trajectories)},
    )


def _ensure_trajectory_case_name(
    operations: ResolvedOperations,
    *,
    case_name: str,
) -> None:
    case_name = str(case_name or "").strip()
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != _TRAJECTORY_MEMORY_TYPE:
            continue
        fields = getattr(op, "memory_fields", None)
        if isinstance(fields, dict):
            if case_name:
                fields["case_name"] = case_name


def _evidence_sources_payload(
    rollout: Rollout,
    evaluation: RubricEvaluation | None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if evaluation is not None:
        items.append(
            {
                "source": "rollout_evaluation",
                "kind": "outcome_evaluation",
                "direct": True,
                "scope": "outcome_and_requirement_compliance",
                "passed": bool(evaluation.passed),
                "score": float(evaluation.score),
                "feedback": list(evaluation.feedback),
                "criterion_results": [
                    {
                        "criterion_name": item.criterion_name,
                        "passed": bool(item.passed),
                        "score": float(item.score),
                        "feedback": list(item.feedback),
                        "evidence": list(item.evidence),
                    }
                    for item in evaluation.criterion_results
                ],
                "contract": (
                    "Authoritative feedback for outcome and requirement compliance. "
                    "It does not independently prove an unobserved internal cause."
                ),
            }
        )
    sources = (
        ("rollout_metadata", dict(rollout.metadata or {})),
        ("evaluation_metadata", dict(evaluation.metadata or {}) if evaluation else {}),
    )
    for container_name, metadata in sources:
        raw_sources = metadata.get("evidence_sources")
        if isinstance(raw_sources, dict):
            raw_sources = [raw_sources]
        if not isinstance(raw_sources, list):
            continue
        for raw in raw_sources:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item.setdefault("container", container_name)
            item["direct"] = bool(item.get("direct"))
            items.append(item)
    return {
        "direct_available": any(item["direct"] for item in items),
        "items": items,
        "contract": (
            "Only items with direct=true may prove a material claim. "
            "Other items may only identify what to inspect."
        ),
    }


def _advisory_signals_payload(
    rollout: Rollout,
    evaluation: RubricEvaluation | None,
) -> dict[str, Any]:
    items: list[Any] = []
    sources = (
        dict(rollout.metadata or {}),
        dict(evaluation.metadata or {}) if evaluation else {},
    )
    for metadata in sources:
        raw = metadata.get("advisory_signals")
        if isinstance(raw, list):
            items.extend(raw)
        elif raw is not None:
            items.append(raw)
    return {"available": bool(items), "items": items}


def _split_operations_by_type(
    operations: ResolvedOperations, *, target_type: str
) -> tuple[ResolvedOperations, ResolvedOperations]:
    """Split operations into (target_type_ops, other_ops)."""
    target_upserts = [op for op in operations.upsert_operations if op.memory_type == target_type]
    other_upserts = [op for op in operations.upsert_operations if op.memory_type != target_type]
    target_deletes = [dc for dc in operations.delete_file_contents if dc.memory_type == target_type]
    other_deletes = [dc for dc in operations.delete_file_contents if dc.memory_type != target_type]
    target_ops = ResolvedOperations(
        upsert_operations=target_upserts,
        delete_file_contents=target_deletes,
        errors=list(operations.errors),
        resolved_links=[
            link
            for link in operations.resolved_links
            if getattr(link, "from_uri", "").endswith("/trajectories/")
            or target_type in getattr(link, "from_uri", "")
        ],
    )
    other_ops = ResolvedOperations(
        upsert_operations=other_upserts,
        delete_file_contents=other_deletes,
        errors=[],
        resolved_links=[],
    )
    return target_ops, other_ops


def _skill_operations_to_gradients(
    operations: ResolvedOperations,
    *,
    viking_fs: Any = None,
    ctx: Any = None,
) -> list[PatchSemanticGradient]:
    """Convert skill ResolvedOperations to PatchSemanticGradient instances.

    The resulting gradients carry the full proposed skill content in their
    ``after_file`` so the patch-merge optimizer can reconcile multiple
    proposals against the current policy set.
    """
    gradients: list[PatchSemanticGradient] = []
    for op in operations.upsert_operations or []:
        if op.memory_type != SESSION_SKILL_MEMORY_TYPE:
            continue
        fields = dict(op.memory_fields or {})
        skill_name = str(fields.get("skill_name") or _fallback_skill_name(op))
        target_uri = (op.uris or [None])[0]
        after_content = str(fields.get("content") or "")
        if not after_content.strip():
            continue

        old_file = op.old_memory_file_content
        after_file = MemoryFile(
            uri=target_uri,
            content=after_content,
            memory_type="skills",
            extra_fields={
                **dict(getattr(old_file, "extra_fields", {}) or {}),
                **{k: v for k, v in fields.items() if k != "content"},
                "memory_type": "skills",
                "skill_name": skill_name,
            },
        )
        links: list[StoredLink] = []
        # Build derived_from links from the source trajectory(s).
        for link in getattr(operations, "resolved_links", []) or []:
            try:
                stored = link if isinstance(link, StoredLink) else StoredLink(**dict(link))
                if stored.link_type == "derived_from" and stored.to_uri:
                    if "/memories/trajectories/" in stored.to_uri:
                        links.append(stored.model_copy(update={"from_uri": target_uri or ""}))
            except Exception:
                continue

        gradients.append(
            PatchSemanticGradient(
                before_file=old_file,
                after_file=after_file,
                base_version=_base_version_from_old_file(old_file),
                rationale=(
                    "Session skill patch extracted from rollout trajectory "
                    "by AgentTrajectoryContextProvider."
                ),
                links=links,
                confidence=0.7,
                metadata={
                    "source": "trajectory_co_extract",
                    "memory_fields": fields,
                    "skill_name": skill_name,
                    "uris": list(op.uris or []),
                },
            )
        )
    return gradients


def _fallback_skill_name(op: Any) -> str:
    uris = getattr(op, "uris", None) or []
    if uris:
        uri = str(uris[0])
        # path/to/skills/my_skill/SKILL.md → my_skill
        parts = uri.rstrip("/").split("/")
        if len(parts) >= 2 and parts[-1] == "SKILL.md":
            return parts[-2]
        return parts[-1].removesuffix(".md")
    return "unknown_skill"


def _base_version_from_old_file(old_file: Any) -> int | None:
    if old_file is None:
        return None
    fields = getattr(old_file, "extra_fields", {}) or {}
    try:
        v = int(fields.get("version"))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None
