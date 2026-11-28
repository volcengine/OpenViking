# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.retrieval_config import RetrievalConfig


def test_mcp_min_score_preserves_the_existing_default():
    assert RetrievalConfig().mcp_min_score == 0.35


@pytest.mark.parametrize("score", [-0.2, 0.0, 0.35, 1.2])
def test_mcp_min_score_accepts_finite_backend_scores(score):
    assert RetrievalConfig.model_validate({"mcp_min_score": score}).mcp_min_score == score


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -float("inf"), "invalid"])
def test_mcp_min_score_rejects_non_finite_or_invalid_configuration(score):
    with pytest.raises(ValidationError):
        RetrievalConfig.model_validate({"mcp_min_score": score})
