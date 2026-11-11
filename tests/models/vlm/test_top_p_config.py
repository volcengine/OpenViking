# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the first-class ``top_p`` VLM configuration parameter.

Until now ``top_p`` could only reach OpenAI-compatible providers through
``extra_request_body``. These tests lock in that the config value flows
through the credential machinery to both the OpenAI and LiteLLM backends,
and that reasoning-model requests skip it the same way they skip
``temperature``.
"""

import pytest
from pydantic import ValidationError

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_vlm_config_accepts_top_p():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x", top_p=0.95)
    assert cfg.top_p == 0.95


def test_vlm_config_top_p_defaults_to_none():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x")
    assert cfg.top_p is None


def test_vlm_config_rejects_top_p_out_of_range():
    with pytest.raises(ValidationError):
        VLMConfig(model="gpt-4o-mini", api_key="sk-x", top_p=1.5)
    with pytest.raises(ValidationError):
        VLMConfig(model="gpt-4o-mini", api_key="sk-x", top_p=-0.1)


def test_openai_vlm_applies_top_p_for_plain_models():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "top_p": 0.95,
            "temperature": 1.0,
        }
    )
    kwargs: dict = {"model": "gpt-4o-mini"}
    vlm._apply_completion_params(kwargs, thinking=False)
    assert kwargs["top_p"] == 0.95
    assert kwargs["temperature"] == 1.0


def test_openai_vlm_omits_top_p_for_reasoning_models():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "o1-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "top_p": 0.95,
            "max_tokens": 256,
        }
    )
    kwargs: dict = {"model": "o1-mini"}
    vlm._apply_completion_params(kwargs, thinking=False)
    assert kwargs["max_completion_tokens"] == 256
    assert kwargs["reasoning_effort"] == "low"
    assert "top_p" not in kwargs
    assert "temperature" not in kwargs


def test_openai_vlm_top_p_absent_when_not_configured():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
        }
    )
    kwargs: dict = {"model": "gpt-4o-mini"}
    vlm._apply_completion_params(kwargs, thinking=False)
    assert "top_p" not in kwargs


def test_litellm_build_kwargs_includes_top_p_and_effort_fallback():
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    vlm = LiteLLMVLMProvider(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "top_p": 0.95,
            "reasoning_effort": "low",
        }
    )
    kwargs = vlm._build_text_kwargs(prompt="hi")
    assert kwargs["top_p"] == 0.95
    assert kwargs["reasoning_effort"] == "low"


def test_litellm_per_call_effort_overrides_config_default():
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    vlm = LiteLLMVLMProvider(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "reasoning_effort": "low",
        }
    )
    kwargs = vlm._build_text_kwargs(prompt="hi", reasoning_effort="high")
    assert kwargs["reasoning_effort"] == "high"


def test_vlm_config_propagates_top_p_to_backend():
    cfg = VLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-x",
        api_base="https://example.invalid",
        top_p=0.93,
    )
    vlm = cfg.get_vlm_instance()
    assert vlm.top_p == 0.93
