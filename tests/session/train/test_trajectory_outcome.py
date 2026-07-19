# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.session.memory.dataclass import MemoryFile, ResolvedOperation, ResolvedOperations
from openviking.session.train.components.trajectory_outcome import (
    attach_outcome_evidence,
    render_outcome_evidence,
)
from openviking.session.train.domain import CriterionResult, RubricEvaluation


def _operations(
    content: str = "# cancellation\n- Outcome: unknown\n\n## Execution\n- Writes: none",
):
    return ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_fields={
                    "trajectory_name": "cancellation",
                    "outcome": "unknown",
                    "retrieval_anchor": "Stage: final",
                    "content": content,
                },
                memory_type="trajectories",
                uris=["viking://user/u/memories/trajectories/cancellation.md"],
                page_id=1,
            )
        ],
        delete_file_contents=[],
        errors=[],
        resolved_links=[],
    )


def _tau2_evaluation() -> RubricEvaluation:
    return RubricEvaluation(
        passed=False,
        score=0.0,
        criterion_results=[
            CriterionResult(
                criterion_name="task_result",
                passed=False,
                score=0.0,
                feedback=["Do not copy this evaluator explanation."],
                evidence=["The required cancellation was missing."],
            )
        ],
        metadata={
            "evaluation_result": {
                "reward": 0.0,
                "reward_breakdown": {"DB": 0.0, "COMMUNICATE": 1.0},
                "db_check": {"db_match": False, "db_reward": 0.0},
                "action_checks": [
                    {
                        "action": {
                            "name": "cancel_reservation",
                            "arguments": {"reservation_id": "A1"},
                        },
                        "action_match": True,
                        "tool_type": "write",
                    },
                    {
                        "action": {
                            "name": "cancel_reservation",
                            "arguments": {"reservation_id": "A2"},
                        },
                        "action_match": False,
                        "tool_type": "write",
                    },
                ],
                "communicate_checks": [
                    {
                        "info": {"reservation_id": "A1", "status": "cancelled"},
                        "met": True,
                        "justification": "Do not persist this explanation.",
                    },
                    {
                        "info": {"reservation_id": "A2", "status": "cancelled"},
                        "met": False,
                        "justification": "Do not persist this explanation either.",
                    },
                ],
            },
            "large_private_metadata": "do not persist",
        },
    )


def test_render_tau2_outcome_evidence_separates_result_categories():
    text = render_outcome_evidence(_tau2_evaluation())

    assert "- Passed: false" in text
    assert "- Database state: mismatched" in text
    matched_line = next(line for line in text.splitlines() if line.startswith("- Matched actions:"))
    missing_line = next(
        line for line in text.splitlines() if line.startswith("- Missing or mismatched actions:")
    )
    assert 'cancel_reservation({"reservation_id":"A1"})' in matched_line
    assert 'cancel_reservation({"reservation_id":"A2"})' not in matched_line
    assert 'cancel_reservation({"reservation_id":"A2"})' in missing_line
    assert 'cancel_reservation({"reservation_id":"A1"})' not in missing_line
    assert '"reservation_id":"A1"' in next(
        line for line in text.splitlines() if line.startswith("- Communication present:")
    )
    assert '"reservation_id":"A2"' in next(
        line for line in text.splitlines() if line.startswith("- Communication missing:")
    )
    assert "reward_breakdown" not in text
    assert "justification" not in text
    assert "large_private_metadata" not in text
    assert "Do not copy" not in text


def test_render_generic_outcome_evidence_preserves_failed_criterion_evidence():
    evaluation = RubricEvaluation(
        passed=False,
        score=0.25,
        criterion_results=[
            CriterionResult(
                criterion_name="required_state",
                passed=False,
                score=0.0,
                feedback=["interpretation omitted"],
                evidence=["reservation A2 remained active"],
            )
        ],
    )

    text = render_outcome_evidence(evaluation)

    assert "- Database state: unknown" in text
    assert "required_state: reservation A2 remained active" in text
    assert "interpretation omitted" not in text


def test_attach_outcome_evidence_owns_structured_and_rendered_outcome():
    operations = attach_outcome_evidence(_operations(), _tau2_evaluation())
    fields = operations.upsert_operations[0].memory_fields

    assert fields["outcome"] == "failure"
    assert "- Outcome: failure" in fields["content"]
    assert fields["content"].count("## Outcome Evidence") == 1


def test_attach_outcome_evidence_without_evaluation_keeps_llm_outcome():
    operations = _operations().model_copy(deep=True)
    operations.upsert_operations[0].memory_fields["outcome"] = "unfinished"
    operations.upsert_operations[0].memory_fields["content"] = (
        "# cancellation\n- Outcome: unfinished\n\n## Execution\n- Writes: none"
    )

    fields = attach_outcome_evidence(operations, None).upsert_operations[0].memory_fields

    assert fields["outcome"] == "unfinished"
    assert "- Outcome: unfinished" in fields["content"]
    assert "- Passed: unknown" in fields["content"]


def test_attach_outcome_evidence_rejects_llm_owned_section():
    operations = _operations(
        "# cancellation\n- Outcome: unknown\n\n## Outcome Evidence\n- Passed: true"
    )

    with pytest.raises(ValueError, match="must not contain"):
        attach_outcome_evidence(operations, _tau2_evaluation())


def test_attach_outcome_evidence_resolves_existing_patch_before_owning_section():
    old_content = (
        "# cancellation\n- Outcome: success\n\n## Execution\n- Writes: none\n\n"
        "## Outcome Evidence\n- Passed: true\n- Final state: passed"
    )
    new_content = "# cancellation\n- Outcome: partial\n\n## Execution\n- Writes: handoff"
    operations = _operations()
    operation = operations.upsert_operations[0]
    operation.old_memory_file_content = MemoryFile(
        uri=operation.uris[0],
        content=old_content,
        memory_type="trajectories",
    )
    operation.memory_fields["content"] = {
        "blocks": [{"search": old_content, "replace": new_content}]
    }

    fields = (
        attach_outcome_evidence(operations, _tau2_evaluation()).upsert_operations[0].memory_fields
    )

    assert fields["outcome"] == "failure"
    assert fields["content"].startswith(
        "# cancellation\n- Outcome: failure\n\n## Execution\n- Writes: handoff"
    )
    assert fields["content"].count("## Outcome Evidence") == 1
    assert "- Passed: false" in fields["content"]
