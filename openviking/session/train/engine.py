# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared training engine for rollout-driven policy updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from openviking.session.train.context import PipelineContext
from openviking.session.train.domain import (
    ExperienceSet,
    PolicyApplyResult,
    PolicyUpdatePlan,
    Rollout,
    RolloutAnalysis,
)
from openviking.session.train.gates import default_policy_gate_runner
from openviking.session.train.interfaces import (
    GradientEstimator,
    PolicyOptimizer,
    PolicyUpdater,
    RolloutAnalyzer,
    SemanticGradient,
)


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
            analyses=analyses,
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
        gradients = [gradient for batch in gradient_batches for gradient in batch]
        gate_runner = ctx.gate_runner or default_policy_gate_runner()
        gradients, gate_report = await gate_runner.filter_gradients(
            gradients,
            analyses=analyses,
            policy_set=policy_set,
        )
        _append_gate_report(ctx, gate_report)
        return gradients

    async def plan_and_apply(
        self,
        *,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
        analyses: list[RolloutAnalysis] | None = None,
    ) -> tuple[PolicyUpdatePlan, PolicyApplyResult]:
        async with policy_set.lock() as transaction_handle:
            latest_policy_set = await policy_set.reload()
            _prepare_optimization_context_for_gates(
                ctx.optimization_context,
                analyses=list(analyses or []),
                gate_runner=ctx.gate_runner or default_policy_gate_runner(),
            )
            plan = await self.policy_optimizer.plan(
                gradients,
                latest_policy_set,
                ctx.optimization_context,
            )
            gate_runner = ctx.gate_runner or default_policy_gate_runner()
            filtered_items, gate_report = await gate_runner.filter_plan(
                list(plan.items or []),
                analyses=list(analyses or []),
                policy_set=latest_policy_set,
            )
            _append_gate_report(ctx, gate_report)
            if len(filtered_items) != len(plan.items or []):
                plan = PolicyUpdatePlan(
                    items=filtered_items,
                    metadata={
                        **dict(plan.metadata or {}),
                        "gate_report": gate_report.to_dict(),
                    },
                )
            elif gate_report.decisions:
                plan.metadata.setdefault("gate_reports", []).append(gate_report.to_dict())
            apply_result = await self.policy_updater.apply(
                plan,
                latest_policy_set,
                ctx.apply_context or latest_policy_set.request_context,
                transaction_handle=transaction_handle,
            )
        return plan, apply_result


def _append_gate_report(ctx: PipelineContext, report: Any) -> None:
    if report is None:
        return
    ctx.execution_metadata.setdefault("gate_reports", []).append(report.to_dict())


def _prepare_optimization_context_for_gates(
    optimization_context: Any,
    *,
    analyses: list[RolloutAnalysis],
    gate_runner: Any,
) -> None:
    if optimization_context is None:
        return
    if hasattr(optimization_context, "analyses"):
        try:
            optimization_context.analyses = list(analyses)
        except Exception:
            pass
    if hasattr(optimization_context, "gate_runner"):
        try:
            optimization_context.gate_runner = gate_runner
        except Exception:
            pass
