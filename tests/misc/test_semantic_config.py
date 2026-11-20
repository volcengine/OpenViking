# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for semantic configuration defaults and validation."""

import pytest

from openviking_cli.utils.config.parser_config import SemanticConfig


def test_semantic_config_defaults():
    """Test default values match previously hardcoded constants."""
    config = SemanticConfig()
    assert config.max_file_content_chars == 30000
    assert config.max_overview_prompt_chars == 60000
    assert config.overview_batch_size == 50
    assert config.overview_sample_limit == 32
    assert config.freshness_refresh_ratio == 0.10
    assert config.abstract_max_chars == 256
    assert config.overview_max_chars == 4000
    assert config.memory_chunk_chars == 2000
    assert config.memory_chunk_overlap == 200


@pytest.mark.parametrize("ratio", [0, -0.1, 1.1])
def test_freshness_refresh_ratio_must_be_a_probability(ratio):
    with pytest.raises(ValueError, match="freshness_refresh_ratio"):
        SemanticConfig(freshness_refresh_ratio=ratio)


@pytest.mark.parametrize(
    ("chunk_chars", "overlap"),
    [
        (0, 0),
        (100, 100),
        (100, 101),
    ],
)
def test_memory_chunk_config_rejects_non_progressing_overlap(chunk_chars, overlap):
    """Memory chunk settings must guarantee chunking advances."""
    with pytest.raises(ValueError, match="memory_chunk"):
        SemanticConfig(memory_chunk_chars=chunk_chars, memory_chunk_overlap=overlap)
