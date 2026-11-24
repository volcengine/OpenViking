# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult


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


@pytest.mark.parametrize("errors", [(), ("valid", 1)])
def test_result_rejects_invalid_structured_errors(errors):
    with pytest.raises(ValueError):
        ProcessResult.failed("failure", errors=errors)
