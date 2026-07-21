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


def _tau2_evaluation(
    *, reward: Any, evaluation_result: Any, source: str = "tau2"
) -> RubricEvaluation:
    score = _safe_float(reward, default=0.0)
    passed = score >= 1.0
    evaluation_jsonable = _to_jsonable(evaluation_result)
    evaluation_data = evaluation_jsonable if isinstance(evaluation_jsonable, dict) else {}
    criterion_results = [
        CriterionResult(
            criterion_name="task_outcome",
            passed=passed,
            score=score,
            feedback=[] if passed else ["Overall task outcome did not receive full reward."],
            evidence=[f"Overall environment reward: {score:g}."],
            metadata={"component": "overall"},
        )
    ]
    environment_state = _environment_state_criterion(evaluation_data)
    if environment_state is not None:
        criterion_results.append(environment_state)
    required_actions = _required_actions_criterion(evaluation_data)
    if required_actions is not None:
        criterion_results.append(required_actions)
    required_communication = _required_communication_criterion(evaluation_data)
    if required_communication is not None:
        criterion_results.append(required_communication)
    return RubricEvaluation(
        passed=passed,
        score=score,
        criterion_results=criterion_results,
        metadata={"source": source, "reward": score},
    )


def _environment_state_criterion(evaluation: dict[str, Any]) -> CriterionResult | None:
    db_check = evaluation.get("db_check")
    if not isinstance(db_check, dict) or not isinstance(db_check.get("db_match"), bool):
        return None
    passed = db_check["db_match"]
    score = _safe_float(db_check.get("db_reward"), default=1.0 if passed else 0.0)
    if passed:
        feedback: list[str] = []
        evidence = [
            "Expected environment state was reached; preserve the actions that produced it."
        ]
    else:
        feedback = ["Final environment state did not match the expected state."]
        evidence = ["Expected environment state was not reached."]
    return CriterionResult(
        criterion_name="environment_state",
        passed=passed,
        score=score,
        feedback=feedback,
        evidence=evidence,
        metadata={"component": "environment_state"},
    )


def _required_actions_criterion(evaluation: dict[str, Any]) -> CriterionResult | None:
    raw_checks = evaluation.get("action_checks")
    if not isinstance(raw_checks, list):
        return None
    checks: list[tuple[str, Any, str, bool]] = []
    for item in raw_checks:
        if not isinstance(item, dict) or not isinstance(item.get("action_match"), bool):
            continue
        action = item.get("action")
        if not isinstance(action, dict):
            continue
        name = action.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        checks.append(
            (
                name,
                action.get("arguments"),
                str(item.get("tool_type") or "action"),
                item["action_match"],
            )
        )
    if not checks:
        return None

    matched_count = sum(1 for *_, matched in checks if matched)
    feedback: list[str] = []
    evidence: list[str] = []
    for name, arguments, tool_type, matched in checks:
        action_text = f"{name}({_stringify(arguments)})"
        if matched:
            evidence.append(
                f"Required {tool_type} action matched; preserve this behavior: {action_text}."
            )
        else:
            feedback.append(f"Missing or mismatched required {tool_type} action: {action_text}.")
            evidence.append(f"Required {tool_type} action was not matched: {action_text}.")
    return CriterionResult(
        criterion_name="required_actions",
        passed=matched_count == len(checks),
        score=matched_count / len(checks),
        feedback=feedback,
        evidence=evidence,
        metadata={
            "component": "required_actions",
            "matched_count": matched_count,
            "total_count": len(checks),
        },
    )


def _required_communication_criterion(evaluation: dict[str, Any]) -> CriterionResult | None:
    raw_checks = evaluation.get("communicate_checks")
    if not isinstance(raw_checks, list):
        return None
    checks: list[tuple[Any, bool, str]] = []
    for item in raw_checks:
        if not isinstance(item, dict) or not isinstance(item.get("met"), bool):
            continue
        checks.append((item.get("info"), item["met"], str(item.get("justification") or "")))
    if not checks:
        return None

    met_count = sum(1 for _, met, _ in checks if met)
    feedback: list[str] = []
    evidence: list[str] = []
    for info, met, justification in checks:
        info_text = _stringify(info)
        if met:
            evidence.append(
                f"Required information was communicated; preserve this behavior: {info_text}."
            )
            continue
        feedback.append(
            justification.strip() or f"Required information was not communicated: {info_text}."
        )
        evidence.append(f"Required information was not communicated: {info_text}.")
    return CriterionResult(
        criterion_name="required_communication",
        passed=met_count == len(checks),
        score=met_count / len(checks),
        feedback=feedback,
        evidence=evidence,
        metadata={
            "component": "required_communication",
            "met_count": met_count,
            "total_count": len(checks),
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
