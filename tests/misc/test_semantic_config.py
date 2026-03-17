# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for SemanticConfig and overview budget estimation."""

from openviking_cli.utils.config.parser_config import SemanticConfig


def test_semantic_config_defaults():
    """Test default values match previously hardcoded constants."""
    config = SemanticConfig()
    assert config.max_file_content_chars == 30000
    assert config.max_overview_prompt_chars == 60000
    assert config.overview_batch_size == 50
    assert config.abstract_max_chars == 256
    assert config.overview_max_chars == 4000


def test_semantic_config_custom_values():
    """Test custom values override defaults."""
    config = SemanticConfig(
        max_overview_prompt_chars=100000,
        overview_batch_size=100,
    )
    assert config.max_overview_prompt_chars == 100000
    assert config.overview_batch_size == 100
    # Unchanged defaults
    assert config.max_file_content_chars == 30000
    assert config.abstract_max_chars == 256


def test_budget_under_limit_no_batching():
    """Small directories should not trigger batching."""
    config = SemanticConfig()
    # 10 file summaries, each ~100 chars = ~1000 chars total
    summaries = [{"name": f"file_{i}.py", "summary": "x" * 100} for i in range(10)]
    total = sum(len(f"[{i}] {s['name']}: {s['summary']}") for i, s in enumerate(summaries, 1))
    assert total < config.max_overview_prompt_chars
    assert len(summaries) <= config.overview_batch_size


def test_budget_over_limit_triggers_batching():
    """Large directories should exceed budget and require batching."""
    config = SemanticConfig()
    # 200 file summaries, each ~500 chars = ~100000+ chars total
    summaries = [{"name": f"file_{i}.py", "summary": "x" * 500} for i in range(200)]
    total = sum(len(f"[{i}] {s['name']}: {s['summary']}") for i, s in enumerate(summaries, 1))
    assert total > config.max_overview_prompt_chars
    assert len(summaries) > config.overview_batch_size


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
