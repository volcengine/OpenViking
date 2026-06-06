# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""RolloutAnalyzer adapter backed by the legacy trajectory extraction phase."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openviking.server.identity import RequestContext
from openviking.session.compressor_v2 import SessionCompressorV2
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

logger = get_logger(__name__)


@dataclass(slots=True)
class LegacyTrajectoryAnalyzerContext:
    """Context for LegacyTrajectoryRolloutAnalyzer."""

    request_context: RequestContext
    strict_extract_errors: bool = False
    latest_archive_overview: str = ""
    archive_uri: str = ""
    include_session_skills: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LegacyTrajectoryRolloutAnalyzer:
    """Analyze rollouts by reusing the legacy trajectory extraction phase.

    This adapter intentionally invokes only the legacy trajectory phase by
    restricting allowed memory types to {"trajectories"}.  It does not run the
    old experience consolidation phase.
    """

    compressor: SessionCompressorV2
    viking_fs: Any = None

    @tracer(
        "train.rollout_analyzer.legacy_trajectory.analyze",
        ignore_result=True,
        ignore_args=True,
    )
    async def analyze(
        self,
        rollout: Rollout,
        context: LegacyTrajectoryAnalyzerContext,
    ) -> RolloutAnalysis:
        if context is None or context.request_context is None:
            raise ValueError("LegacyTrajectoryAnalyzerContext.request_context is required")

        result = await self.compressor.extract_agent_memories(
            messages=rollout.messages,
            ctx=context.request_context,
            strict_extract_errors=context.strict_extract_errors,
            latest_archive_overview=context.latest_archive_overview,
            archive_uri=context.archive_uri,
            allowed_memory_types={"trajectories"},
            include_session_skills=context.include_session_skills,
        )
        contexts = list((result or {}).get("contexts", []))
        trajectory_uris = [
            item.uri
            for item in contexts
            if getattr(item, "category", "") == "memory_write"
            and "/memories/trajectories/" in getattr(item, "uri", "")
        ]
        trajectories = await self._read_trajectories(
            trajectory_uris,
            ctx=context.request_context,
        )
        evaluation = _evaluation_from_trajectories(trajectories)
        return RolloutAnalysis(
            evaluation=evaluation,
            trajectories=trajectories,
            metadata={
                "legacy_context_count": len(contexts),
                "policy_snapshot_id": rollout.policy_snapshot_id,
                "rollout_messages": rollout.messages,
            },
        )

    @tracer(
        "train.rollout_analyzer.legacy_trajectory.read_trajectories",
        ignore_result=True,
        ignore_args=True,
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
