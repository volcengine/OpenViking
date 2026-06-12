#!/usr/bin/env python3
"""Tau2 rollout evaluator backed by environment rewards."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openviking.session.train import CriterionResult, Rollout, RubricEvaluation


@dataclass(slots=True)
class Tau2RewardRolloutEvaluator:
    """Evaluate a rollout using the tau2 reward stored in rollout metadata."""

    async def evaluate(self, rollout: Rollout, context: Any = None) -> RubricEvaluation:
        del context
        if rollout.evaluation is not None:
            return rollout.evaluation
        reward = _safe_float(rollout.metadata.get("reward"), default=0.0)
        passed = reward >= 1.0
        evaluation_result = rollout.metadata.get("evaluation_result")
        feedback = [] if passed else ["tau2 environment reward is below 1.0."]
        if evaluation_result is not None:
            feedback.append(_stringify(evaluation_result))
        return RubricEvaluation(
            passed=passed,
            score=reward,
            criterion_results=[
                CriterionResult(
                    criterion_name="tau2_reward",
                    passed=passed,
                    score=reward,
                    feedback=feedback,
                    evidence=[_stringify(evaluation_result)] if evaluation_result is not None else [],
                    metadata={"reward": reward},
                )
            ],
            feedback=feedback,
            metadata={
                "source": "tau2_reward",
                "reward": reward,
                "evaluation_result": evaluation_result,
            },
        )


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
