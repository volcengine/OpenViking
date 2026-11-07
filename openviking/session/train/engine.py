# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared training engine for rollout-driven policy updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from openviking.session.train.context import PipelineContext
from openviking.session.train.domain import (
    ExperienceSet,
    PolicyApplyResult,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
)
from openviking.session.train.interfaces import (
    GradientEstimator,
    PolicyOptimizer,
    PolicyUpdater,
    RolloutAnalyzer,
    SemanticGradient,
)

# Bounded number of optimistic re-plans when the policy set changed while the
# optimizer was planning.  Keeps the (potentially minutes-long) LLM replanning
# from livelocking against sustained concurrent writers.
_PLAN_APPLY_MAX_REPLANS = 2


@dataclass(slots=True)
class PolicyTrainingEngine:
    """Shared implementation of analyze -> estimate -> plan -> apply."""

    rollout_analyzer: RolloutAnalyzer
    gradient_estimator: GradientEstimator
    policy_optimizer: PolicyOptimizer
    policy_updater: PolicyUpdater

    async def analyze_estimate_plan_apply(
        self,
        *,
        rollouts: list[Rollout],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> tuple[list[RolloutAnalysis], list[SemanticGradient], PolicyUpdatePlan, PolicyApplyResult]:
        analyses = await self.analyze_rollouts(rollouts, ctx)
        gradients = await self.estimate_gradients(analyses, policy_set, ctx)
        plan, apply_result = await self.plan_and_apply(
            gradients=gradients,
            policy_set=policy_set,
            ctx=ctx,
        )
        return analyses, gradients, plan, apply_result

    async def analyze_rollouts(
        self,
        rollouts: list[Rollout],
        ctx: PipelineContext,
    ) -> list[RolloutAnalysis]:
        analyses = await asyncio.gather(
            *[self.rollout_analyzer.analyze(rollout, ctx.analysis_context) for rollout in rollouts]
        )
        return list(analyses)

    async def estimate_gradients(
        self,
        analyses: list[RolloutAnalysis],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> list[SemanticGradient]:
        gradient_batches = await asyncio.gather(
            *[
                self.gradient_estimator.estimate(
                    analysis,
                    policy_set,
                    ctx.gradient_context,
                )
                for analysis in analyses
            ]
        )
        return [gradient for batch in gradient_batches for gradient in batch]

    async def plan_and_apply(
        self,
        *,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> tuple[PolicyUpdatePlan, PolicyApplyResult]:
        """Plan against a lock-free snapshot, then apply in a short critical section.

        ``policy_optimizer.plan`` may block on a remote LLM for minutes.  Holding
        the policy-root tree lock across that call starves every concurrent
        writer of the same root until the lock acquisition timeout fails their
        commits, so planning runs outside the lock.  The lock is taken only to
        re-check that the snapshot the plan was built against is still the
        latest one before applying.  When another writer committed meanwhile,
        the plan is rebuilt against the fresh snapshot (bounded by
        ``_PLAN_APPLY_MAX_REPLANS``); once the bound is exhausted the last plan
        is applied under the lock with a conflict marker, and the updater's
        per-item base-content guard reports stale targets as errors instead of
        silently overwriting concurrent changes.
        """

        replans = 0
        while True:
            planned_snapshot = await policy_set.reload()
            plan = await self.policy_optimizer.plan(
                gradients,
                planned_snapshot,
                ctx.optimization_context,
            )
            async with policy_set.lock() as transaction_handle:
                latest_policy_set = await policy_set.reload()
                snapshot_current = _policy_sets_match(latest_policy_set, planned_snapshot)
                if snapshot_current or replans >= _PLAN_APPLY_MAX_REPLANS:
                    if not snapshot_current:
                        plan.metadata = {
                            **dict(plan.metadata or {}),
                            "optimistic_apply_after_conflict": True,
                            "replan_attempts": replans + 1,
                        }
                    apply_result = await self.policy_updater.apply(
                        plan,
                        latest_policy_set,
                        ctx.apply_context or latest_policy_set.request_context,
                        transaction_handle=transaction_handle,
                    )
                    return plan, apply_result
            # The policy set moved on while planning: the lock was released on
            # exiting the context manager, so re-plan outside it against the
            # fresh snapshot.
            replans += 1


def _policy_sets_match(current: ExperienceSet, planned: ExperienceSet) -> bool:
    """Whether ``planned`` is still the latest committed view of the policy root."""

    return current.root_uri == planned.root_uri and list(current.policies) == list(planned.policies)
