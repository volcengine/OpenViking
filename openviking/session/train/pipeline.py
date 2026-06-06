# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Default orchestration for the session training framework."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from openviking.session.train.domain import (
    ApplyResult,
    ExecutionContext,
    ExperienceSet,
    PipelineEvaluationResult,
    PipelineIterationResult,
    PipelineResult,
    PolicyUpdatePlan,
    RolloutAnalysis,
    RolloutTrainingResult,
)
from openviking.session.train.interfaces import (
    CaseLoader,
    GradientEstimator,
    PolicyOptimizer,
    PolicySnapshotter,
    PolicyUpdater,
    RolloutAnalyzer,
    RolloutExecutor,
    SemanticGradient,
)
from openviking.session.train.trainers import BatchPolicyTrainer
from openviking.telemetry import tracer


@dataclass(slots=True)
class PipelineContext:
    """Context bundle for DefaultPolicyOptimizationPipeline.

    Context payloads are intentionally opaque and can be shaped by concrete
    implementations without changing the domain interfaces.
    """

    case_load_context: Any = None
    snapshot_context: Any = None
    analysis_context: Any = None
    gradient_context: Any = None
    optimization_context: Any = None
    apply_context: Any = None
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 1
    final_evaluation: bool = False


class DefaultPolicyOptimizationPipeline:
    """Composable batch-oriented iterative policy optimization pipeline.

    This class wires the protocol interfaces together.  It does not implement
    rollout execution, LLM analysis, gradient estimation, optimization, or file
    updates itself.

    ``run`` natively supports multiple offline iterations.  Each iteration uses
    the current policy set to run rollouts and evaluations, then applies the
    resulting update before the next iteration.  With ``final_evaluation=True``
    the pipeline also runs one evaluation-only pass after the last update, which
    gives callers the canonical before/after sequence:

    ``rollout -> evaluate -> train -> rollout -> evaluate``.
    """

    def __init__(
        self,
        *,
        snapshotter: PolicySnapshotter,
        rollout_executor: RolloutExecutor,
        rollout_analyzer: RolloutAnalyzer,
        gradient_estimator: GradientEstimator,
        policy_optimizer: PolicyOptimizer,
        policy_updater: PolicyUpdater,
    ) -> None:
        self.snapshotter = snapshotter
        self.rollout_executor = rollout_executor
        self.rollout_analyzer = rollout_analyzer
        self.gradient_estimator = gradient_estimator
        self.policy_optimizer = policy_optimizer
        self.policy_updater = policy_updater

    @tracer("train.pipeline.run", ignore_result=True, ignore_args=True)
    async def run(
        self,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        context: PipelineContext | Any,
    ) -> PipelineResult:
        ctx = context if isinstance(context, PipelineContext) else PipelineContext()
        max_iterations = max(1, int(ctx.max_iterations or 1))
        current_policy_set = policy_set
        iteration_results: list[PipelineIterationResult] = []
        evaluation_passes: list[PipelineEvaluationResult] = []

        for iteration in range(max_iterations):
            iteration_result = await self._run_training_iteration(
                iteration=iteration,
                case_loader=case_loader,
                policy_set=current_policy_set,
                ctx=ctx,
            )
            iteration_results.append(iteration_result)
            current_policy_set = iteration_result.apply_result.updated_policy_set

        if ctx.final_evaluation:
            evaluation_passes.append(
                await self._run_evaluation_pass(
                    iteration=max_iterations,
                    case_loader=case_loader,
                    policy_set=current_policy_set,
                    ctx=ctx,
                )
            )

        all_analyses = [
            analysis for iteration in iteration_results for analysis in iteration.analyses
        ]
        all_gradients: list[SemanticGradient] = [
            gradient for iteration in iteration_results for gradient in iteration.gradients
        ]

        if iteration_results:
            last_plan = iteration_results[-1].plan
            last_apply_result = iteration_results[-1].apply_result
        else:
            last_plan = PolicyUpdatePlan(metadata={"empty": True})
            last_apply_result = ApplyResult(updated_policy_set=current_policy_set)

        first_score = _first_analysis_score(iteration_results)
        final_score = _final_analysis_score(iteration_results, evaluation_passes)
        metadata: dict[str, Any] = {
            "policy_set_root_uri": current_policy_set.root_uri,
            "max_iterations": max_iterations,
            "final_evaluation": ctx.final_evaluation,
        }
        if first_score is not None:
            metadata["first_score"] = first_score
        if final_score is not None:
            metadata["final_score"] = final_score
        if first_score is not None and final_score is not None:
            metadata["score_delta"] = final_score - first_score

        return PipelineResult(
            analyses=all_analyses,
            gradients=list(all_gradients),
            plan=last_plan,
            apply_result=last_apply_result,
            iterations=iteration_results,
            evaluation_passes=evaluation_passes,
            metadata=metadata,
        )

    @tracer("train.pipeline.train_from_rollouts", ignore_result=True, ignore_args=True)
    async def train_from_rollouts(
        self,
        rollouts,
        policy_set: ExperienceSet,
        context: PipelineContext | Any,
    ) -> RolloutTrainingResult:
        """Train directly from externally produced rollout records.

        This path is intended for realtime/online collection where another
        component has already executed an agent loop and produced ``Rollout``s.
        It deliberately skips ``CaseLoader``, ``PolicySnapshotter`` and
        ``RolloutExecutor`` while reusing the same downstream training stages as
        offline optimization:

        ``Rollout[] -> RolloutAnalyzer -> GradientEstimator -> PolicyOptimizer -> PolicyUpdater``.
        """

        ctx = context if isinstance(context, PipelineContext) else PipelineContext()
        rollout_list = list(rollouts)
        result = await BatchPolicyTrainer(
            rollout_analyzer=self.rollout_analyzer,
            gradient_estimator=self.gradient_estimator,
            policy_optimizer=self.policy_optimizer,
            policy_updater=self.policy_updater,
        ).train_rollouts(
            rollouts=rollout_list,
            policy_set=policy_set,
            context=ctx,
        )
        result.metadata["source"] = "external_rollouts"
        return result

    async def _run_training_iteration(
        self,
        *,
        iteration: int,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> PipelineIterationResult:
        all_analyses: list[RolloutAnalysis] = []
        all_gradients: list[SemanticGradient] = []
        last_plan: PolicyUpdatePlan | None = None
        last_apply_result: ApplyResult | None = None
        current_policy_set = policy_set
        snapshot_ids: list[str] = []

        async for cases in case_loader.batches(ctx.case_load_context):
            analyses, snapshot_id = await self._rollout_and_analyze_batch(
                cases=cases,
                policy_set=current_policy_set,
                ctx=ctx,
                iteration=iteration,
                training=True,
            )
            snapshot_ids.append(snapshot_id)
            all_analyses.extend(analyses)

            gradients = await self._estimate_gradients(analyses, current_policy_set, ctx)
            all_gradients.extend(gradients)

            last_plan, last_apply_result = await self._plan_and_apply(
                gradients,
                current_policy_set,
                ctx,
            )
            current_policy_set = last_apply_result.updated_policy_set

        if last_plan is None or last_apply_result is None:
            last_plan = PolicyUpdatePlan(metadata={"empty": True, "iteration": iteration})
            last_apply_result = ApplyResult(updated_policy_set=current_policy_set)

        return PipelineIterationResult(
            iteration=iteration,
            analyses=all_analyses,
            gradients=list(all_gradients),
            plan=last_plan,
            apply_result=last_apply_result,
            policy_snapshot_ids=snapshot_ids,
            metadata={
                "score": _average_score(all_analyses),
                "analysis_count": len(all_analyses),
                "gradient_count": len(all_gradients),
            },
        )

    async def _estimate_gradients(
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

    async def _plan_and_apply(
        self,
        gradients: list[SemanticGradient],
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> tuple[PolicyUpdatePlan, ApplyResult]:
        async with policy_set.lock():
            latest_policy_set = await policy_set.reload()
            plan = await self.policy_optimizer.plan(
                gradients,
                latest_policy_set,
                ctx.optimization_context,
            )
            apply_result = await self.policy_updater.apply(
                plan,
                latest_policy_set,
                ctx.apply_context or latest_policy_set.request_context,
            )
        return plan, apply_result

    async def _run_evaluation_pass(
        self,
        *,
        iteration: int,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> PipelineEvaluationResult:
        all_analyses: list[RolloutAnalysis] = []
        snapshot_ids: list[str] = []

        async for cases in case_loader.batches(ctx.case_load_context):
            analyses, snapshot_id = await self._rollout_and_analyze_batch(
                cases=cases,
                policy_set=policy_set,
                ctx=ctx,
                iteration=iteration,
                training=False,
            )
            snapshot_ids.append(snapshot_id)
            all_analyses.extend(analyses)

        return PipelineEvaluationResult(
            iteration=iteration,
            analyses=all_analyses,
            policy_snapshot_ids=snapshot_ids,
            metadata={
                "score": _average_score(all_analyses),
                "analysis_count": len(all_analyses),
                "evaluation_only": True,
            },
        )

    async def _rollout_and_analyze_batch(
        self,
        *,
        cases,
        policy_set: ExperienceSet,
        ctx: PipelineContext,
        iteration: int,
        training: bool,
    ) -> tuple[list[RolloutAnalysis], str]:
        snapshot_id = await self.snapshotter.snapshot(
            policy_set,
            ctx.snapshot_context,
        )
        execution_metadata = {
            **dict(ctx.execution_metadata),
            "iteration": iteration,
            "training": training,
        }
        execution_context = ExecutionContext(
            policy_snapshot_id=snapshot_id,
            metadata=execution_metadata,
        )
        rollouts = await self.rollout_executor.execute(
            cases,
            policy_set,
            execution_context,
        )
        analyses = await asyncio.gather(
            *[self.rollout_analyzer.analyze(rollout, ctx.analysis_context) for rollout in rollouts]
        )
        return list(analyses), snapshot_id


def _average_score(analyses: list[RolloutAnalysis]) -> float | None:
    if not analyses:
        return None
    return sum(float(analysis.evaluation.score) for analysis in analyses) / len(analyses)


def _first_analysis_score(iterations: list[PipelineIterationResult]) -> float | None:
    for iteration in iterations:
        score = _average_score(iteration.analyses)
        if score is not None:
            return score
    return None


def _final_analysis_score(
    iterations: list[PipelineIterationResult],
    evaluation_passes: list[PipelineEvaluationResult],
) -> float | None:
    for evaluation in reversed(evaluation_passes):
        score = _average_score(evaluation.analyses)
        if score is not None:
            return score
    for iteration in reversed(iterations):
        score = _average_score(iteration.analyses)
        if score is not None:
            return score
    return None
