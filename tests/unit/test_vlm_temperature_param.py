# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the ``temperature=None`` opt-out in the VLM backends."""

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


def _make_litellm(model: str, **extra_config) -> LiteLLMVLMProvider:
    config = {"model": model, "provider": "litellm", **extra_config}
    return LiteLLMVLMProvider(config)


class TestLiteLLMTemperatureOptOut:
    def test_numeric_temperature_is_sent(self):
        vlm = _make_litellm("gpt-4o", temperature=0.3)
        model = vlm._resolve_model("gpt-4o")
        kwargs = vlm._build_kwargs(model, [{"role": "user", "content": "hi"}])
        assert kwargs["temperature"] == 0.3

    def test_zero_temperature_is_sent(self):
        vlm = _make_litellm("gpt-4o", temperature=0.0)
        model = vlm._resolve_model("gpt-4o")
        kwargs = vlm._build_kwargs(model, [{"role": "user", "content": "hi"}])
        assert kwargs["temperature"] == 0.0

    def test_none_temperature_is_omitted(self):
        vlm = _make_litellm("claude-sonnet-5", temperature=None)
        model = vlm._resolve_model("claude-sonnet-5")
        kwargs = vlm._build_kwargs(model, [{"role": "user", "content": "hi"}])
        assert "temperature" not in kwargs


class TestOpenAITemperatureOptOut:
    def test_numeric_temperature_is_sent(self):
        vlm = OpenAIVLM({"model": "gpt-4o", "api_key": "sk-test", "temperature": 0.5})
        kwargs = vlm._build_text_kwargs(prompt="hi")
        assert kwargs["temperature"] == 0.5

    def test_none_temperature_is_omitted(self):
        vlm = OpenAIVLM({"model": "gpt-4o", "api_key": "sk-test", "temperature": None})
        kwargs = vlm._build_text_kwargs(prompt="hi")
        assert "temperature" not in kwargs
