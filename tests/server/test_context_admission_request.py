# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking.server.routers.search import RecallRequest, SearchRequest


def test_context_and_recall_requests_accept_bounded_admission_policy():
    policy = {
        "mode": "shadow",
        "type_min_scores": {"events": 0.5, "resources": 0.45},
        "other_peer_score_delta": 0.08,
    }
    context = SearchRequest.model_validate(
        {"query": "known memory", "mode": "context", "admission": policy}
    )
    recall = RecallRequest.model_validate({"query": "known memory", "admission": policy})

    assert context.admission is not None
    assert context.admission.type_min_scores == {"events": 0.5, "resources": 0.45}
    assert recall.admission == context.admission


def test_admission_is_rejected_in_list_mode():
    with pytest.raises(ValidationError, match="require mode='context'"):
        SearchRequest.model_validate({"query": "list", "admission": {"mode": "shadow"}})


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
def test_context_request_rejects_invalid_admission_policy(admission):
    with pytest.raises(ValidationError):
        SearchRequest.model_validate(
            {"query": "invalid", "mode": "context", "admission": admission}
        )
