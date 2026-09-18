# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for retrieval recall_min_score configuration."""

import pytest
from pydantic import ValidationError


def test_recall_min_score_defaults_to_0_35():
    """The shipped default threshold stays 0.35 when ov.conf says nothing."""
    from openviking_cli.utils.config.retrieval_config import RetrievalConfig

    assert RetrievalConfig().recall_min_score == 0.35


def test_recall_min_score_parsed_from_config():
    """A deployed ov.conf can lower or raise the default threshold."""
    from openviking_cli.utils.config.retrieval_config import RetrievalConfig

    assert RetrievalConfig(**{"recall_min_score": 0.3}).recall_min_score == 0.3
    assert RetrievalConfig(recall_min_score=0.5).recall_min_score == 0.5


def test_recall_min_score_rejects_out_of_range_values():
    """The threshold is a score in [0, 1]; anything else is a config error."""
    from openviking_cli.utils.config.retrieval_config import RetrievalConfig

    with pytest.raises(ValidationError):
        RetrievalConfig(recall_min_score=-0.1)
    with pytest.raises(ValidationError):
        RetrievalConfig(recall_min_score=1.5)
