# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openviking.models.vlm import VLMFactory
from openviking.models.vlm.backends.glm_vlm import DEFAULT_GLM_API_BASE, GLMVLM
from openviking.models.vlm.backends.kimi_vlm import (
    DEFAULT_KIMI_MAX_TOKENS,
    DEFAULT_KIMI_USER_AGENT,
    KimiVLM,
)
from openviking_cli.utils.config.vlm_config import VLMConfig


def _build_openai_response(text: str = "ok", finish_reason: str = "stop") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=text), finish_reason=finish_reason)]
    response.usage = MagicMock(prompt_tokens=12, completion_tokens=7, total_tokens=19)
    return response


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_kimi_vision_completion_uses_openai_messages_and_headers(mock_openai_class):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _build_openai_response("vision ok")
    mock_openai_class.return_value = mock_client

    vlm = KimiVLM(
        {
            "provider": "kimi",
            "model": "kimi-code",
            "api_key": "kimi-test-key",
        }
    )

    result = vlm.get_vision_completion(prompt="describe", images=[b"\x89PNG\r\n\x1a\n0000"])

    assert result == "vision ok"
    client_kwargs = mock_openai_class.call_args.kwargs
    assert client_kwargs["base_url"] == "https://api.kimi.com/coding/v1"
    assert client_kwargs["api_key"] == "kimi-test-key"
    assert client_kwargs["default_headers"]["User-Agent"] == DEFAULT_KIMI_USER_AGENT
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "kimi-for-coding"
    content = call_kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": "describe"}


def test_kimi_backend_sets_openai_compatible_defaults():
    vlm = KimiVLM({"provider": "kimi", "model": "kimi-code", "api_key": "kimi-test-key"})

    assert vlm.provider == "kimi"
    assert vlm.api_base == "https://api.kimi.com/coding/v1"
    assert vlm.model == "kimi-for-coding"
    assert vlm.max_tokens == DEFAULT_KIMI_MAX_TOKENS
    assert vlm.extra_headers == {"User-Agent": DEFAULT_KIMI_USER_AGENT}


def test_glm_backend_sets_coding_plan_defaults():
    vlm = GLMVLM({"provider": "glm", "api_key": "glm-key"})

    assert vlm.provider == "glm"
    assert vlm.api_base == DEFAULT_GLM_API_BASE
    assert vlm.model == "glm-4.6v"


def test_vlm_factory_routes_first_class_kimi_and_glm_providers():
    kimi_vlm = VLMFactory.create({"provider": "kimi", "api_key": "kimi-key", "model": "kimi-code"})
    glm_vlm = VLMFactory.create({"provider": "glm", "api_key": "glm-key", "model": "glm-4.6v"})

    assert kimi_vlm.__class__.__name__ == "KimiVLM"
    assert glm_vlm.__class__.__name__ == "GLMVLM"


def test_vlm_factory_exposes_canonical_provider_names():
    assert VLMFactory.get_available_providers() == [
        "volcengine",
        "openai",
        "azure",
        "kimi",
        "glm",
        "litellm",
        "openai-codex",
    ]


def test_vlm_config_uses_canonical_provider_names():
    config = VLMConfig(
        model="glm-4.6v",
        default_provider="glm",
        providers={"kimi": {"api_key": "kimi-key"}, "glm": {"api_key": "glm-key"}},
    )

    provider_config, provider_name = config.get_provider_config()

    assert "kimi" in config.providers
    assert "glm" in config.providers
    assert provider_name == "glm"
    assert provider_config == {"api_key": "glm-key"}


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_glm_backend_reuses_openai_client_with_coding_endpoint(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client

    vlm = GLMVLM({"provider": "glm", "api_key": "glm-key"})
    _ = vlm.get_client()

    call_kwargs = mock_openai_class.call_args.kwargs
    assert call_kwargs["base_url"] == DEFAULT_GLM_API_BASE
    assert call_kwargs["api_key"] == "glm-key"
