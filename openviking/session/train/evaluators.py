# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rollout evaluation helpers for the session training framework."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openviking.message import Message, TextPart
from openviking.models.vlm.llm import parse_json_from_response
from openviking.session.train.domain import (
    Case,
    CriterionResult,
    Rollout,
    RolloutAnalysis,
    RubricEvaluation,
    Trajectory,
)
from openviking.telemetry import tracer
from openviking_cli.utils.config import get_openviking_config


@dataclass(slots=True)
class LLMRubricRolloutAnalyzer:
    """Analyze a rollout by grading it against the case Rubric with an LLM.

    The analyzer can also receive extracted trajectories from ``trajectory_extractor``.
    This makes it possible to combine evaluation and trajectory extraction behind
    the existing RolloutAnalyzer interface, so a pipeline iteration has a single
    ``rollout -> analysis`` boundary.
    """

    vlm: Any = None
    trajectory_extractor: Any = None
    thinking: bool | None = None

    @tracer("train.rollout_analyzer.llm_rubric.analyze", ignore_result=True, ignore_args=True)
    async def analyze(self, rollout: Rollout, context: Any = None) -> RolloutAnalysis:
        vlm = self.vlm or get_openviking_config().vlm
        response = await vlm.get_completion_async(
            prompt=_rubric_evaluation_prompt(rollout.case, rollout),
            thinking=self.thinking,
        )
        evaluation = _parse_rubric_evaluation(response, rollout.case)
        trajectories = await self._extract_trajectories(rollout, context)
        return RolloutAnalysis(
            evaluation=evaluation,
            trajectories=trajectories,
            metadata={
                "policy_snapshot_id": rollout.policy_snapshot_id,
                "rollout_messages": rollout.messages,
                "raw_evaluation_response": getattr(response, "content", str(response)),
            },
        )

    async def _extract_trajectories(self, rollout: Rollout, context: Any) -> list[Trajectory]:
        if self.trajectory_extractor is None:
            return []
        extracted = self.trajectory_extractor(rollout, context)
        if hasattr(extracted, "__await__"):
            extracted = await extracted
        return list(extracted or [])


@dataclass(slots=True)
class HeuristicRubricRolloutAnalyzer:
    """Deterministic rubric analyzer for local tests and bootstrap evaluations.

    It is intentionally small and domain-agnostic enough for smoke tests: each
    criterion is considered passed when all CJK/word tokens from the criterion
    description appear in the assistant output.  Production evaluation should use
    ``LLMRubricRolloutAnalyzer`` or another dedicated implementation.
    """

    min_token_chars: int = 2

    @tracer(
        "train.rollout_analyzer.heuristic_rubric.analyze",
        ignore_result=True,
        ignore_args=True,
    )
    async def analyze(self, rollout: Rollout, context: Any = None) -> RolloutAnalysis:
        del context
        assistant_text = "\n".join(
            message.content for message in rollout.messages if message.role == "assistant"
        )
        criterion_results: list[CriterionResult] = []
        total_weight = sum(max(0.0, criterion.weight) for criterion in rollout.case.rubric.criteria)
        weighted_score = 0.0
        for criterion in rollout.case.rubric.criteria:
            tokens = _description_tokens(criterion.description, self.min_token_chars)
            passed = all(token in assistant_text for token in tokens) if tokens else False
            score = 1.0 if passed else 0.0
            weighted_score += score * max(0.0, criterion.weight)
            criterion_results.append(
                CriterionResult(
                    criterion_name=criterion.name,
                    passed=passed,
                    score=score,
                    feedback=[] if passed else [f"Missing evidence for: {criterion.description}"],
                    evidence=[token for token in tokens if token in assistant_text],
                )
            )

        score = weighted_score / total_weight if total_weight > 0 else 0.0
        passed = all(
            result.passed
            for result in criterion_results
            if _criterion_required(rollout.case, result.criterion_name)
        )
        return RolloutAnalysis(
            evaluation=RubricEvaluation(
                passed=passed,
                score=score,
                criterion_results=criterion_results,
                feedback=[] if passed else ["One or more required criteria failed."],
            ),
            trajectories=[
                Trajectory(
                    name=rollout.case.name,
                    uri=f"memory://rollouts/{rollout.case.name}/{rollout.policy_snapshot_id}",
                    content=assistant_text,
                    outcome="success" if passed else "failure",
                    retrieval_anchor=rollout.case.task_signature,
                )
            ],
            metadata={"rollout_messages": rollout.messages},
        )


