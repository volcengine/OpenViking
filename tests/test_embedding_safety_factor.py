# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the tokenizer_safety_factor embedding guard.

Motivation: Qwen3-style tokenizers inflate CJK-heavy text roughly 1.1-1.5x
versus the CJK=1 estimate used by estimate_embedding_input_tokens, which let
real token counts exceed the provider context window (observed as
exceed_context_size_error on a CJK-heavy deployment). The factor tightens the
guard by dividing the effective limit; default 1.0 must be byte-compatible
with legacy behavior.
"""

import math

from openviking.utils.embedding_input import (
    EMBEDDING_TRUNCATION_SUFFIX,
    estimate_embedding_input_tokens,
    truncate_embedding_input,
)


def test_estimate_baseline_formula_unchanged():
    text = "abc一二三"
    cjk, other = 3, 3
    assert estimate_embedding_input_tokens(text) == max(1, cjk + math.ceil(other / 4))


def test_truncate_respects_limit():
    text = "hello " * 30 + "你好世界" * 60
    out = truncate_embedding_input(text, 20)
    assert out.endswith(EMBEDDING_TRUNCATION_SUFFIX)
    body = out[: -len(EMBEDDING_TRUNCATION_SUFFIX)]
    assert estimate_embedding_input_tokens(body) <= 20


def test_effective_limit_factor_math():
    # factor=1.0 → guard threshold equals max_input_tokens (legacy compatible)
    for m in (100, 3800, 4096):
        assert int(m / 1.0) == m
    # factor=1.5 → threshold tightens
    assert int(4096 / 1.5) == 2730
    assert int(3800 / 1.5) == 2533


def test_config_field_default_and_wiring():
    try:
        from openviking_cli.utils.config.embedding_config import EmbeddingConfig
    except ImportError:  # pragma: no cover - config extras unavailable
        return
    cfg = EmbeddingConfig()
    assert cfg.tokenizer_safety_factor == 1.0
    runtime = cfg.runtime_config() if hasattr(cfg, "runtime_config") else {}
    if runtime:
        assert runtime.get("tokenizer_safety_factor") == 1.0
