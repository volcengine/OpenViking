# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for thinking-mode control across VLM backends.

Covers:
- OpenAI VLM: auto-detect, config-driven enable_thinking, extra_body merge
- LiteLLM VLM: auto-detect via model name, config-driven enable_thinking
- Config override: enable_thinking config overrides thinking call argument
"""

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_openai_vlm(model="gpt-4o", api_base="https://api.openai.com/v1", **extra):
    config = {"api_key": "sk-test", "model": model, "api_base": api_base, **extra}
    return OpenAIVLM(config)


def _make_litellm_vlm(model="gpt-4o", **extra):
    config = {"api_key": "sk-test", "model": model, "provider": "litellm", **extra}
    return LiteLLMVLMProvider(config)


# ===========================================================================
# OpenAI VLM — Provider detection
# ===========================================================================


class TestOpenAIDetectThinkingProvider:
    """_detect_thinking_provider() should identify DashScope and DeepSeek hosts."""

    @pytest.mark.parametrize(
        "api_base",
        [
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-intl.aliyuncs.com/v1",
        ],
    )
    def test_dashscope_hosts(self, api_base):
        assert _make_openai_vlm(api_base=api_base)._detect_thinking_provider() == "dashscope"

    def test_dashscope_model_prefix(self):
        assert _make_openai_vlm(model="dashscope/qwen-plus")._detect_thinking_provider() == "dashscope"

    def test_deepseek_host(self):
        assert _make_openai_vlm(
            model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1"
        )._detect_thinking_provider() == "deepseek"

    def test_openai_not_detected(self):
        assert _make_openai_vlm()._detect_thinking_provider() is None

    def test_no_api_base(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "model": "gpt-4o"})
        assert vlm._detect_thinking_provider() is None

    def test_unknown_host(self):
        assert _make_openai_vlm(api_base="https://api.example.com/v1")._detect_thinking_provider() is None


# ===========================================================================
# OpenAI VLM — Auto-detect mode (enable_thinking unset)
# ===========================================================================


class TestOpenAIAutoDetect:
    """When enable_thinking is unset, only emit params for detected providers."""

    def test_deepseek_thinking_false(self):
        vlm = _make_openai_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        assert vlm._build_thinking_extra_body(thinking=False) == {
            "thinking": {"type": "disabled"}
        }

    def test_deepseek_thinking_true_returns_none(self):
        vlm = _make_openai_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        assert vlm._build_thinking_extra_body(thinking=True) is None

    def test_dashscope_thinking_false(self):
        vlm = _make_openai_vlm(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
        assert vlm._build_thinking_extra_body(thinking=False) == {"enable_thinking": False}

    def test_dashscope_thinking_true(self):
        vlm = _make_openai_vlm(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
        assert vlm._build_thinking_extra_body(thinking=True) == {"enable_thinking": True}

    def test_openai_returns_none(self):
        vlm = _make_openai_vlm()
        assert vlm._build_thinking_extra_body(thinking=False) is None
        assert vlm._build_thinking_extra_body(thinking=True) is None


# ===========================================================================
# OpenAI VLM — Explicit config mode + override
# ===========================================================================


class TestOpenAIExplicitConfig:
    """When enable_thinking is explicitly set, it overrides the thinking argument."""

    def test_enable_thinking_false_overrides_thinking_true(self):
        """Config says false, call says true → config wins."""
        vlm = _make_openai_vlm(enable_thinking=False)
        assert vlm._build_thinking_extra_body(thinking=True) == {"enable_thinking": False}

    def test_enable_thinking_true_overrides_thinking_false(self):
        """Config says true, call says false → config wins."""
        vlm = _make_openai_vlm(enable_thinking=True)
        assert vlm._build_thinking_extra_body(thinking=False) == {"enable_thinking": True}

    def test_enable_thinking_false_on_deepseek(self):
        vlm = _make_openai_vlm(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com/v1",
            enable_thinking=False,
        )
        assert vlm._build_thinking_extra_body(thinking=False) == {
            "thinking": {"type": "disabled"}
        }

    def test_enable_thinking_false_on_deepseek_overrides_true(self):
        """Config false overrides call argument true for DeepSeek."""
        vlm = _make_openai_vlm(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com/v1",
            enable_thinking=False,
        )
        assert vlm._build_thinking_extra_body(thinking=True) == {
            "thinking": {"type": "disabled"}
        }

    def test_enable_thinking_true_on_deepseek(self):
        """Config true on DeepSeek → no param needed (default behavior)."""
        vlm = _make_openai_vlm(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com/v1",
            enable_thinking=True,
        )
        assert vlm._build_thinking_extra_body(thinking=False) is None

    def test_enable_thinking_false_on_dashscope(self):
        vlm = _make_openai_vlm(
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            enable_thinking=False,
        )
        assert vlm._build_thinking_extra_body(thinking=False) == {"enable_thinking": False}


# ===========================================================================
# OpenAI VLM — extra_body merge semantics
# ===========================================================================


class TestOpenAIExtraBodyMerge:
    """extra_body should be merged, not overwritten."""

    def test_merges_with_existing_extra_body(self):
        vlm = _make_openai_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = {"extra_body": {"existing_param": "kept"}}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert kwargs["extra_body"] == {
            "existing_param": "kept",
            "thinking": {"type": "disabled"},
        }

    def test_creates_extra_body_when_absent(self):
        vlm = _make_openai_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = {}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_no_extra_body_for_unsupported_provider(self):
        vlm = _make_openai_vlm()
        kwargs = {}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert "extra_body" not in kwargs

    def test_thinking_param_overrides_same_key_in_existing(self):
        vlm = _make_openai_vlm(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
        kwargs = {"extra_body": {"enable_thinking": True, "other": "val"}}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert kwargs["extra_body"]["enable_thinking"] is False
        assert kwargs["extra_body"]["other"] == "val"


# ===========================================================================
# OpenAI VLM — End-to-end _build_text_kwargs / _build_vision_kwargs
# ===========================================================================


class TestOpenAIBuildKwargsIntegration:
    """Thinking params flow through the full kwargs build pipeline."""

    def test_deepseek_text_kwargs(self):
        vlm = _make_openai_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = vlm._build_text_kwargs(prompt="test", thinking=False)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_deepseek_vision_kwargs(self):
        vlm = _make_openai_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = vlm._build_vision_kwargs(prompt="describe", thinking=False)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_openai_no_extra_body(self):
        vlm = _make_openai_vlm()
        kwargs = vlm._build_text_kwargs(prompt="test", thinking=False)
        assert "extra_body" not in kwargs

    def test_explicit_config_on_unknown_endpoint(self):
        vlm = _make_openai_vlm(enable_thinking=False)
        kwargs = vlm._build_text_kwargs(prompt="test", thinking=True)
        assert kwargs["extra_body"] == {"enable_thinking": False}


# ===========================================================================
# LiteLLM VLM — Auto-detect mode
# ===========================================================================


class TestLiteLLMAutoDetect:
    """LiteLLM detects provider by model name keywords."""

    def test_dashscope_model_thinking_false(self):
        vlm = _make_litellm_vlm(model="qwen-plus")
        result = vlm._build_thinking_extra_body(thinking=False, provider="dashscope")
        assert result == {"enable_thinking": False}

    def test_dashscope_model_thinking_true(self):
        vlm = _make_litellm_vlm(model="qwen-plus")
        result = vlm._build_thinking_extra_body(thinking=True, provider="dashscope")
        assert result == {"enable_thinking": True}

    def test_deepseek_model_thinking_false(self):
        vlm = _make_litellm_vlm(model="deepseek-v4-flash")
        result = vlm._build_thinking_extra_body(thinking=False, provider="deepseek")
        assert result == {"thinking": {"type": "disabled"}}

    def test_deepseek_model_thinking_true_returns_none(self):
        vlm = _make_litellm_vlm(model="deepseek-v4-flash")
        result = vlm._build_thinking_extra_body(thinking=True, provider="deepseek")
        assert result is None

    def test_non_thinking_provider_returns_none(self):
        vlm = _make_litellm_vlm(model="gpt-4o")
        result = vlm._build_thinking_extra_body(thinking=False, provider="openai")
        assert result is None

    def test_no_provider_returns_none(self):
        vlm = _make_litellm_vlm(model="gpt-4o")
        result = vlm._build_thinking_extra_body(thinking=False, provider=None)
        assert result is None


# ===========================================================================
# LiteLLM VLM — Explicit config mode
# ===========================================================================


class TestLiteLLMExplicitConfig:
    """enable_thinking config overrides thinking argument for LiteLLM."""

    def test_enable_thinking_false_overrides_true(self):
        vlm = _make_litellm_vlm(model="gpt-4o", enable_thinking=False)
        result = vlm._build_thinking_extra_body(thinking=True, provider="openai")
        assert result == {"enable_thinking": False}

    def test_enable_thinking_false_deepseek(self):
        vlm = _make_litellm_vlm(model="deepseek-v4-flash", enable_thinking=False)
        result = vlm._build_thinking_extra_body(thinking=True, provider="deepseek")
        assert result == {"thinking": {"type": "disabled"}}

    def test_enable_thinking_true_deepseek(self):
        vlm = _make_litellm_vlm(model="deepseek-v4-flash", enable_thinking=True)
        result = vlm._build_thinking_extra_body(thinking=False, provider="deepseek")
        assert result is None


# ===========================================================================
# LiteLLM VLM — _build_kwargs integration
# ===========================================================================


class TestLiteLLMBuildKwargsIntegration:
    """Thinking params flow through LiteLLM _build_kwargs."""

    def test_dashscope_build_kwargs(self):
        vlm = _make_litellm_vlm(model="qwen-plus")
        model = vlm._resolve_model("qwen-plus")
        kwargs = vlm._build_kwargs(model, [{"role": "user", "content": "hi"}], thinking=False)
        assert kwargs["extra_body"]["enable_thinking"] is False

    def test_deepseek_build_kwargs(self):
        vlm = _make_litellm_vlm(model="deepseek-v4-flash")
        model = vlm._resolve_model("deepseek-v4-flash")
        kwargs = vlm._build_kwargs(model, [{"role": "user", "content": "hi"}], thinking=False)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_deepseek_build_kwargs_thinking_true_no_extra(self):
        vlm = _make_litellm_vlm(model="deepseek-v4-flash")
        model = vlm._resolve_model("deepseek-v4-flash")
        kwargs = vlm._build_kwargs(model, [{"role": "user", "content": "hi"}], thinking=True)
        assert "extra_body" not in kwargs

    def test_openai_build_kwargs_no_extra(self):
        vlm = _make_litellm_vlm(model="gpt-4o")
        model = vlm._resolve_model("gpt-4o")
        kwargs = vlm._build_kwargs(model, [{"role": "user", "content": "hi"}], thinking=False)
        assert "extra_body" not in kwargs
