#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared private helpers for the Tau2 vikingbot and native rollout executors.

These are intentionally underscore-prefixed: they remain an internal surface
between the two executor implementations and the tests that reach through
``rollout_executor.py`` re-exports. Do not import them from outside the
``benchmark.tau2.train`` package.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.encoders import jsonable_encoder

from openviking.message import Message, TextPart
from openviking.session.train import Case, CriterionResult, RubricEvaluation


def _message(
    message_id: str,
    role: str,
    text: str,
    *,
    created_at: str | None = None,
) -> Message:
    return Message(id=message_id, role=role, parts=[TextPart(text=text)], created_at=created_at)


def _metadata_message(
    message_id: str,
    text: str,
    *,
    created_at: str | None = None,
) -> Message:
    return _message(message_id, "user", text, created_at=created_at)


def _is_communicate_with_user(tool_name: str) -> bool:
    return tool_name == "communicate_with_user"


def _communicate_text_from_tool_input(tool_input: dict[str, Any] | None) -> str:
    if not isinstance(tool_input, dict):
        return ""
    content = tool_input.get("content")
    if content is None:
        return ""
    return str(content)


def _case_trial(case: Case) -> Any:
    return case.input.get("eval_trial", case.input.get("train_trial"))


def _tau2_evaluation(*, reward: Any, evaluation_result: Any, source: str = "tau2") -> RubricEvaluation:
    score = _safe_float(reward, default=0.0)
    passed = score >= 1.0
    feedback = [] if passed else ["tau2 environment reward is below 1.0."]
    evaluation_jsonable = _to_jsonable(evaluation_result)
    if evaluation_jsonable is not None:
        feedback.append(_stringify(evaluation_jsonable))
    return RubricEvaluation(
        passed=passed,
        score=score,
        criterion_results=[
            CriterionResult(
                criterion_name="tau2_reward",
                passed=passed,
                score=score,
                feedback=feedback,
                evidence=[_stringify(evaluation_jsonable)] if evaluation_jsonable is not None else [],
                metadata={"reward": score},
            )
        ],
        feedback=feedback,
        metadata={
            "source": source,
            "reward": score,
            "evaluation_result": evaluation_jsonable,
        },
    )


def _as_tool_input(args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {"arguments": args}
        if isinstance(parsed, dict):
            return parsed
        return {"arguments": parsed}
    return {"arguments": args}


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_jsonable(value: Any) -> Any:
    return jsonable_encoder(value)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(_to_jsonable(value), ensure_ascii=False, sort_keys=True)
