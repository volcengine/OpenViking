# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Default orchestration for the session training framework."""

from __future__ import annotations

import inspect
import time
from typing import Any

from openviking.session.train.components.case_loader import make_trial_case_loader
from openviking.session.train.components.policy_trainer import BatchPolicyTrainer
from openviking.session.train.context import (
    ExecutionContext,
    PipelineContext,
    PipelineHookDecision,
)
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
from openviking.session.train.utils import average_score, validate_rollouts_have_cases
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
        requested_epochs = ctx.max_epochs if ctx.max_epochs is not None else 1
        max_epochs = max(0, int(requested_epochs))
        case_loader = _train_case_loader(case_loader, ctx)
        current_policy_set = policy_set
        epoch_results: list[PipelineEpochResult] = []
        evaluation_passes: list[PipelineEvaluationResult] = []
        train_epoch_reports: list[dict[str, Any]] = []
        stop_decision: PipelineHookDecision | None = None

        for epoch in range(max_epochs):
            ctx.execution_metadata["epoch"] = epoch
            await _emit_epoch_start(ctx, epoch)
            epoch_result = await self._run_training_epoch(
                epoch=epoch,
                case_loader=case_loader,
                policy_set=current_policy_set,
                ctx=ctx,
            )
            epoch_results.append(epoch_result)
            current_policy_set = epoch_result.apply_result.updated_policy_set
            hook_decision = await _emit_epoch_end(
                epoch_result=epoch_result,
                policy_set=current_policy_set,
                ctx=ctx,
            )
            train_report = hook_decision.report if hook_decision is not None else None
            if train_report is not None:
                train_epoch_reports.append(train_report)
            await _emit_train_report(ctx, train_report)
            if hook_decision is not None and hook_decision.stop_training:
                stop_decision = hook_decision
                break
            epoch_eval = await self._run_epoch_evaluation_pass(
                epoch=epoch,
                policy_set=current_policy_set,
                ctx=ctx,
            )
            if epoch_eval is not None:
                evaluation_passes.append(epoch_eval)

        all_analyses = [analysis for epoch in epoch_results for analysis in epoch.analyses]
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
            "completed_epochs": len(epoch_results),
            "evaluation_pass_count": len(evaluation_passes),
            "train_reports": train_epoch_reports,
            "stopped_early": stop_decision is not None,
        }
        if stop_decision is not None:
            metadata["stop_reason"] = stop_decision.reason
            if stop_decision.metadata:
                metadata["stop_metadata"] = dict(stop_decision.metadata)
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
            evaluation_passes=evaluation_passes,
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
        eval_case_loader = _eval_case_loader(case_loader, ctx)
        result = await self._run_evaluation_pass(
            epoch=int(ctx.execution_metadata.get("epoch", 0) or 0),
            case_loader=eval_case_loader,
            policy_set=policy_set,
            ctx=ctx,
        )
        eval_report = await _emit_eval_end(
            evaluation_result=result,
            policy_set=policy_set,
            ctx=ctx,
        )
        if eval_report is None:
            raise RuntimeError(
                "pipeline eval requires a lifecycle hook to provide an evaluation report"
            )
        result.metadata["report"] = eval_report
        await _emit_eval_report(ctx, eval_report)
        return result

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
        validate_rollouts_have_cases(rollout_list)
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
        rollout_report: dict[str, Any] | None = None

        epoch_started_at = time.monotonic()
        async for cases in case_loader.batches(ctx.case_load_context):
            rollout_started_at = time.monotonic()
            rollouts, snapshot_id = await self._rollout_batch(
                cases=cases,
                policy_set=current_policy_set,
                ctx=ctx,
                epoch=epoch,
                training=True,
            )
            rollout_cost_seconds = time.monotonic() - rollout_started_at
            snapshot_ids.append(snapshot_id)
            hook_rollout_report = await _emit_train_rollout_end(
                epoch=epoch,
                rollouts=rollouts,
                snapshot_id=snapshot_id,
                policy_set=current_policy_set,
                ctx=ctx,
            )
            rollout_report = _with_cost(hook_rollout_report, rollout_cost_seconds)
            await _emit_train_rollout_report(ctx, rollout_report)
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

        epoch_cost_seconds = time.monotonic() - epoch_started_at
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
                "score": average_score(all_analyses),
                "analysis_count": len(all_analyses),
                "gradient_count": len(all_gradients),
                "train_rollout_report": rollout_report,
                "cost_seconds": epoch_cost_seconds,
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

        started_at = time.monotonic()
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
        cost_seconds = time.monotonic() - started_at

        return PipelineEvaluationResult(
            epoch=epoch,
            analyses=all_analyses,
            policy_snapshot_ids=snapshot_ids,
            metadata={
                **dict(ctx.execution_metadata),
                "score": average_score(all_analyses),
                "analysis_count": len(all_analyses),
                "evaluation_only": True,
                "cost_seconds": cost_seconds,
            },
        )

    async def _run_epoch_evaluation_pass(
        self,
        *,
        epoch: int,
        policy_set: ExperienceSet,
        ctx: PipelineContext,
    ) -> PipelineEvaluationResult | None:
        if ctx.eval_each_epoch_case_loader is None:
            return None
        eval_ctx = _epoch_eval_context(ctx, epoch=epoch)
        eval_case_loader = _eval_case_loader(ctx.eval_each_epoch_case_loader, eval_ctx)
        result = await self._run_evaluation_pass(
            epoch=epoch,
            case_loader=eval_case_loader,
            policy_set=policy_set,
            ctx=eval_ctx,
        )
        eval_report = await _emit_eval_end(
            evaluation_result=result,
            policy_set=policy_set,
            ctx=eval_ctx,
        )
        if eval_report is None:
            raise RuntimeError(
                "pipeline eval requires a lifecycle hook to provide an evaluation report"
            )
        result.metadata["report"] = eval_report
        await _emit_eval_report(eval_ctx, eval_report)
        return result

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
        stage = _rollout_stage(epoch=epoch, training=training)
        if not training:
            stage = ctx.execution_metadata.get("rollout_stage") or stage
        execution_metadata = {
            **dict(ctx.execution_metadata),
            "epoch": epoch,
            "training": training,
            "stage": stage,
        }
        # ponytail: train rollouts must never inherit an eval rollout_stage —
        # _stage_from_execution_metadata checks rollout_stage before stage, so
        # a leaked value would mis-route artifacts into eval directories.
        if training:
            execution_metadata.pop("rollout_stage", None)
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


