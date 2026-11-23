# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for SemanticConfig."""

import pytest

from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from openviking_cli.utils.config.parser_config import SemanticConfig


def test_semantic_config_defaults():
    """Test default values match previously hardcoded constants."""
    config = SemanticConfig()
    assert config.max_file_content_chars == 30000
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


def test_semantic_config_custom_values():
    config = OpenVikingConfig.from_dict(
        {"semantic": {"max_overview_prompt_chars": 4_000, "overview_sample_limit": 16}}
    )
    semantic = config.to_dict()["semantic"]
    assert "max_overview_prompt_chars" not in semantic
    assert config.semantic.overview_sample_limit == 16


def test_abstract_truncation():
    """Test abstract is truncated to abstract_max_chars."""
    config = SemanticConfig(abstract_max_chars=100)
    abstract = "x" * 200
    if len(abstract) > config.abstract_max_chars:
        abstract = abstract[: config.abstract_max_chars - 3] + "..."
    assert len(abstract) == 100
    assert abstract.endswith("...")


def test_overview_truncation():
    """Test overview is truncated to overview_max_chars."""
    config = SemanticConfig(overview_max_chars=500)
    overview = "x" * 1000
    if len(overview) > config.overview_max_chars:
        overview = overview[: config.overview_max_chars]
    assert len(overview) == 500


def test_batch_splitting():
    """Test batch splitting logic produces correct batch count."""
    config = SemanticConfig(overview_batch_size=50)
    summaries = [{"name": f"f{i}.py", "summary": "s"} for i in range(120)]
    batches = [
        summaries[i : i + config.overview_batch_size]
        for i in range(0, len(summaries), config.overview_batch_size)
    ]
    assert len(batches) == 3  # 50 + 50 + 20
    assert len(batches[0]) == 50
    assert len(batches[1]) == 50
    assert len(batches[2]) == 20


def test_memory_chunk_config_custom():
    """Custom memory chunk config values work."""
    config = SemanticConfig(memory_chunk_chars=500, memory_chunk_overlap=50)
    assert config.memory_chunk_chars == 500
    assert config.memory_chunk_overlap == 50


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
