# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RolloutAnalyzer that extracts persistent trajectory memories directly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater
from openviking.session.memory.agent_trajectory_context_provider import (
    AgentTrajectoryContextProvider,
)
from openviking.session.memory.dataclass import ResolvedOperations
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train.domain import (
    CriterionResult,
    Rollout,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
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

    @tracer("train.rollout_analyzer.trajectory.analyze", ignore_result=True, ignore_args=True)
    async def analyze(
        self,
        rollout: Rollout,
        context: TrajectoryAnalyzerContext,
    ) -> RolloutAnalysis:
        if context is None or context.request_context is None:
            raise ValueError("TrajectoryAnalyzerContext.request_context is required")

        result = await self.extract_trajectory_memories(
            messages=rollout.messages,
            ctx=context.request_context,
            strict_extract_errors=context.strict_extract_errors,
            latest_archive_overview=context.latest_archive_overview,
        )
        contexts = list((result or {}).get("contexts", []))
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
        return RolloutAnalysis(
            evaluation=_evaluation_from_trajectories(trajectories),
            trajectories=trajectories,
            metadata={
                "context_count": len(contexts),
                "policy_snapshot_id": rollout.policy_snapshot_id,
                "rollout_messages": rollout.messages,
            },
        )

    async def extract_trajectory_memories(
        self,
        *,
        messages: list[Message],
        ctx: RequestContext | None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
    ) -> dict[str, list[Any]]:
        """Extract and persist trajectory memories from rollout messages."""
        empty_result: dict[str, list[Any]] = {"contexts": []}
        if not messages or ctx is None:
            return empty_result

        provider = AgentTrajectoryContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            include_trajectories=True,
            include_session_skills=False,
        )
        phase_result = await self._run_trajectory_extract_phase(
            provider=provider,
            messages=messages,
            ctx=ctx,
            strict_extract_errors=strict_extract_errors,
        )
        if phase_result is None:
            return empty_result

        _, _, contexts = phase_result
        return {"contexts": contexts}

    async def _run_trajectory_extract_phase(
        self,
        *,
        provider: AgentTrajectoryContextProvider,
        messages: list[Message],
        ctx: RequestContext,
        strict_extract_errors: bool,
    ) -> tuple[list[str], list[str], list[Context]] | None:
        config = get_openviking_config()
        vlm = self.vlm or config.vlm.get_vlm_instance()
        viking_fs = self.viking_fs or get_viking_fs()
        if viking_fs is None:
            raise RuntimeError("VikingFS is required to extract trajectory memories")

        extract_context = ExtractContext(messages)
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types={_TRAJECTORY_MEMORY_TYPE},
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
        )

        try:
            provider._transaction_handle = None
            orchestrator._transaction_handle = None
            operations, _ = await orchestrator.run()
            if operations is None:
                tracer.info("[trajectory] No memory operations generated")
                return [], [], []

            _log_operations(operations)
            memory_result = await self._apply_trajectory_operations(
                operations=operations,
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
            return list(memory_result.written_uris), list(memory_result.edited_uris), contexts
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

    @tracer("train.rollout_analyzer.trajectory.read_trajectories", ignore_result=True, ignore_args=True)
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
            metadata = dict(fields)
            metadata.setdefault("memory_type", mf.memory_type or fields.get("memory_type"))
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
        f"{op.memory_type}(uris={op.uris!r})"
        for op in getattr(operations, "upsert_operations", [])
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
