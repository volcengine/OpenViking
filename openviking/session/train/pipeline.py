# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Default orchestration for the session training framework."""

from __future__ import annotations

from typing import Any

from openviking.session.train.components.policy_trainer import BatchPolicyTrainer
from openviking.session.train.context import ExecutionContext, PipelineContext
from openviking.session.train.domain import (
    ExperienceSet,
    PipelineEpochResult,
    PipelineEvaluationResult,
    PipelineResult,
    PolicyApplyResult,
    PolicyUpdatePlan,
    RolloutAnalysis,
    RolloutTrainingResult,
)
from openviking.session.train.engine import PolicyTrainingEngine
from openviking.session.train.interfaces import (
    CaseLoader,
    GradientEstimator,
    PolicyOptimizer,
    PolicySnapshotter,
    PolicyTrainer,
    PolicyUpdater,
    RolloutAnalyzer,
    RolloutExecutor,
    SemanticGradient,
)
from openviking.telemetry import tracer


class OfflinePolicyOptimizationPipeline:
    """Composable offline train/eval pipeline for case-driven policy optimization.

    This class wires the protocol interfaces together.  It does not implement
    rollout execution, LLM analysis, gradient estimation, optimization, or file
    updates itself.

    ``train`` updates the policy set from case rollouts. ``eval`` only executes
    and analyzes rollouts; it never estimates gradients or writes policy files.
    Benchmark runners should explicitly compose them, for example:
    ``eval(test) -> train(train) -> eval(test)``.
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
        policy_trainer: PolicyTrainer | None = None,
    ) -> None:
        self.snapshotter = snapshotter
        self.rollout_executor = rollout_executor
        self.rollout_analyzer = rollout_analyzer
        self.gradient_estimator = gradient_estimator
        self.policy_optimizer = policy_optimizer
        self.policy_updater = policy_updater
        self._training_engine = PolicyTrainingEngine(
            rollout_analyzer=rollout_analyzer,
            gradient_estimator=gradient_estimator,
            policy_optimizer=policy_optimizer,
            policy_updater=policy_updater,
        )
        self.policy_trainer = policy_trainer or BatchPolicyTrainer(
            rollout_analyzer=rollout_analyzer,
            gradient_estimator=gradient_estimator,
            policy_optimizer=policy_optimizer,
            policy_updater=policy_updater,
        )

    @tracer("train.pipeline.train", ignore_result=True, ignore_args=True)
    async def train(
        self,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        context: PipelineContext | Any,
    ) -> PipelineResult:
        ctx = context if isinstance(context, PipelineContext) else PipelineContext()
        max_epochs = max(1, int(ctx.max_epochs or 1))
        current_policy_set = policy_set
        epoch_results: list[PipelineEpochResult] = []

        for epoch in range(max_epochs):
            epoch_result = await self._run_training_epoch(
                epoch=epoch,
                case_loader=case_loader,
                policy_set=current_policy_set,
                ctx=ctx,
            )
            epoch_results.append(epoch_result)
            current_policy_set = epoch_result.apply_result.updated_policy_set

        all_analyses = [
            analysis for epoch in epoch_results for analysis in epoch.analyses
        ]
        all_gradients: list[SemanticGradient] = [
            gradient for epoch in epoch_results for gradient in epoch.gradients
        ]

        if epoch_results:
            last_plan = epoch_results[-1].plan
            last_apply_result = epoch_results[-1].apply_result
        else:
            last_plan = PolicyUpdatePlan(metadata={"empty": True})
            last_apply_result = PolicyApplyResult(updated_policy_set=current_policy_set)

        first_score = _first_epoch_score(epoch_results)
        final_score = _final_epoch_score(epoch_results)
        metadata: dict[str, Any] = {
            "policy_set_root_uri": current_policy_set.root_uri,
            "max_epochs": max_epochs,
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
            epochs=epoch_results,
            evaluation_passes=[],
            metadata=metadata,
        )

    @tracer("train.pipeline.eval", ignore_result=True, ignore_args=True)
    async def eval(
        self,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        context: PipelineContext | Any,
    ) -> PipelineEvaluationResult:
        ctx = context if isinstance(context, PipelineContext) else PipelineContext()
        return await self._run_evaluation_pass(
            epoch=int(ctx.execution_metadata.get("epoch", 0) or 0),
            case_loader=case_loader,
            policy_set=policy_set,
            ctx=ctx,
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

        The configured PolicyTrainer owns downstream training semantics. The
        default BatchPolicyTrainer analyzes rollouts locally; remote trainers
        may submit raw rollouts to a server-side analyzer.
        """

        ctx = context if isinstance(context, PipelineContext) else PipelineContext()
        rollout_list = list(rollouts)
        _validate_rollouts_have_cases(rollout_list)
        result = await self.policy_trainer.train_rollouts(
            rollout_list,
            policy_set,
            ctx,
        )
        result.metadata["source"] = "external_rollouts"
        return result

    async def _run_training_epoch(
        self,
        *,
        epoch: int,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> PipelineEpochResult:
        all_analyses: list[RolloutAnalysis] = []
        all_gradients: list[SemanticGradient] = []
        last_plan: PolicyUpdatePlan | None = None
        last_apply_result: PolicyApplyResult | None = None
        current_policy_set = policy_set
        snapshot_ids: list[str] = []

        async for cases in case_loader.batches(ctx.case_load_context):
            rollouts, snapshot_id = await self._rollout_batch(
                cases=cases,
                policy_set=current_policy_set,
                ctx=ctx,
                epoch=epoch,
                training=True,
            )
            snapshot_ids.append(snapshot_id)
            training_result = await self.policy_trainer.train_rollouts(
                rollouts,
                current_policy_set,
                ctx,
            )
            gradients = list(training_result.gradients)
            all_analyses.extend(training_result.analyses)
            last_plan = training_result.plan
            last_apply_result = training_result.apply_result
            all_gradients.extend(gradients)
            current_policy_set = last_apply_result.updated_policy_set

        if last_plan is None or last_apply_result is None:
            last_plan = PolicyUpdatePlan(metadata={"empty": True, "epoch": epoch})
            last_apply_result = PolicyApplyResult(updated_policy_set=current_policy_set)

        return PipelineEpochResult(
            epoch=epoch,
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

    async def _run_evaluation_pass(
        self,
        *,
        epoch: int,
        case_loader: CaseLoader,
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> PipelineEvaluationResult:
        all_analyses: list[RolloutAnalysis] = []
        snapshot_ids: list[str] = []

        async for cases in case_loader.batches(ctx.case_load_context):
            rollouts, snapshot_id = await self._rollout_batch(
                cases=cases,
                policy_set=policy_set,
                ctx=ctx,
                epoch=epoch,
                training=False,
            )
            snapshot_ids.append(snapshot_id)
            all_analyses.extend(_analyses_from_rollout_evaluations(rollouts))

        return PipelineEvaluationResult(
            epoch=epoch,
            analyses=all_analyses,
            policy_snapshot_ids=snapshot_ids,
            metadata={
                "score": _average_score(all_analyses),
                "analysis_count": len(all_analyses),
                "evaluation_only": True,
            },
        )

    async def _rollout_batch(
        self,
        *,
        cases,
        policy_set: ExperienceSet,
        ctx: PipelineContext,
        epoch: int,
        training: bool,
    ) -> tuple[list[Any], str]:
        snapshot_id = await self.snapshotter.snapshot(
            policy_set,
            ctx.snapshot_context,
        )
        execution_metadata = {
            **dict(ctx.execution_metadata),
            "epoch": epoch,
            "training": training,
            "stage": _rollout_stage(epoch=epoch, training=training),
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
        return rollouts, snapshot_id


def _rollout_stage(*, epoch: int, training: bool) -> str:
    if training:
        return f"train-rollout epoch={epoch}"
    if epoch < 0:
        return "baseline-rollout"
    return "final-rollout"


def _average_score(analyses: list[RolloutAnalysis]) -> float | None:
    if not analyses:
        return None
    return sum(float(analysis.evaluation.score) for analysis in analyses) / len(analyses)


def _analyses_from_rollout_evaluations(rollouts) -> list[RolloutAnalysis]:
    analyses: list[RolloutAnalysis] = []
    for idx, rollout in enumerate(rollouts):
        if rollout.evaluation is None:
            raise ValueError(
                "pipeline eval requires RolloutExecutor to provide rollout.evaluation; "
                f"missing index={idx}, case={rollout.case.name}"
            )
        analyses.append(
            RolloutAnalysis(
                evaluation=rollout.evaluation,
                trajectories=[],
                metadata={
                    "rollout": rollout,
                    "rollout_messages": rollout.messages,
                    "policy_snapshot_id": rollout.policy_snapshot_id,
                    "evaluation_source": "rollout_executor",
                },
            )
        )
    return analyses


def _first_epoch_score(epochs: list[PipelineEpochResult]) -> float | None:
    for epoch in epochs:
        score = _average_score(epoch.analyses)
        if score is not None:
            return score
    return None


def _final_epoch_score(
    epochs: list[PipelineEpochResult],
) -> float | None:
    for epoch in reversed(epochs):
        score = _average_score(epoch.analyses)
        if score is not None:
            return score
    return None


def _validate_rollouts_have_cases(rollouts) -> None:
    missing = [
        idx for idx, rollout in enumerate(rollouts) if getattr(rollout, "case", None) is None
    ]
    if missing:
        raise ValueError(
            f"rollout training requires Rollout.case for all rollouts; missing indices={missing}"
        )