def _rubric_evaluation_prompt(case: Case, rollout: Rollout) -> str:
    conversation = "\n\n".join(
        f"{message.role.upper()}:\n{message.content}" for message in rollout.messages
    )
    return "\n".join(
        [
            "你是 OpenViking 离线训练的严格评估器。",
            "请只根据 Case、Rubric 和 Rollout Assistant 的实际输出进行评分。",
            "不要因为提示词里出现了 rubric 或经验就给分；必须看助手是否真的按要求完成。",
            "",
            "# Case",
            f"Name: {case.name}",
            f"Task signature: {case.task_signature}",
            "Input:",
            json.dumps(case.input, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "# Rubric",
            f"{case.rubric.name}: {case.rubric.description}",
            *[
                f"- {criterion.name} ({'required' if criterion.required else 'optional'}, "
                f"weight={criterion.weight}): {criterion.description}"
                for criterion in case.rubric.criteria
            ],
            "",
            "# Rollout",
            conversation,
            "",
            "# 输出要求",
            "返回 JSON，不要输出 markdown。",
            "JSON schema:",
            json.dumps(
                {
                    "passed": True,
                    "score": 0.0,
                    "feedback": ["string"],
                    "criterion_results": [
                        {
                            "criterion_name": "string",
                            "passed": True,
                            "score": 0.0,
                            "feedback": ["string"],
                            "evidence": ["string"],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "score 必须是 0 到 1 之间的小数。",
        ]
    )


def _parse_rubric_evaluation(response: Any, case: Case) -> RubricEvaluation:
    payload = parse_json_from_response(response)
    if not isinstance(payload, dict):
        return RubricEvaluation(
            passed=False,
            score=0.0,
            criterion_results=[
                CriterionResult(
                    criterion_name=criterion.name,
                    passed=False,
                    score=0.0,
                    feedback=["Evaluator response could not be parsed as JSON."],
                    evidence=[],
                )
                for criterion in case.rubric.criteria
            ],
            feedback=["Evaluator response could not be parsed as JSON."],
            metadata={"parse_failed": True},
        )

    criterion_results = []
    raw_criteria = payload.get("criterion_results")
    if isinstance(raw_criteria, list):
        for item in raw_criteria:
            if not isinstance(item, dict):
                continue
            criterion_results.append(
                CriterionResult(
                    criterion_name=str(item.get("criterion_name") or "unknown"),
                    passed=bool(item.get("passed")),
                    score=_clip_score(item.get("score")),
                    feedback=_string_list(item.get("feedback")),
                    evidence=_string_list(item.get("evidence")),
                    metadata={
                        key: value
                        for key, value in item.items()
                        if key
                        not in {
                            "criterion_name",
                            "passed",
                            "score",
                            "feedback",
                            "evidence",
                        }
                    },
                )
            )

    if not criterion_results:
        score = _clip_score(payload.get("score"))
        criterion_results = [
            CriterionResult(
                criterion_name=criterion.name,
                passed=score >= 1.0 if criterion.required else score > 0,
                score=score,
                feedback=_string_list(payload.get("feedback")),
                evidence=[],
            )
            for criterion in case.rubric.criteria
        ]

    score = _clip_score(payload.get("score"))
    passed = bool(payload.get("passed")) and all(
        result.passed
        for result in criterion_results
        if _criterion_required(case, result.criterion_name)
    )
    return RubricEvaluation(
        passed=passed,
        score=score,
        criterion_results=criterion_results,
        feedback=_string_list(payload.get("feedback")),
        metadata={
            key: value
            for key, value in payload.items()
            if key not in {"passed", "score", "feedback", "criterion_results"}
        },
    )


def _clip_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _criterion_required(case: Case, name: str) -> bool:
    for criterion in case.rubric.criteria:
        if criterion.name == name:
            return criterion.required
    return False


def _description_tokens(description: str, min_chars: int) -> list[str]:
    import re

    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", description)
    return [token for token in tokens if len(token) >= min_chars]


def make_message(role: str, content: str, message_id: str) -> Message:
    """Small helper for tests/adapters that need framework-native messages."""

    return Message(id=message_id, role=role, parts=[TextPart(text=content)])
