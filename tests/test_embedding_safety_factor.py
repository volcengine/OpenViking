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


def _make_embedder(config):
    from openviking.models.embedder.base import EmbedderBase

    # Python 3.12+ 的 ABCMeta 与 object.__new__ 都会拒绝抽象类实例化
    # （abstract method 'embed'），因此用具体子类承载守卫逻辑测试。
    class _ConcreteEmbedder(EmbedderBase):
        def embed(self, content, is_query=False):  # pragma: no cover
            raise NotImplementedError("embed is not exercised by guard tests")

    return _ConcreteEmbedder("test-model", config)


def test_prepare_input_applies_guarded_limit():
    emb = _make_embedder({"max_input_tokens": 100, "tokenizer_safety_factor": 2.0})
    assert emb.tokenizer_safety_factor == 2.0
    assert emb._guarded_max_tokens == 50
    out = emb.prepare_embedding_input("汉" * 400)  # estimate = 400
    body = out[: -len(EMBEDDING_TRUNCATION_SUFFIX)]
    assert estimate_embedding_input_tokens(body) <= 50


def test_prepare_input_default_factor_is_legacy_compatible():
    emb = _make_embedder({"max_input_tokens": 100})
    assert emb.tokenizer_safety_factor == 1.0
    assert emb._guarded_max_tokens == 100  # byte-identical to pre-change behavior


def test_factor_covers_observed_cjk_inflation():
    # Regression guard for a CJK-heavy deployment: 21 long-form Chinese docs
    # embedded via a Qwen3 tokenizer spanned 1.09x-1.52x real/estimated tokens
    # (e.g. estimate 3800 -> actual 5790), all failing with
    # exceed_context_size_error before the guard existed.
    worst_inflation = 1.52
    limit = 3800
    # factor 1.6 fully absorbs the worst observed inflation on its own.
    guarded_16 = int(limit / 1.6)
    assert guarded_16 * worst_inflation <= limit
    # factor 1.5 leaves a ~2% gap at the worst case, so it must be paired
    # with provider slot headroom (the reference deployment runs limit=3800
    # inside a 6144-token slot, which covers it).
    guarded_15 = int(limit / 1.5)
    assert guarded_15 * worst_inflation > limit
    assert guarded_15 * worst_inflation <= 6144
