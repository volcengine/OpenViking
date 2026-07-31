# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Default report builders for session train/eval results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from openviking.session.train.components.reporter import NoopPipelineLifecycleHook
from openviking.session.train.domain import (
    CriterionResult,
    ExperienceSet,
    PipelineEpochResult,
    PipelineEvaluationResult,
    Rollout,
    RolloutAnalysis,
)


@dataclass(slots=True)
class PipelineReportBuilder:
    """Build serializable summary reports from pipeline domain objects."""

    trial_index_key: str = "trial"
    memory_tool_name_prefix: str = "openviking"

    def evaluation_report(
        self,
        result: PipelineEvaluationResult,
    ) -> dict[str, Any]:
        rewards = [float(analysis.evaluation.score) for analysis in result.analyses]
        passed_count = sum(1 for analysis in result.analyses if analysis.evaluation.passed)
        case_count = len(result.analyses)
        return {
            "epoch": result.epoch,
            **_eval_metadata_fields(result.metadata),
            "case_count": case_count,
            "accuracy": _ratio(passed_count, case_count),
            "passed_count": passed_count,
            "average_reward": _average(rewards),
            "rewards": rewards,
            "snapshot_ids": list(result.policy_snapshot_ids),
            "metadata": dict(result.metadata),
            "memory_usage": self.memory_usage_from_analyses(result.analyses),
            "cost_seconds": result.metadata.get("cost_seconds"),
        }

    def trial_evaluation_report(
        self,
        result: PipelineEvaluationResult,
        *,
        trial_count: int | None = None,
    ) -> dict[str, Any]:
        if trial_count is None:
            trial_count = self._trial_count_from_analyses(result.analyses)
        analyses_by_trial: dict[int, list[RolloutAnalysis]] = {
            trial_index: [] for trial_index in range(trial_count)
        }
        for analysis in result.analyses:
            trial_index = self.analysis_trial_index(analysis)
            analyses_by_trial.setdefault(trial_index, []).append(analysis)

        trials = [
            {
                "trial": trial_index,
                **self.evaluation_summary_from_analyses(
                    analyses_by_trial.get(trial_index, [])
                ),
            }
            for trial_index in range(trial_count)
        ]
        accuracies = [
            float(item["accuracy"])
            for item in trials
            if item.get("accuracy") is not None
        ]
        average_rewards = [
            float(item["average_reward"])
            for item in trials
            if item.get("average_reward") is not None
        ]
        overall = self.evaluation_report(result)
        case_counts = [int(item["case_count"]) for item in trials]
        return {
            **overall,
            "trial_count": trial_count,
            "case_count_per_trial": case_counts[0] if len(set(case_counts)) == 1 else None,
            "case_counts_per_trial": case_counts,
            "total_rollout_count": len(result.analyses),
            "accuracy_mean": _average(accuracies),
            "accuracy_std": _stddev(accuracies),
            "average_reward_mean": _average(average_rewards),
            "average_reward_std": _stddev(average_rewards),
            "trials": trials,
            # Keep callers simple: accuracy/average_reward denote the
            # trial-level mean when trials are enabled.
            "accuracy": _average(accuracies),
            "average_reward": _average(average_rewards),
        }

    def evaluation_summary_from_analyses(
        self,
        analyses: list[RolloutAnalysis],
    ) -> dict[str, Any]:
        rewards = [float(analysis.evaluation.score) for analysis in analyses]
        passed_count = sum(1 for analysis in analyses if analysis.evaluation.passed)
        case_count = len(analyses)
        return {
            "case_count": case_count,
            "accuracy": _ratio(passed_count, case_count),
            "passed_count": passed_count,
            "average_reward": _average(rewards),
            "rewards": rewards,
            "memory_usage": self.memory_usage_from_analyses(analyses),
        }

    def analysis_trial_index(self, analysis: RolloutAnalysis) -> int:
        rollout = analysis.metadata.get("rollout")
        if isinstance(rollout, Rollout):
            value = rollout.case.input.get(
                self.trial_index_key,
                rollout.case.metadata.get(self.trial_index_key, 0),
            )
        else:
            value = 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _trial_count_from_analyses(self, analyses: list[RolloutAnalysis]) -> int:
        for analysis in analyses:
            rollout = analysis.metadata.get("rollout")
            if not isinstance(rollout, Rollout):
                continue
            value = rollout.case.input.get(
                f"{self.trial_index_key}_count",
                rollout.case.metadata.get(f"{self.trial_index_key}_count"),
            )
            if value is not None:
                return int(value)
        return 1

    def train_rollout_report(
        self,
        *,
        epoch: int,
        rollouts: list[Rollout],
        snapshot_id: str,
    ) -> dict[str, Any]:
        analyses = _analyses_from_rollout_evaluations(rollouts)
        train_eval = self.train_evaluation_report(analyses)
        cache_hits = [
            rollout
            for rollout in rollouts
            if isinstance(rollout.metadata, dict)
            and bool(rollout.metadata.get("train_rollout_cache_hit"))
        ]
        return {
            "epoch": epoch,
            "snapshot_id": snapshot_id,
            "snapshot_ids": [snapshot_id],
            **train_eval,
            "cache_hit_count": len(cache_hits),
            "cache_miss_count": max(len(rollouts) - len(cache_hits), 0),
            "from_cache": bool(cache_hits) and len(cache_hits) == len(rollouts),
        }

    def train_epoch_report(
        self,
        epoch_result: PipelineEpochResult,
        *,
        rollout_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        train_eval = self.train_evaluation_report(epoch_result.analyses)
        commit_results = list(epoch_result.apply_result.metadata.get("commit_results", []))
        errors = [error for item in commit_results if (error := item.get("error"))]
        snapshot_ids = list(epoch_result.policy_snapshot_ids)
        display_eval = rollout_report or train_eval
        return {
            "epoch": epoch_result.epoch,
            "case_count": display_eval["case_count"],
            "accuracy": display_eval["accuracy"],
            "passed_count": display_eval["passed_count"],
            "average_reward": display_eval["average_reward"],
            "train_rollout": rollout_report,
            "train_eval": train_eval,
            "batch_count": len(snapshot_ids),
            "gradient_count": len(epoch_result.gradients),
            "committed_rollout_count": len(commit_results),
            "errors": errors,
            "failed_commit_trace_ids": _failed_commit_trace_ids(commit_results),
            "failed_commit_telemetry_ids": _failed_commit_telemetry_ids(commit_results),
            "snapshot_ids": snapshot_ids,
            "commit_results": commit_results,
            "metadata": dict(epoch_result.metadata),
            "memory_usage": self.memory_usage_from_analyses(epoch_result.analyses),
            "cost_seconds": epoch_result.metadata.get("cost_seconds"),
        }

    def train_evaluation_report(
        self,
        analyses: list[RolloutAnalysis],
    ) -> dict[str, Any]:
        rewards = [float(analysis.evaluation.score) for analysis in analyses]
        return {
            **self.evaluation_summary_from_analyses(analyses),
            "reward_std": _stddev(rewards),
            "case_results": [_train_case_evaluation_result(analysis) for analysis in analyses],
        }

    def memory_usage_from_analyses(
        self,
        analyses: list[RolloutAnalysis],
    ) -> dict[str, Any]:
        rollouts = [
            rollout
            for analysis in analyses
            if isinstance((rollout := analysis.metadata.get("rollout")), Rollout)
        ]
        rollout_count = len(rollouts)
        memory_context_count = 0
        memory_tool_call_rollout_count = 0
        memory_tool_call_total = 0
        for rollout in rollouts:
            metadata = rollout.metadata or {}
            if str(metadata.get("memory") or "").strip():
                memory_context_count += 1
            tool_call_count = self.memory_tool_call_count(metadata.get("tools_used"))
            if tool_call_count:
                memory_tool_call_rollout_count += 1
                memory_tool_call_total += tool_call_count
        return {
            "rollout_count": rollout_count,
            "memory_context_count": memory_context_count,
            "memory_context_ratio": _ratio(memory_context_count, rollout_count),
            "memory_tool_call_rollout_count": memory_tool_call_rollout_count,
            "memory_tool_call_rollout_ratio": _ratio(
                memory_tool_call_rollout_count,
                rollout_count,
            ),
            "memory_tool_call_total": memory_tool_call_total,
        }

    def memory_tool_call_count(self, tools_used: Any) -> int:
        if not isinstance(tools_used, list):
            return 0
        count = 0
        for tool_info in tools_used:
            if not isinstance(tool_info, dict):
                continue
            tool_name = str(tool_info.get("tool_name") or "")
            if tool_name.startswith(self.memory_tool_name_prefix):
                count += 1
        return count

    def accuracy_delta(
        self,
        baseline_eval: dict[str, Any] | None,
        final_eval: dict[str, Any] | None,
    ) -> float | None:
        if not baseline_eval or not final_eval:
            return None
        baseline = baseline_eval.get("accuracy")
        final = final_eval.get("accuracy")
        if baseline is None or final is None:
            return None
        return float(final) - float(baseline)


@dataclass(slots=True)
class PipelineReportHook(NoopPipelineLifecycleHook):
    """Lifecycle hook that builds default serializable pipeline reports."""

    def on_train_rollout_end(
        self,
        *,
        epoch: int,
        rollouts: list[Any],
        snapshot_id: str,
        policy_set: ExperienceSet,
        context: Any,
    ) -> dict[str, Any] | None:
        del policy_set
        return _context_report_builder(context).train_rollout_report(
            epoch=epoch,
            rollouts=list(rollouts),
            snapshot_id=snapshot_id,
        )

    def on_epoch_end(
        self,
        *,
        epoch_result: PipelineEpochResult,
        policy_set: ExperienceSet,
        context: Any,
    ) -> Any:
        del policy_set
        from openviking.session.train.context import PipelineHookDecision

        return PipelineHookDecision(
            report=_context_report_builder(context).train_epoch_report(
                epoch_result,
                rollout_report=epoch_result.metadata.get("train_rollout_report"),
            )
        )

    def on_eval_end(
        self,
        *,
        evaluation_result: PipelineEvaluationResult,
        policy_set: ExperienceSet,
        context: Any,
    ) -> dict[str, Any] | None:
        del policy_set
        eval_trials = int(getattr(context, "eval_trials", 1) or 1)
        builder = _context_report_builder(context)
        if eval_trials > 1:
            return builder.trial_evaluation_report(
                evaluation_result,
                trial_count=eval_trials,
            )
        return builder.evaluation_report(evaluation_result)


def _context_report_builder(context: Any) -> PipelineReportBuilder:
    report_builder = getattr(context, "report_builder", None)
    if report_builder is not None:
        return report_builder
    return PipelineReportBuilder(
        trial_index_key=str(getattr(context, "trial_index_key", "trial") or "trial")
    )


def _eval_metadata_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if metadata.get("rollout_stage") is not None:
        fields["rollout_stage"] = metadata["rollout_stage"]
    if metadata.get("eval_split") is not None:
        fields["split"] = metadata["eval_split"]
    return fields


def _analyses_from_rollout_evaluations(rollouts: list[Rollout]) -> list[RolloutAnalysis]:
    analyses: list[RolloutAnalysis] = []
    for idx, rollout in enumerate(rollouts):
        if rollout.evaluation is None:
            raise ValueError(
                "report builder requires rollout.evaluation; "
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
                    "evaluation_source": "rollout",
                },
            )
        )
    return analyses


