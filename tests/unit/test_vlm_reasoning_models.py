# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for reasoning-model parameter translation in the OpenAI VLM backend.

OpenAI reasoning-model families (gpt-5, o1, o3, o4) reject the `max_tokens` and
non-default `temperature` parameters. They require `max_completion_tokens` and
only accept `temperature=1` (the server default). This module verifies that the
OpenAI VLM backend translates parameters for reasoning models while leaving
non-reasoning models (e.g. gpt-4o-mini) unchanged.
"""

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM, _is_reasoning_model


class TestIsReasoningModel:
    """Pure function: reasoning-model prefix detection."""

    @pytest.mark.parametrize(
        "model",
        [
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano-2025-08-07",
            "GPT-5-Mini",
            "o1",
            "o1-preview",
            "o1-mini",
            "o3",
            "o3-mini",
            "o4-mini",
            "o4-mini-2025-04-16",
        ],
    )
    def test_reasoning_model_detected(self, model):
        assert _is_reasoning_model(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "doubao-seed-2-0-pro-260215",
            "gpt-3.5-turbo",
            "",
            None,
        ],
    )
    def test_non_reasoning_model_not_detected(self, model):
        assert _is_reasoning_model(model) is False


class TestReasoningModelTextKwargs:
    """`_build_text_kwargs` should translate params for reasoning models."""

    def test_gpt5_mini_uses_max_completion_tokens(self):
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-5-mini",
                "api_base": "https://api.openai.com/v1",
                "max_tokens": 512,
            }
        )
        kwargs = vlm._build_text_kwargs(prompt="hi")
        assert kwargs["max_completion_tokens"] == 512
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
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "model": "gpt-5-mini",
                "api_base": "https://api.openai.com/v1",
                "max_tokens": 512,
                "reasoning_effort": "high",
            }
        )
        kwargs = vlm._build_text_kwargs(prompt="hi")
        assert kwargs["reasoning_effort"] == "high"

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


class TestReasoningModelVisionKwargs:
    """`_build_vision_kwargs` should apply the same translation."""

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
