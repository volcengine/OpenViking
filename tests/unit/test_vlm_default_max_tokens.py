# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for the OpenAI VLM default ``max_tokens`` fallback (issue #2751).

When ``max_tokens`` is not configured, the OpenAI VLM backend falls back to a default
that must not exceed the completion-token cap of the backend's own default model
(``gpt-4o-mini`` / ``gpt-4o``, capped at 16384 completion tokens). The previous
fallback of 32768 produced an HTTP 400 ("max_tokens is too large ... supports at most
16384 completion tokens") that the memory-extraction path swallowed, silently yielding
0 extracted memories for default-configured deployments.
"""

from openviking.models.vlm.backends.openai_vlm import (
    _DEFAULT_MAX_TOKENS,
    _DEFAULT_REASONING_MAX_TOKENS,
    OpenAIVLM,
)

# gpt-4o / gpt-4o-mini (the backend default model) cap completion at 16384 tokens.
_GPT_4O_COMPLETION_CAP = 16384


def _make_vlm(**overrides):
    config = {
        "api_key": "sk-test",
        "api_base": "https://api.openai.com/v1",
    }
    config.update(overrides)
    return OpenAIVLM(config)


class TestDefaultMaxTokensFallback:
    """Unset ``max_tokens`` must fall back to a value the default model accepts."""

    def test_default_fallback_within_default_model_cap(self):
        assert _DEFAULT_MAX_TOKENS <= _GPT_4O_COMPLETION_CAP

    def test_text_kwargs_default_model_unset_max_tokens(self):
        # No model -> backend default gpt-4o-mini; no max_tokens -> fallback default.
        kwargs = _make_vlm()._build_text_kwargs(prompt="hi")
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["max_tokens"] == _DEFAULT_MAX_TOKENS
        assert kwargs["max_tokens"] <= _GPT_4O_COMPLETION_CAP

    def test_vision_kwargs_default_model_unset_max_tokens(self):
        kwargs = _make_vlm()._build_vision_kwargs(prompt="describe this")
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["max_tokens"] == _DEFAULT_MAX_TOKENS
        assert kwargs["max_tokens"] <= _GPT_4O_COMPLETION_CAP

    def test_explicit_max_tokens_is_respected(self):
        # An explicitly configured max_tokens must override the fallback unchanged.
        vlm = _make_vlm(max_tokens=512)
        assert vlm._build_text_kwargs(prompt="hi")["max_tokens"] == 512
        assert vlm._build_vision_kwargs(prompt="x")["max_tokens"] == 512

    def test_explicit_zero_max_tokens_not_replaced_by_default(self):
        # The fallback fires only when max_tokens is unset (None); an explicit value
        # is passed through unchanged, so the default never silently overrides config.
        vlm = _make_vlm(max_tokens=0)
        assert vlm._build_text_kwargs(prompt="hi")["max_tokens"] == 0
        assert vlm._build_vision_kwargs(prompt="x")["max_tokens"] == 0


class TestReasoningModelDefaultUnchanged:
    """Reasoning models keep their prior 32768 unset default (not lowered by #2751)."""

    def test_reasoning_unset_keeps_prior_default(self):
        # gpt-5 is a reasoning model: unset max_tokens -> prior 32768 via
        # max_completion_tokens, NOT the lowered gpt-4o-family cap. Reasoning models
        # advertise larger completion limits and spend hidden reasoning tokens from
        # this budget, so the #2751 16384 cap must not apply to them.
        for builder in ("_build_text_kwargs", "_build_vision_kwargs"):
            kwargs = getattr(_make_vlm(model="gpt-5"), builder)(prompt="hi")
            assert "max_tokens" not in kwargs
            assert kwargs["max_completion_tokens"] == _DEFAULT_REASONING_MAX_TOKENS
            assert _DEFAULT_REASONING_MAX_TOKENS > _DEFAULT_MAX_TOKENS

    def test_reasoning_explicit_max_tokens_respected(self):
        # An explicit budget on a reasoning model is still honored unchanged.
        vlm = _make_vlm(model="o3", max_tokens=4096)
        assert vlm._build_text_kwargs(prompt="hi")["max_completion_tokens"] == 4096
        assert vlm._build_vision_kwargs(prompt="x")["max_completion_tokens"] == 4096
