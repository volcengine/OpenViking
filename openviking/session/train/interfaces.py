# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Protocol interfaces for the session training framework."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from openviking.session.memory.dataclass import MemoryFile, StoredLink
from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    PipelineEvaluationResult,
    PipelineResult,
    PolicyApplyResult,
    PolicySet,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
    RolloutTrainingResult,
    RubricEvaluation,
)


class SemanticGradient(Protocol):
    """A semantic update signal for one target policy."""

    @property
    def before_file(self) -> MemoryFile | None: ...

    @property
    def after_file(self) -> MemoryFile: ...

    @property
    def target_name(self) -> str: ...

    @property
    def target_uri(self) -> str | None: ...

    @property
    def base_version(self) -> int | None: ...

    @property
    def rationale(self) -> str: ...

    @property
    def links(self) -> list[StoredLink]: ...

    @property
    def confidence(self) -> float: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


class PolicyOptimizer(Protocol):
    """Plans policy-set updates from semantic gradients."""

    async def plan(
        self,
        gradients: list[SemanticGradient],
        policy_set: PolicySet,
        context: Any,
    ) -> PolicyUpdatePlan: ...


class PolicyUpdater(Protocol):
    """Applies a policy update plan to a PolicySet."""

    async def apply(
        self,
        plan: PolicyUpdatePlan,
        policy_set: PolicySet,
        context: Any,
        *,
        transaction_handle: Any = None,
    ) -> PolicyApplyResult: ...


class CaseLoader(Protocol):
    """Loads case batches for policy optimization."""

    async def batches(self, context: Any) -> AsyncIterator[list[Case]]: ...


class RolloutExecutor(Protocol):
    """Executes cases against a policy set and produces rollouts."""

    async def execute(
        self,
        cases: list[Case],
        policy_set: PolicySet,
        context: ExecutionContext,
    ) -> list[Rollout]: ...


class PolicySnapshotter(Protocol):
    """Creates a snapshot identifier for a PolicySet."""

    async def snapshot(self, policy_set: PolicySet, context: Any) -> str: ...


class RolloutAnalyzer(Protocol):
    """Analyzes a rollout and extracts learning signals."""

    async def analyze(self, rollout: Rollout, context: Any) -> RolloutAnalysis: ...


class RolloutEvaluator(Protocol):
    """Evaluates a rollout before learning-signal extraction."""

    async def evaluate(self, rollout: Rollout, context: Any) -> RubricEvaluation: ...


class GradientEstimator(Protocol):
    """Estimates semantic gradients from rollout analysis."""

    async def estimate(
        self,
        analysis: RolloutAnalysis,
        policy_set: PolicySet,
        context: Any,
    ) -> list[SemanticGradient]: ...


class PolicyTrainer(Protocol):
    """Trains a policy from rollout batches, optionally using precomputed analyses."""

    async def train_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: PolicySet,
        context: Any,
        analyses: list[RolloutAnalysis] | None = None,
    ) -> RolloutTrainingResult: ...


class PolicyOptimizationPipeline(Protocol):
    """Runs end-to-end policy optimization over case batches."""

    async def train(
        self,
        case_loader: CaseLoader,
        policy_set: PolicySet,
        context: Any,
    ) -> PipelineResult: ...

    async def eval(
        self,
        case_loader: CaseLoader,
        policy_set: PolicySet,
        context: Any,
    ) -> PipelineEvaluationResult: ...

    async def train_from_rollouts(
        self,
        rollouts: list[Rollout],
        policy_set: PolicySet,
        context: Any,
    ) -> RolloutTrainingResult: ...
