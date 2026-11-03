# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for LiteLLM VLM provider detection and model prefix resolution."""

import asyncio
import os
from unittest.mock import AsyncMock, Mock

import pytest
from litellm import ModelResponse

from openviking.models.vlm.backends.litellm_vlm import (
    LiteLLMVLMProvider,
    detect_provider_by_model,
)

NEMOTRON_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
NEMOTRON_LITELLM_MODEL = f"nvidia_nim/{NEMOTRON_MODEL}"


def _vlm(model: str) -> LiteLLMVLMProvider:
    return LiteLLMVLMProvider({"model": model, "provider": "litellm"})


@pytest.mark.parametrize(
    "model,mode",
    [("glm-4.6v", None), ("zhipu/glm-4.6v", "user_message"), ("zai/glm-4.6v", "user_message")],
)
def test_zhipu_models_use_current_zai_route(model, mode):
    vlm = _vlm(model)
    assert vlm._resolve_model(model) == "zai/glm-4.6v"
    assert vlm.resolved_provider() == "zai"
    assert vlm.tool_result_media_mode() == mode


@pytest.mark.parametrize("api_key", [None, "test-key"])
@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("litellm", "gemini/future-claude-vision", "gemini/future-claude-vision"),
        ("litellm", "moonshot/future-claude-vision", "moonshot/future-claude-vision"),
        ("openai", "future-qwen-vision", "openai/future-qwen-vision"),
        ("openrouter", "anthropic/future-vision", "openrouter/anthropic/future-vision"),
    ],
)
def test_declared_protocol_wins_over_model_keywords(
    monkeypatch, provider, model, expected, api_key
):
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DASHSCOPE_API_KEY",
        "MOONSHOT_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    vlm = LiteLLMVLMProvider({"provider": provider, "model": model, "api_key": api_key})
    assert vlm._resolve_model(model) == expected
    kwargs = vlm._build_text_kwargs(prompt="read the image")
    assert kwargs["model"] == expected
    assert "enable_thinking" not in kwargs.get("extra_body", {})
    assert vlm.tool_result_media_mode() is not None


@pytest.mark.parametrize("api_base", [None, "https://compat.invalid/v1"])
def test_new_model_needs_declared_image_protocol(api_base):
    implicit = LiteLLMVLMProvider(
        {"provider": "litellm", "model": "future-claude-vision", "api_base": api_base}
    )
    assert implicit.tool_result_media_mode() is None
    explicit = LiteLLMVLMProvider(
        {"provider": "litellm", "model": "openai/future-claude-vision", "api_base": api_base}
    )
    assert explicit._resolve_model(explicit.model) == "openai/future-claude-vision"
    assert explicit.tool_result_media_mode() == "user_message"


class TestLiteLLMVLMProviderDetection:
    def test_nvidia_nim_detected_by_nemotron_keyword(self):
        assert detect_provider_by_model(NEMOTRON_MODEL) == "nvidia_nim"

    def test_nvidia_nim_detected_by_existing_prefix(self):
        assert detect_provider_by_model(NEMOTRON_LITELLM_MODEL) == "nvidia_nim"

    def test_nvidia_nim_prefix_applied_to_nemotron_model(self):
        assert _vlm(NEMOTRON_MODEL)._resolve_model(NEMOTRON_MODEL) == NEMOTRON_LITELLM_MODEL

    def test_existing_nvidia_nim_prefix_is_not_rewritten(self):
        model = "nvidia_nim/nvidia/nim-fake"

        assert detect_provider_by_model(model) == "nvidia_nim"
        assert _vlm(model)._resolve_model(model) == model

    def test_nvidia_nim_setup_env_uses_nvidia_key(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        vlm = LiteLLMVLMProvider(
            {"api_key": "fake", "model": NEMOTRON_MODEL, "provider": "litellm"}
        )

        assert vlm._detected_provider == "nvidia_nim"
        assert "OPENAI_API_KEY" not in os.environ
        assert os.environ["NVIDIA_NIM_API_KEY"] == "fake"

    def test_openai_prefixed_nemotron_model_is_left_alone(self):
        model = f"openai/{NEMOTRON_MODEL}"

        assert _vlm(model)._resolve_model(model) == model

    @pytest.mark.parametrize(
        "model",
        [
            "github_copilot/claude-opus-4.8",
            "github_copilot/gpt-4o",
        ],
    )
    def test_github_copilot_route_is_left_alone(self, model):
        assert detect_provider_by_model(model) is None
        assert _vlm(model)._resolve_model(model) == model

    @pytest.mark.parametrize(
        ("model", "provider"),
        [
            ("gemini-3.1-flash-lite-preview", "gemini"),
            ("claude-haiku-4-5", "anthropic"),
            ("gpt-4o", "openai"),
        ],
    )
    def test_existing_provider_detection_still_matches(self, model, provider):
        assert detect_provider_by_model(model) == provider


@pytest.mark.parametrize(
    "model", ["ollama_chat/qwen2.5:7b", "ollama/qwen2.5:7b", "openai/qwen2.5:7b"]
)
@pytest.mark.parametrize("api_key", [None, "test-key"])
@pytest.mark.parametrize("api_base", [None, "http://localhost:11434"])
@pytest.mark.parametrize("extra_body", [{}, {"num_ctx": 32768, "think": True}])
def test_explicit_local_routes_reach_completion_unchanged(
    monkeypatch, model, api_key, api_base, extra_body
):
    monkeypatch.setenv("OLLAMA_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    provider = LiteLLMVLMProvider(
        {
            "provider": "litellm",
            "model": model,
            "api_key": api_key,
            "api_base": api_base,
            "extra_request_body": extra_body,
        }
    )
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    response = ModelResponse(choices=[{"message": {"role": "assistant", "content": "ok"}}])
    sync_call = Mock(return_value=response)
    async_call = AsyncMock(return_value=response)
    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.completion", sync_call)
    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", async_call)

    provider.get_completion(prompt="test", tools=tools)
    asyncio.run(provider.get_completion_async(prompt="test", tools=tools))

    for call in (sync_call, async_call):
        kwargs = call.call_args.kwargs
        assert kwargs["model"] == model
        assert kwargs["tools"] == tools
        assert kwargs.get("api_key") == api_key
        assert kwargs.get("api_base") == api_base
        if model.startswith(("ollama/", "ollama_chat/")):
            assert kwargs["extra_body"] == {"num_ctx": 16384, "think": False} | extra_body
