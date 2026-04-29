# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for thinking-mode control in the OpenAI VLM backend.

Covers:
- Auto-detection of thinking-capable providers (DashScope, DeepSeek)
- Config-driven ``enable_thinking`` override for any endpoint
- Provider-specific parameter formats (DashScope vs DeepSeek)
- extra_body merge semantics (no silent overwrite)
"""

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vlm(model="gpt-4o", api_base="https://api.openai.com/v1", **extra):
    config = {"api_key": "sk-test", "model": model, "api_base": api_base, **extra}
    return OpenAIVLM(config)


# ===========================================================================
# Provider detection
# ===========================================================================


class TestDetectThinkingProvider:
    """_detect_thinking_provider() should identify DashScope and DeepSeek hosts."""

    @pytest.mark.parametrize(
        "api_base",
        [
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-intl.aliyuncs.com/v1",
        ],
    )
    def test_dashscope_hosts(self, api_base):
        assert _make_vlm(api_base=api_base)._detect_thinking_provider() == "dashscope"

    def test_dashscope_model_prefix(self):
        assert _make_vlm(model="dashscope/qwen-plus")._detect_thinking_provider() == "dashscope"

    def test_deepseek_host(self):
        assert _make_vlm(
            model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1"
        )._detect_thinking_provider() == "deepseek"

    def test_openai_not_detected(self):
        assert _make_vlm()._detect_thinking_provider() is None

    def test_no_api_base(self):
        vlm = OpenAIVLM({"api_key": "sk-test", "model": "gpt-4o"})
        assert vlm._detect_thinking_provider() is None

    def test_unknown_host(self):
        assert _make_vlm(api_base="https://api.example.com/v1")._detect_thinking_provider() is None


# ===========================================================================
# _build_thinking_extra_body — auto-detect mode (enable_thinking unset)
# ===========================================================================


class TestBuildThinkingExtraBodyAutoDetect:
    """When enable_thinking is unset, only emit params for detected providers."""

    def test_deepseek_thinking_false(self):
        vlm = _make_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        assert vlm._build_thinking_extra_body(thinking=False) == {
            "thinking": {"type": "disabled"}
        }

    def test_deepseek_thinking_true_returns_none(self):
        """DeepSeek enables thinking by default — no param needed when thinking=True."""
        vlm = _make_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        assert vlm._build_thinking_extra_body(thinking=True) is None

    def test_dashscope_thinking_false(self):
        vlm = _make_vlm(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
        assert vlm._build_thinking_extra_body(thinking=False) == {"enable_thinking": False}

    def test_dashscope_thinking_true(self):
        vlm = _make_vlm(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
        assert vlm._build_thinking_extra_body(thinking=True) == {"enable_thinking": True}

    def test_openai_returns_none(self):
        """Non-detected providers should get no thinking params."""
        vlm = _make_vlm()
        assert vlm._build_thinking_extra_body(thinking=False) is None
        assert vlm._build_thinking_extra_body(thinking=True) is None


# ===========================================================================
# _build_thinking_extra_body — explicit config mode
# ===========================================================================


class TestBuildThinkingExtraBodyExplicitConfig:
    """When enable_thinking is explicitly set, force behavior for any endpoint."""

    def test_enable_thinking_false_on_openai(self):
        """Explicit enable_thinking=false on unknown endpoint uses default format."""
        vlm = _make_vlm(enable_thinking=False)
        assert vlm._build_thinking_extra_body(thinking=False) == {"enable_thinking": False}

    def test_enable_thinking_true_on_openai(self):
        vlm = _make_vlm(enable_thinking=True)
        assert vlm._build_thinking_extra_body(thinking=True) == {"enable_thinking": True}

    def test_enable_thinking_false_on_deepseek(self):
        """Explicit config + detected DeepSeek host → DeepSeek param format."""
        vlm = _make_vlm(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com/v1",
            enable_thinking=False,
        )
        assert vlm._build_thinking_extra_body(thinking=False) == {
            "thinking": {"type": "disabled"}
        }

    def test_enable_thinking_false_on_dashscope(self):
        """Explicit config + detected DashScope host → DashScope param format."""
        vlm = _make_vlm(
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            enable_thinking=False,
        )
        assert vlm._build_thinking_extra_body(thinking=False) == {"enable_thinking": False}


# ===========================================================================
# _apply_provider_specific_extra_body — merge semantics
# ===========================================================================


class TestApplyProviderSpecificExtraBodyMerge:
    """extra_body should be merged, not overwritten."""

    def test_merges_with_existing_extra_body(self):
        vlm = _make_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = {"extra_body": {"existing_param": "kept"}}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert kwargs["extra_body"] == {
            "existing_param": "kept",
            "thinking": {"type": "disabled"},
        }

    def test_creates_extra_body_when_absent(self):
        vlm = _make_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = {}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_no_extra_body_for_unsupported_provider(self):
        vlm = _make_vlm()
        kwargs = {}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert "extra_body" not in kwargs

    def test_thinking_param_overrides_same_key_in_existing(self):
        """If existing extra_body has enable_thinking, our value should win."""
        vlm = _make_vlm(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
        kwargs = {"extra_body": {"enable_thinking": True, "other": "val"}}
        vlm._apply_provider_specific_extra_body(kwargs, thinking=False)
        assert kwargs["extra_body"]["enable_thinking"] is False
        assert kwargs["extra_body"]["other"] == "val"


# ===========================================================================
# End-to-end: _build_text_kwargs / _build_vision_kwargs integration
# ===========================================================================


class TestBuildKwargsIntegration:
    """Thinking params flow through the full kwargs build pipeline."""

    def test_deepseek_text_kwargs(self):
        vlm = _make_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = vlm._build_text_kwargs(prompt="test", thinking=False)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_deepseek_vision_kwargs(self):
        vlm = _make_vlm(model="deepseek-v4-flash", api_base="https://api.deepseek.com/v1")
        kwargs = vlm._build_vision_kwargs(prompt="describe", thinking=False)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_openai_no_extra_body(self):
        vlm = _make_vlm()
        kwargs = vlm._build_text_kwargs(prompt="test", thinking=False)
        assert "extra_body" not in kwargs

    def test_explicit_config_on_unknown_endpoint(self):
        vlm = _make_vlm(enable_thinking=False)
        kwargs = vlm._build_text_kwargs(prompt="test", thinking=False)
        assert kwargs["extra_body"] == {"enable_thinking": False}
