# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protocol interfaces for the session training framework."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    ExperienceSet,
    PipelineResult,
    PolicyApplyResult,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
)


class SemanticGradient(Protocol):
    """A semantic update signal for one target Experience."""

    @property
    def before_file(self) -> MemoryFile | None: ...

    @property
    def after_file(self) -> MemoryFile: ...

    @property
    def target_experience_name(self) -> str: ...

    @property
    def target_experience_uri(self) -> str | None: ...

    @property
    def base_version(self) -> int | None: ...

    @property
    def rationale(self) -> str: ...

    @property
    def evidence_trajectory_uris(self) -> list[str]: ...

    @property
    def confidence(self) -> float: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


class PolicyOptimizer(Protocol):
    """Plans policy-set updates from semantic gradients."""

    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        context: Any,
    ) -> PolicyUpdatePlan: ...


class PolicyUpdater(Protocol):
    """Applies a policy update plan to an ExperienceSet."""

    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: ExperienceSet,
        context: Any,
    ) -> PolicyApplyResult: ...


class CaseLoader(Protocol):
    """Loads case batches for policy optimization."""

    async def batches(self, context: Any) -> AsyncIterator[list[Case]]: ...


class RolloutExecutor(Protocol):
    """Executes cases against a policy set and produces rollouts."""

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]: ...


class PolicySnapshotter(Protocol):
    """Creates a snapshot identifier for an ExperienceSet."""

    async def snapshot(self, policy_set: ExperienceSet, context: Any) -> str: ...


class RolloutAnalyzer(Protocol):
    """Analyzes a rollout and extracts learning signals."""

    async def analyze(self, rollout: Rollout, context: Any) -> RolloutAnalysis: ...


class GradientEstimator(Protocol):
    """Estimates semantic gradients from rollout analysis."""

    async def estimate(
        self,
        analysis: RolloutAnalysis,
        experience_set: ExperienceSet,
        context: Any,
    ) -> list[SemanticGradient]: ...


class PolicyOptimizationPipeline(Protocol):
    """Runs end-to-end policy optimization over case batches."""

    async def run(
        self,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        context: Any,
    ) -> PipelineResult: ...

    async def train_from_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        context: Any,
    ) -> RolloutTrainingResult: ...