def _train_case_evaluation_result(analysis: RolloutAnalysis) -> dict[str, Any]:
    evaluation = analysis.evaluation
    rollout = analysis.metadata.get("rollout")
    case = rollout.case if isinstance(rollout, Rollout) else None
    rollout_metadata = dict(rollout.metadata) if isinstance(rollout, Rollout) else {}
    case_input = dict(case.input) if case is not None else {}
    return {
        "case_name": case.name if case is not None else "",
        "task_signature": case.task_signature if case is not None else "",
        "data_split": case_input.get("data_split") or rollout_metadata.get("data_split"),
        "task_no": case_input.get("task_no") or rollout_metadata.get("task_no"),
        "task_id": case_input.get("task_id") or rollout_metadata.get("task_id"),
        "passed": evaluation.passed,
        "score": float(evaluation.score),
        "reward": rollout_metadata.get("reward", evaluation.metadata.get("reward")),
        "evaluation_result": rollout_metadata.get(
            "evaluation_result",
            evaluation.metadata.get("evaluation_result"),
        ),
        "feedback": list(evaluation.feedback),
        "criterion_results": [
            _criterion_result_report(result) for result in evaluation.criterion_results
        ],
        "policy_snapshot_id": analysis.metadata.get("policy_snapshot_id"),
    }


def _criterion_result_report(result: CriterionResult) -> dict[str, Any]:
    return {
        "criterion_name": result.criterion_name,
        "passed": result.passed,
        "score": float(result.score),
        "feedback": list(result.feedback),
        "evidence": list(result.evidence),
        "metadata": dict(result.metadata),
    }


def _failed_commit_trace_ids(commit_results: list[dict[str, Any]]) -> list[str]:
    trace_ids: list[str] = []
    for item in commit_results:
        if not item.get("error"):
            continue
        trace_id = str(item.get("trace_id") or "").strip()
        if trace_id:
            trace_ids.append(trace_id)
    return trace_ids


def _failed_commit_telemetry_ids(commit_results: list[dict[str, Any]]) -> list[str]:
    telemetry_ids: list[str] = []
    for item in commit_results:
        if not item.get("error"):
            continue
        telemetry_id = str(item.get("telemetry_id") or "").strip()
        if telemetry_id:
            telemetry_ids.append(telemetry_id)
    return telemetry_ids


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _stddev(values: list[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator
