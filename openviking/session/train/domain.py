# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Domain models for session experience-policy optimization.

This module defines the new training domain model alongside the existing
trajectory/experience memory implementation.  The types here are intentionally
small and implementation-agnostic so the new framework can be built out without
changing the current extraction pipeline.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from openviking.message import Message

PolicyStatus = Literal["draft", "staging", "production", "deprecated", "archived"]
TrajectoryOutcome = Literal["success", "failure", "partial", "unfinished", "unknown"]
PolicyPlanItemKind = Literal["upsert_experience", "delete_experience"]


@dataclass(slots=True)
class Experience:
    """A single experience file in an ExperienceSet."""

    name: str
    uri: str
    version: int
    status: PolicyStatus
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExperienceSet:
    """Snapshot of all experiences under an experiences directory.

    ``viking_fs`` and ``request_context`` are runtime storage dependencies used
    for concurrency-safe policy updates.  They are intentionally excluded from
    equality/repr so the domain snapshot still behaves like policy data in
    tests and diagnostics.
    """

    root_uri: str
    policies: list[Experience]
    metadata: dict[str, Any] = field(default_factory=dict)
    viking_fs: Any | None = field(default=None, repr=False, compare=False)
    request_context: Any | None = field(default=None, repr=False, compare=False)

    @asynccontextmanager
    async def lock(self):
        """Acquire a tree lock for the whole policy root directory.

        Policy updates serialize on this lock so concurrent realtime/batch
        training jobs plan and apply against a freshly reloaded policy set.
        ``timeout=None`` means wait indefinitely until the lock is available.
        """

        if self.viking_fs is None:
            raise RuntimeError("ExperienceSet.viking_fs is required for policy locking")
        if self.request_context is None:
            raise RuntimeError("ExperienceSet.request_context is required for policy locking")
        uri_to_path = getattr(self.viking_fs, "_uri_to_path", None)
        if uri_to_path is None:
            raise RuntimeError("ExperienceSet.viking_fs must provide _uri_to_path for locking")

        from openviking.storage.transaction import get_lock_manager

        lock_manager = get_lock_manager()
        handle = lock_manager.create_handle()
        path = uri_to_path(self.root_uri, ctx=self.request_context)
        acquired = await lock_manager.acquire_tree(handle, path, timeout=None)
        if not acquired:
            await lock_manager.release(handle)
            raise RuntimeError(f"Failed to acquire policy tree lock for {self.root_uri}")
        try:
            yield handle
        finally:
            await lock_manager.release(handle)

    async def reload(self) -> "ExperienceSet":
        """Reload this policy set from its backing VikingFS under the same ctx."""

        if self.viking_fs is None:
            raise RuntimeError("ExperienceSet.viking_fs is required for policy reload")
        if self.request_context is None:
            raise RuntimeError("ExperienceSet.request_context is required for policy reload")

        from openviking.session.train.components.memory_store import ExperienceSetLoader

        return await ExperienceSetLoader(viking_fs=self.viking_fs).load(
            self.root_uri,
            ctx=self.request_context,
        )


@dataclass(slots=True)
class Trajectory:
    """A distilled, trainable trajectory sample parsed from trajectory memory."""

    name: str
    uri: str
    content: str
    outcome: TrajectoryOutcome | str
    retrieval_anchor: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RubricCriterion:
    """One criterion in a case rubric."""

    name: str
    description: str
    required: bool
    weight: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Rubric:
    """Acceptance criteria and scoring rules for a case."""

    name: str
    description: str
    criteria: list[RubricCriterion]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Case:
    """An executable, reproducible, evaluable training/evaluation sample."""

    name: str
    task_signature: str
    input: dict[str, Any]
    rubric: Rubric
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Rollout:
    """Execution record for a case under a policy-set snapshot."""

    case: Case
    messages: list[Message]
    policy_snapshot_id: str


@dataclass(slots=True)
class CriterionResult:
    """Evaluation result for one rubric criterion."""

    criterion_name: str
    passed: bool
    score: float
    feedback: list[str]
    evidence: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RubricEvaluation:
    """Structured evaluation of a rollout against a rubric."""

    passed: bool
    score: float
    criterion_results: list[CriterionResult]
    feedback: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RolloutAnalysis:
    """Structured analysis of a rollout.

    Contains both rubric evaluation and trajectories extracted from the same
    rollout context.
    """

    evaluation: RubricEvaluation
    trajectories: list[Trajectory]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyPlanItem:
    """One executable item in a PolicyUpdatePlan.

    The first stable implementation focuses on upserting full experience file
    content produced by PatchSemanticGradient.  Other plan kinds are reserved
    for future delete / split / merge / human-review actions.
    """

    kind: PolicyPlanItemKind
    target_experience_name: str
    target_experience_uri: str | None
    before_content: str | None
    after_content: str | None
    base_version: int | None = None
    confidence: float | None = None
    evidence_trajectory_uris: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyUpdatePlan:
    """Planned update for an ExperienceSet.

    ``items`` is the executable part consumed by PolicyUpdater implementations.
    ``metadata`` keeps optimizer diagnostics such as grouping, conflicts, and
    unresolved gradients.
    """

    items: list[PolicyPlanItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyApplyResult:
    """Result of applying a PolicyUpdatePlan."""

    updated_policy_set: ExperienceSet
    written_uris: list[str] = field(default_factory=list)
    deleted_uris: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineIterationResult:
    """Result of one rollout/evaluate/train iteration.

    One iteration runs the current policy snapshot on case batches, analyzes the
    resulting rollouts, estimates semantic gradients, plans a policy update, and
    applies it.  Repeating this structure models the offline equivalent of
    rollout -> evaluation -> update -> rollout -> evaluation.
    """

    iteration: int
    analyses: list[RolloutAnalysis]
    gradients: list[Any]
    plan: PolicyUpdatePlan
    apply_result: PolicyApplyResult
    policy_snapshot_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineEvaluationResult:
    """Evaluation-only rollout result for a policy snapshot.

    This is typically used as the final after-training evaluation pass.  It
    intentionally does not include gradients or policy updates.
    """

    iteration: int
    analyses: list[RolloutAnalysis]
    policy_snapshot_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineResult:
    """End-to-end result of a policy optimization pipeline run."""

    analyses: list[RolloutAnalysis]
    gradients: list[Any]
    plan: PolicyUpdatePlan
    apply_result: PolicyApplyResult
    iterations: list[PipelineIterationResult] = field(default_factory=list)
    evaluation_passes: list[PipelineEvaluationResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RolloutTrainingResult:
    """Result of training directly from externally produced rollouts.

    This is the online/realtime counterpart of one offline pipeline training
    iteration.  The caller owns rollout execution; the training framework owns
    analysis, gradient estimation, policy planning, and policy update.
    """

    analyses: list[RolloutAnalysis]
    gradients: list[Any]
    plan: PolicyUpdatePlan
    apply_result: PolicyApplyResult
    metadata: dict[str, Any] = field(default_factory=dict)

