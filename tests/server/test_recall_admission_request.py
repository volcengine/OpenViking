# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking.server.routers.search import RecallRequest


def test_recall_request_accepts_bounded_admission_policy():
    request = RecallRequest.model_validate(
        {
            "query": "known memory",
            "admission": {
                "mode": "shadow",
                "type_min_scores": {"events": 0.5, "preferences": 0.45},
                "other_peer_score_delta": 0.08,
            },
        }
    )

    assert request.admission is not None
    assert request.admission.mode == "shadow"
    assert request.admission.type_min_scores == {"events": 0.5, "preferences": 0.45}


@pytest.mark.parametrize(
    "admission",
    [
        {"mode": "unknown"},
        {"mode": "enforce", "type_min_scores": {"unknown": 0.5}},
        {"mode": "enforce", "type_min_scores": {"events": 1.1}},
        {"mode": "enforce", "other_peer_score_delta": -0.1},
        {"mode": "enforce", "unexpected": True},
    ],
)
def test_recall_request_rejects_invalid_admission_policy(admission):
    with pytest.raises(ValidationError):
        RecallRequest.model_validate({"query": "invalid", "admission": admission})
