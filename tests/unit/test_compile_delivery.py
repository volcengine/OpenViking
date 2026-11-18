"""Versioned completion evidence validates conservatively without content leaks."""

from copy import deepcopy

import pytest

from openviking.utils.compile_delivery import completion_evidence

COMPLETE = {
    "completion_status": "complete",
    "coverage_kind": "assignment_delivery_v1",
    "source_coverage": {"delivered": 1},
    "failure_summary": [],
}


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        True,
        {},
        {"completion_status": "complete"},
        {**COMPLETE, "source_coverage": {"delivered": True}},
        {**COMPLETE, "source_coverage": {"delivered": -1}},
        {**COMPLETE, "source_coverage": {"delivered": 2**64}},
        {**COMPLETE, "source_coverage": {"unrecognized": 1}},
        {**COMPLETE, "coverage_kind": "unknown_future_version"},
        {**COMPLETE, "failure_summary": "synthetic sensitive content"},
        {**COMPLETE, "failure_summary": ["synthetic sensitive content"]},
    ],
)
def test_missing_and_malformed_proof_is_unknown(value):
    assert completion_evidence(value)["completion_status"] == "unknown"
    assert "synthetic sensitive content" not in str(completion_evidence(value))


@pytest.mark.parametrize("state", ["pending", "failed", "prepared", "unreferenced"])
def test_reported_complete_cannot_hide_known_input_gaps(state):
    value = {**COMPLETE, "source_coverage": {"delivered": 1, state: 1}}
    before = deepcopy(value)
    result = completion_evidence(value)
    assert result["completion_status"] == "partial"
    assert result["failure_summary"] == ["input_gaps"]
    assert value == before


def test_supported_complete_and_kernel_verified_noop():
    assert completion_evidence(COMPLETE)["completion_status"] == "complete"
    assert completion_evidence({}, verified_noop=True)["completion_status"] == "complete"
    assert (
        completion_evidence({"completion_status": "partial"}, verified_noop=True)[
            "completion_status"
        ]
        == "partial"
    )


def test_only_completion_fields_and_allowlisted_codes_are_returned():
    value = {
        **COMPLETE,
        "completion_status": "partial",
        "error": "synthetic payload",
        "api_key": "synthetic",
        "failure_summary": ["execution_failures", "synthetic payload"],
    }
    result = completion_evidence(value)
    assert result["failure_summary"] == ["execution_failures"]
    assert result["completion_status"] == "partial"
    assert set(result) == {
        "completion_status",
        "coverage_kind",
        "source_coverage",
        "failure_summary",
    }
