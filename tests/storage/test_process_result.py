# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult


@pytest.mark.parametrize("outcome", list(ProcessOutcome))
def test_result_outcome_and_payload(outcome):
    error = "failure" if outcome is ProcessOutcome.FAILED else None
    result = ProcessResult(outcome, error=error)
    assert result.outcome is outcome
    assert result.error == error
    assert result.value is None


@pytest.mark.parametrize(
    "outcome,error",
    [
        (ProcessOutcome.FAILED, None),
        (ProcessOutcome.SUCCESS, "failure"),
        ("success", None),
    ],
)
def test_result_rejects_inconsistent_state(outcome, error):
    with pytest.raises(ValueError):
        ProcessResult(outcome, error=error)


def test_result_preserves_value_and_empty_error():
    payload = {"value": 1}
    assert ProcessResult.success(payload).value is payload
    assert ProcessResult.failed("").error == ""
