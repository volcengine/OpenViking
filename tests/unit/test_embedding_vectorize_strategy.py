#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def _cfg(**kwargs):
    return EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="openai",
            model="text-embedding-3-small",
            api_base="http://localhost:8080/v1",
            dimension=1536,
        ),
        **kwargs,
    )


def test_embedding_text_source_validation_accepts_supported_values():
    for value in ["summary_first", "summary_only", "content_only"]:
        cfg = _cfg(text_source=value)
        assert cfg.text_source == value


def test_embedding_text_source_defaults_to_content_only():
    cfg = _cfg()
    assert cfg.text_source == "content_only"
    assert cfg.max_input_tokens == 4096


@pytest.mark.parametrize("bad_value", ["summary", "content", "auto", ""])
def test_embedding_text_source_validation_rejects_invalid_values(bad_value):
    with pytest.raises(ValueError, match="embedding.text_source"):
        _cfg(text_source=bad_value)


def test_embedding_max_input_tokens_validation_accepts_reasonable_value():
    cfg = _cfg(max_input_tokens=1000)
    assert cfg.max_input_tokens == 1000


def test_embedding_max_input_tokens_validation_rejects_too_small_value():
    with pytest.raises(ValueError):
        _cfg(max_input_tokens=10)
