# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RolloutAnalyzer that extracts persistent trajectory memories directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking.core.context import Context
from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking.session.memory.dataclass import (
    MemoryFile,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.skill.session_skill_context_provider import (
    SESSION_SKILL_MEMORY_TYPE,
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


@dataclass(slots=True)
class TrajectoryAnalyzerContext:
    """Runtime context for TrajectoryRolloutAnalyzer."""

    request_context: RequestContext
    strict_extract_errors: bool = False
    latest_archive_overview: str = ""
    evaluator_context: Any = None
    inject_evaluation_feedback: bool = True
    include_session_skills: bool = False
    source_archive_uri: str = ""


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
        extraction_messages = _messages_with_evaluation_feedback(
            rollout.messages,
            evaluation=evaluation,
            enabled=evaluation is not None and context.inject_evaluation_feedback,
        )
        result = await self.extract_trajectory_memories(
            messages=extraction_messages,
            ctx=context.request_context,
            strict_extract_errors=context.strict_extract_errors,
            latest_archive_overview=context.latest_archive_overview,
            include_session_skills=context.include_session_skills,
            case_name=getattr(rollout.case, "name", ""),
            source_archive_uri=context.source_archive_uri,
        )
        contexts = list((result or {}).get("contexts", []))
        skill_gradients = list((result or {}).get("skill_gradients", []))
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
        evaluation = evaluation or _evaluation_from_trajectories(trajectories)
        return RolloutAnalysis(
            evaluation=evaluation,
            trajectories=trajectories,
            gradients=skill_gradients,
            metadata={
                "context_count": len(contexts),
                "policy_snapshot_id": rollout.policy_snapshot_id,
                "rollout_messages": rollout.messages,
                "extraction_message_count": len(extraction_messages),
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
        source_archive_uri: str = "",
    ) -> dict[str, list[Any]]:
        """Extract and persist trajectory memories from rollout messages.

        When ``include_session_skills`` is True, session skill patches are
        co-extracted in the same ExtractLoop pass and returned as
        ``PatchSemanticGradient`` instances in the ``"skill_gradients"`` key.
        Skill patches are *not* applied to disk by this method — they are
        returned as gradient signals for downstream policy training.
        """
        empty_result: dict[str, list[Any]] = {"contexts": [], "skill_gradients": []}
        if not messages or ctx is None:
            return empty_result

        provider = AgentTrajectoryContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            include_trajectories=True,
            include_session_skills=include_session_skills,
        )
        phase_result = await self._run_trajectory_extract_phase(
            provider=provider,
            messages=messages,
            ctx=ctx,
            strict_extract_errors=strict_extract_errors,
            include_session_skills=include_session_skills,
            case_name=case_name,
            source_archive_uri=source_archive_uri,
        )
        if phase_result is None:
            return empty_result

        _, _, contexts, skill_gradients = phase_result
        return {"contexts": contexts, "skill_gradients": skill_gradients}

    async def _run_trajectory_extract_phase(
        self,
        *,
        provider: AgentTrajectoryContextProvider,
        messages: list[Message],
        ctx: RequestContext,
        strict_extract_errors: bool,
        include_session_skills: bool = False,
        case_name: str = "",
        source_archive_uri: str = "",
    ) -> tuple[list[str], list[str], list[Context], list[PatchSemanticGradient]] | None:
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

        orchestrator = ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=provider,
            isolation_handler=isolation_handler,
            thinking=True,
        )

        try:
            provider._transaction_handle = None
            orchestrator._transaction_handle = None
            operations, _ = await orchestrator.run()
            if operations is None:
                tracer.info("[trajectory] No memory operations generated")
                return [], [], [], []

            _log_operations(operations)

            # Split operations into trajectory (applied to disk) and skill
            # (returned as gradients).  Skill ops are *not* written here —
            # they flow through the patch-merge trainer.
            traj_ops, skill_ops = _split_operations_by_type(
                operations, target_type=_TRAJECTORY_MEMORY_TYPE
            )
            skill_gradients = _skill_operations_to_gradients(
                skill_ops,
                viking_fs=viking_fs,
                ctx=ctx,
            )

            _ensure_trajectory_case_name(traj_ops, case_name=case_name)
            _apply_trajectory_source_archive_uri(traj_ops, source_archive_uri)

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
            )
        except Exception as exc:
            logger.error("[trajectory] Failed to extract: %s", exc, exc_info=True)
            if strict_extract_errors:
                raise
            return None

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


def _ensure_trajectory_case_name(operations: ResolvedOperations, *, case_name: str) -> None:
    case_name = str(case_name or "").strip()
    if not case_name:
        return
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != _TRAJECTORY_MEMORY_TYPE:
            continue
        fields = getattr(op, "memory_fields", None)
        if isinstance(fields, dict):
            fields["case_name"] = case_name


def _apply_trajectory_source_archive_uri(
    operations: ResolvedOperations,
    source_archive_uri: str,
) -> None:
    source_archive_uri = str(source_archive_uri or "").rstrip("/")
    if not source_archive_uri:
        return
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != _TRAJECTORY_MEMORY_TYPE:
            continue
        fields = getattr(op, "memory_fields", None)
        if not isinstance(fields, dict):
            continue
        fields["source_archive_uri"] = source_archive_uri


def _messages_with_evaluation_feedback(
    messages: list[Message],
    *,
    evaluation: RubricEvaluation | None,
    enabled: bool,
) -> list[Message]:
    result = list(messages)
    if not enabled or evaluation is None:
        return result
    result.append(_evaluation_feedback_message(evaluation))
    return result


def _evaluation_feedback_message(evaluation: RubricEvaluation) -> Message:
    lines = [
        "[Rollout Evaluation]",
        f"passed: {evaluation.passed}",
        f"score: {evaluation.score}",
    ]
    if evaluation.feedback:
        lines.extend(["", "feedback:", *[f"- {item}" for item in evaluation.feedback]])
    criterion_lines: list[str] = []
    evidence_lines: list[str] = []
    for criterion in evaluation.criterion_results:
        criterion_lines.append(
            f"- {criterion.criterion_name}: passed={criterion.passed}, score={criterion.score}"
        )
        criterion_lines.extend(f"  feedback: {item}" for item in criterion.feedback)
        evidence_lines.extend(criterion.evidence)
    if criterion_lines:
        lines.extend(["", "criteria:", *criterion_lines])
    if evidence_lines:
        lines.extend(["", "evidence:", *[f"- {item}" for item in dict.fromkeys(evidence_lines)]])
    return Message(
        id="rollout-evaluation-feedback",
        role="user",
        parts=[TextPart(text="\n".join(lines))],
    )


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