async def _emit_train_rollout_end(
    *,
    epoch: int,
    rollouts: list[Any],
    snapshot_id: str,
    policy_set: ExperienceSet,
    ctx: PipelineContext,
) -> dict[str, Any] | None:
    hook_report: dict[str, Any] | None = None
    for hook in ctx.lifecycle_hooks:
        result = await _call_hook(
            hook.on_train_rollout_end,
            epoch=epoch,
            rollouts=rollouts,
            snapshot_id=snapshot_id,
            policy_set=policy_set,
            context=ctx,
        )
        hook_report = _merge_report_hook_result(
            hook_report,
            result,
            hook_name="on_train_rollout_end",
        )
    return hook_report


def _merge_report_hook_result(
    current: dict[str, Any] | None,
    result: Any,
    *,
    hook_name: str,
) -> dict[str, Any] | None:
    if result is None:
        return current
    if not isinstance(result, dict):
        raise TypeError(f"{hook_name} must return dict or None, got {type(result).__name__}")
    return result


async def _call_hook(method: Any, **kwargs: Any) -> Any:
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _call_event_hook(method: Any, **kwargs: Any) -> None:
    await _call_hook(method, **kwargs)


async def _emit_epoch_end(
    *,
    epoch_result: PipelineEpochResult,
    policy_set: ExperienceSet,
    ctx: PipelineContext,
) -> PipelineHookDecision | None:
    hook_decision: PipelineHookDecision | None = None
    for hook in ctx.lifecycle_hooks:
        result = await _call_hook(
            hook.on_epoch_end,
            epoch_result=epoch_result,
            policy_set=policy_set,
            context=ctx,
        )
        if result is None:
            continue
        if not isinstance(result, PipelineHookDecision):
            raise TypeError(
                "on_epoch_end must return PipelineHookDecision or None, "
                f"got {type(result).__name__}"
            )
        hook_decision = _merge_hook_decision(hook_decision, result)
    return hook_decision


async def _emit_eval_end(
    *,
    evaluation_result: PipelineEvaluationResult,
    policy_set: ExperienceSet,
    ctx: PipelineContext,
) -> dict[str, Any] | None:
    hook_report: dict[str, Any] | None = None
    for hook in ctx.lifecycle_hooks:
        result = await _call_hook(
            hook.on_eval_end,
            evaluation_result=evaluation_result,
            policy_set=policy_set,
            context=ctx,
        )
        hook_report = _merge_report_hook_result(
            hook_report,
            result,
            hook_name="on_eval_end",
        )
    return hook_report


