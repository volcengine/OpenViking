# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAI request defaults and explicitly configured reasoning effort."""

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking_cli.utils.config.vlm_config import VLMConfig


class TestOpenAITextCompletionParams:
    """OpenAI defaults must not gate compatible-provider configuration."""

    @pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-6-luna", "GPT-7"])
    def test_reasoning_families_use_max_completion_tokens(self, model):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": model,
                "api_base": "https://api.openai.com/v1",
                "max_tokens": 512,
            }
        )
        kwargs = vlm._build_text_kwargs(prompt="hi", max_tokens=128)
        assert kwargs["max_completion_tokens"] == 128
        assert "max_tokens" not in kwargs
        assert "temperature" not in kwargs
        assert kwargs["reasoning_effort"] == "low"

    def test_o3_mini_uses_max_completion_tokens(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "o3-mini",
                "api_base": "https://api.openai.com/v1",
                "max_tokens": 256,
            }
        )
        kwargs = vlm._build_text_kwargs(prompt="hi")
        assert kwargs["max_completion_tokens"] == 256
        assert "max_tokens" not in kwargs
        assert "temperature" not in kwargs
        assert kwargs["reasoning_effort"] == "low"

    def test_reasoning_effort_overridable_via_config(self):
        vlm = VLMConfig(
            provider="glm",
            model="glm-5.3-flash",
            api_key="sk-test",
            max_tokens=512,
            temperature=0.3,
            reasoning_effort="high",
        ).get_vlm_instance()
        kwargs = vlm._build_text_kwargs(prompt="hi", max_tokens=128)
        assert kwargs["reasoning_effort"] == "high"
        assert kwargs["max_tokens"] == 128
        assert "max_completion_tokens" not in kwargs
        assert kwargs["temperature"] == 0.3

    def test_gpt4o_mini_keeps_max_tokens_and_temperature(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-4o-mini",
                "api_base": "https://api.openai.com/v1",
                "max_tokens": 512,
                "temperature": 0.5,
            }
        )
        kwargs = vlm._build_text_kwargs(prompt="hi")
        assert kwargs["max_tokens"] == 512
        assert "max_completion_tokens" not in kwargs
        assert kwargs["temperature"] == 0.5
        assert "reasoning_effort" not in kwargs

    def test_reasoning_model_without_max_tokens_omits_both(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-5",
                "api_base": "https://api.openai.com/v1",
            }
        )
        kwargs = vlm._build_text_kwargs(prompt="hi")
        assert "max_tokens" not in kwargs
        assert "max_completion_tokens" not in kwargs
        assert "temperature" not in kwargs

    @pytest.mark.parametrize(
        "model",
        [
            "gpt-5.4",
            "gpt-5.5-2026-04-23",
            "gpt-5.6-luna",
            "gpt-6-sol",
            "gpt-6-luna-2026-09-30",
        ],
    )
    def test_chat_tool_models_disable_reasoning(self, model):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": model,
                "api_base": "https://api.openai.com/v1",
                "reasoning_effort": "high",
            }
        )

        kwargs = vlm._build_text_kwargs(
            prompt="hi",
            tools=[{"type": "function", "function": {"name": "read"}}],
        )

        assert kwargs["reasoning_effort"] == "none"

    def test_gpt6_luna_keeps_configured_reasoning_without_tools(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-6-luna",
                "api_base": "https://api.openai.com/v1",
                "reasoning_effort": "high",
            }
        )

        kwargs = vlm._build_text_kwargs(prompt="hi")

        assert kwargs["reasoning_effort"] == "high"


class TestOpenAIVisionCompletionParams:
    """Vision requests preserve the same OpenAI defaults as text requests."""

    def test_gpt5_mini_vision_uses_max_completion_tokens(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-5-mini",
                "api_base": "https://api.openai.com/v1",
                "max_tokens": 1024,
            }
        )
        kwargs = vlm._build_vision_kwargs(prompt="describe this")
        assert kwargs["max_completion_tokens"] == 1024
        assert "max_tokens" not in kwargs
        assert "temperature" not in kwargs
        assert kwargs["reasoning_effort"] == "low"

    def test_gpt4o_vision_keeps_max_tokens_and_temperature(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-4o",
                "api_base": "https://api.openai.com/v1",
                "max_tokens": 1024,
                "temperature": 0.2,
            }
        )
        kwargs = vlm._build_vision_kwargs(prompt="describe this")
        assert kwargs["max_tokens"] == 1024
        assert "max_completion_tokens" not in kwargs
        assert kwargs["temperature"] == 0.2

    def test_gpt6_luna_vision_tools_disable_reasoning(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-6-luna",
                "api_base": "https://api.openai.com/v1",
                "reasoning_effort": "high",
            }
        )

        kwargs = vlm._build_vision_kwargs(
            prompt="describe this",
            tools=[{"type": "function", "function": {"name": "read"}}],
        )

        assert kwargs["reasoning_effort"] == "none"
