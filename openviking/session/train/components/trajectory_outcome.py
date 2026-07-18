# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deterministic evaluation projection for persisted trajectory memories."""

from __future__ import annotations

import json
import re
from typing import Any

from openviking.session.memory.dataclass import ResolvedOperations
from openviking.session.train.domain import RubricEvaluation

_TRAJECTORY_MEMORY_TYPE = "trajectories"
_OUTCOME_EVIDENCE_HEADING = "## Outcome Evidence"
_OUTCOME_LINE_RE = re.compile(r"(?m)^- Outcome:\s*[^\n]*$")


def render_outcome_evidence(evaluation: RubricEvaluation | None) -> str:
    """Render a compact, stable projection without evaluator interpretation."""

    if evaluation is None:
        return _render_section(
            passed="unknown",
            database_state="unknown",
            matched_actions=[],
            missing_actions=[],
            communication_present=[],
            communication_missing=[],
            final_state="unknown",
        )

    evaluation_result = _structured_evaluation_result(evaluation)
    if evaluation_result is None:
        return _render_section(
            passed=_bool_text(evaluation.passed),
            database_state="unknown",
            matched_actions=[],
            missing_actions=[],
            communication_present=[],
            communication_missing=[],
            final_state=_generic_final_state(evaluation),
        )

    matched_actions: list[str] = []
    missing_actions: list[str] = []
    for check in evaluation_result.get("action_checks") or []:
        if not isinstance(check, dict):
            continue
        action = check.get("action")
        if not isinstance(action, dict):
            continue
        rendered = _render_action(action)
        if check.get("action_match") is True:
            matched_actions.append(rendered)
        else:
            missing_actions.append(rendered)

    communication_present: list[str] = []
    communication_missing: list[str] = []
    for check in evaluation_result.get("communicate_checks") or []:
        if not isinstance(check, dict):
            continue
        rendered = _compact_json(check.get("info"))
        if check.get("met") is True:
            communication_present.append(rendered)
        else:
            communication_missing.append(rendered)

    return _render_section(
        passed=_bool_text(evaluation.passed),
        database_state=_database_state(evaluation_result),
        matched_actions=matched_actions,
        missing_actions=missing_actions,
        communication_present=communication_present,
        communication_missing=communication_missing,
        final_state="passed" if evaluation.passed else "failed",
    )


def attach_outcome_evidence(
    operations: ResolvedOperations,
    evaluation: RubricEvaluation | None,
) -> ResolvedOperations:
    """Return operations enriched with one system-owned outcome section."""

    enriched = operations.model_copy(deep=True)
    section = render_outcome_evidence(evaluation)
    for operation in enriched.upsert_operations or []:
        if operation.memory_type != _TRAJECTORY_MEMORY_TYPE:
            continue
        fields = operation.memory_fields
        content = str(fields.get("content") or "").rstrip()
        if _OUTCOME_EVIDENCE_HEADING in content:
            raise ValueError("Trajectory LLM content must not contain Outcome Evidence")
        if evaluation is not None:
            outcome = "success" if evaluation.passed else "failure"
            fields["outcome"] = outcome
            content = _replace_rendered_outcome(content, outcome)
        fields["content"] = f"{content}\n\n{section}" if content else section
    return enriched


def _render_section(
    *,
    passed: str,
    database_state: str,
    matched_actions: list[str],
    missing_actions: list[str],
    communication_present: list[str],
    communication_missing: list[str],
    final_state: str,
) -> str:
    return "\n".join(
        [
            _OUTCOME_EVIDENCE_HEADING,
            f"- Passed: {passed}",
            f"- Database state: {database_state}",
            f"- Matched actions: {_render_items(matched_actions)}",
            f"- Missing or mismatched actions: {_render_items(missing_actions)}",
            f"- Communication present: {_render_items(communication_present)}",
            f"- Communication missing: {_render_items(communication_missing)}",
            f"- Final state: {final_state}",
        ]
    )


def _structured_evaluation_result(evaluation: RubricEvaluation) -> dict[str, Any] | None:
    metadata = evaluation.metadata if isinstance(evaluation.metadata, dict) else {}
    result = metadata.get("evaluation_result")
    return result if isinstance(result, dict) else None


def _database_state(evaluation_result: dict[str, Any]) -> str:
    db_check = evaluation_result.get("db_check")
    if not isinstance(db_check, dict):
        return "unknown"
    db_match = db_check.get("db_match")
    if db_match is True:
        return "matched"
    if db_match is False:
        return "mismatched"
    return "unknown"


def _generic_final_state(evaluation: RubricEvaluation) -> str:
    failed: list[str] = []
    for criterion in evaluation.criterion_results:
        if criterion.passed:
            continue
        evidence = [_compact_text(item) for item in criterion.evidence if _compact_text(item)]
        item = str(criterion.criterion_name or "unnamed criterion")
        if evidence:
            item += f": {'; '.join(evidence)}"
        failed.append(item)
    if failed:
        return "; ".join(failed)
    return "passed" if evaluation.passed else "failed"


def _render_action(action: dict[str, Any]) -> str:
    return f"{str(action.get('name') or 'unknown')}({_compact_json(action.get('arguments'))})"


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return _compact_text(value)


def _render_items(items: list[str]) -> str:
    return "; ".join(item for item in items if item) or "none"


def _replace_rendered_outcome(content: str, outcome: str) -> str:
    replacement = f"- Outcome: {outcome}"
    if _OUTCOME_LINE_RE.search(content):
        return _OUTCOME_LINE_RE.sub(replacement, content, count=1)
    lines = content.splitlines()
    if lines:
        lines.insert(1, replacement)
        return "\n".join(lines)
    return replacement


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())
