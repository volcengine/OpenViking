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


def test_embedding_runtime_config_includes_max_input_tokens():
    cfg = _cfg(max_input_tokens=1000)
    embedder = cfg.get_embedder()

    assert embedder.config["max_input_tokens"] == 1000


def test_embedding_max_input_tokens_validation_rejects_too_small_value():
    with pytest.raises(ValueError):
        _cfg(max_input_tokens=10)


# image_vectorization
#
# Regression: PR #2460 added an `image_vectorization` config field that selects
# between `summary_only` (text-only embedding of the VLM summary), `image_only`
# (multimodal embedding of the image bytes), and `image_and_summary` (both).
# The field was inadvertently dropped from EmbeddingConfig during the
# multi-credential refactor in PR #2468, but the consumer in
# `openviking/utils/embedding_utils.py` still reads it via getattr — meaning
# the feature became unreachable in user configs (always defaulting to
# summary_only). These tests pin the field back in place.


def test_embedding_image_vectorization_validation_accepts_supported_values():
    for value in ["summary_only", "image_only", "image_and_summary"]:
        cfg = _cfg(image_vectorization=value)
        assert cfg.image_vectorization == value


def test_embedding_image_vectorization_defaults_to_summary_only():
    cfg = _cfg()
    assert cfg.image_vectorization == "summary_only"


@pytest.mark.parametrize("bad_value", ["image", "summary", "both", "auto", ""])
def test_embedding_image_vectorization_validation_rejects_invalid_values(bad_value):
    with pytest.raises(ValueError, match="embedding.image_vectorization"):
        _cfg(image_vectorization=bad_value)