def _epoch_eval_context(ctx: PipelineContext, *, epoch: int) -> PipelineContext:
    inherited_metadata = dict(ctx.execution_metadata)
    execution_metadata = {
        **inherited_metadata,
        "epoch": epoch,
        "training": False,
        "rollout_stage": inherited_metadata.get("rollout_stage") or "test_rollout",
        "eval_split": inherited_metadata.get("eval_split") or "test",
    }
    return PipelineContext(
        case_load_context=ctx.case_load_context,
        snapshot_context=ctx.snapshot_context,
        analysis_context=ctx.analysis_context,
        gradient_context=ctx.gradient_context,
        optimization_context=ctx.optimization_context,
        apply_context=ctx.apply_context,
        execution_metadata=execution_metadata,
        max_epochs=1,
        eval_trials=ctx.eval_trials,
        train_trials=ctx.train_trials,
        trial_index_key=ctx.trial_index_key,
        report_builder=ctx.report_builder,
        lifecycle_hooks=list(ctx.lifecycle_hooks),
    )


def _train_case_loader(case_loader: CaseLoader, ctx: PipelineContext) -> CaseLoader:
    train_trials = int(ctx.train_trials or 1)
    if train_trials <= 1:
        return case_loader
    return make_trial_case_loader(
        case_loader,
        train_trials,
        trial_input_key="train_trial",
    )


def _eval_case_loader(case_loader: CaseLoader, ctx: PipelineContext) -> CaseLoader:
    eval_trials = int(ctx.eval_trials or 1)
    if eval_trials <= 1:
        return case_loader
    return make_trial_case_loader(
        case_loader,
        eval_trials,
        trial_input_key=ctx.trial_index_key,
    )


def _merge_hook_decision(
    current: PipelineHookDecision | None,
    incoming: PipelineHookDecision,
) -> PipelineHookDecision:
    if current is None:
        return incoming
    return PipelineHookDecision(
        stop_training=current.stop_training or incoming.stop_training,
        reason=incoming.reason or current.reason,
        metadata={**current.metadata, **incoming.metadata},
        report=incoming.report if incoming.report is not None else current.report,
    )


async def _emit_epoch_start(ctx: PipelineContext, epoch: int) -> None:
    for hook in ctx.lifecycle_hooks:
        await _call_event_hook(hook.on_epoch_start, epoch=epoch, context=ctx)


async def _emit_train_rollout_report(
    ctx: PipelineContext,
    report: dict[str, Any] | None,
) -> None:
    if report is None:
        return
    for hook in ctx.lifecycle_hooks:
        await _call_event_hook(
            hook.on_train_rollout_report,
            report=report,
            context=ctx,
        )


async def _emit_train_report(
    ctx: PipelineContext,
    report: dict[str, Any] | None,
) -> None:
    if report is None:
        return
    for hook in ctx.lifecycle_hooks:
        await _call_event_hook(hook.on_train_report, report=report, context=ctx)


async def _emit_eval_report(ctx: PipelineContext, report: dict[str, Any] | None) -> None:
    if report is None:
        return
    label = str(
        report.get("label")
        or ctx.execution_metadata.get("report_label")
        or ctx.execution_metadata.get("rollout_stage")
        or _rollout_stage(
            epoch=int(ctx.execution_metadata.get("epoch", 0) or 0),
            training=False,
        ).split(maxsplit=1)[0]
    )
    for hook in ctx.lifecycle_hooks:
        await _call_event_hook(
            hook.on_eval_report,
            label=label,
            report=report,
            context=ctx,
        )


def _with_cost(report: dict[str, Any] | None, cost_seconds: float) -> dict[str, Any] | None:
    if report is None:
        return None
    updated = dict(report)
    updated["cost_seconds"] = max(0.0, float(cost_seconds))
    return updated


def _rollout_stage(*, epoch: int, training: bool) -> str:
    if training:
        return f"train_rollout epoch={epoch}"
    if epoch < 0:
        return "baseline_test_rollout"
    return f"test_rollout epoch={epoch}"


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
        score = average_score(epoch.analyses)
        if score is not None:
            return score
    return None


def _final_epoch_score(
    epochs: list[PipelineEpochResult],
) -> float | None:
    for epoch in reversed(epochs):
        score = average_score(epoch.analyses)
        if score is not None:
            return score
    return None
