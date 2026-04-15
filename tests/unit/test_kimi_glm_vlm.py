# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.vlm import VLMFactory
from openviking.models.vlm.backends.glm_vlm import DEFAULT_GLM_API_BASE, GLMVLM
from openviking.models.vlm.backends.kimi_vlm import DEFAULT_KIMI_USER_AGENT, KimiVLM
from openviking_cli.utils.config.vlm_config import VLMConfig


def _build_kimi_response(text: str = "ok", stop_reason: str = "end_turn") -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }


@patch("openviking.models.vlm.backends.kimi_vlm.httpx.Client")
def test_kimi_vision_completion_uses_anthropic_messages_and_headers(mock_client_class):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = _build_kimi_response("vision ok")
    mock_response.raise_for_status.return_value = None
    mock_client.post.return_value = mock_response
    mock_client_class.return_value = mock_client

    vlm = KimiVLM(
        {
            "provider": "kimi",
            "model": "kimi-code",
            "api_key": "kimi-test-key",
        }
    )

    result = vlm.get_vision_completion(prompt="describe", images=[b"\x89PNG\r\n\x1a\n0000"])

    assert result == "vision ok"
    call_kwargs = mock_client.post.call_args.kwargs
    assert mock_client.post.call_args.args[0] == "https://api.kimi.com/coding/v1/messages"
    assert call_kwargs["headers"]["X-API-Key"] == "kimi-test-key"
    assert call_kwargs["headers"]["User-Agent"] == DEFAULT_KIMI_USER_AGENT
    assert call_kwargs["json"]["model"] == "kimi-for-coding"
    content = call_kwargs["json"]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1] == {"type": "text", "text": "describe"}


def test_kimi_tool_markup_is_mapped_to_tool_calls():
    vlm = KimiVLM({"provider": "kimi", "model": "kimi-code", "api_key": "kimi-test-key"})

    response = vlm._build_response(
        _build_kimi_response(
            "<|tool_calls_section_begin|><|tool_call_begin|>search:0"
            "<|tool_call_argument_begin|>{\"query\":\"glm\"}<|tool_call_end|>"
            "<|tool_calls_section_end|>",
            stop_reason="tool_use",
        ),
        has_tools=True,
    )

    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search"
    assert response.tool_calls[0].arguments == {"query": "glm"}


def test_kimi_converts_openai_style_tool_history():
    vlm = KimiVLM({"provider": "kimi", "model": "kimi-code", "api_key": "kimi-test-key"})

    messages, _ = vlm._convert_messages(
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "search",
                            "arguments": "{\"query\": \"kimi\"}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "{\"items\": []}",
            },
        ]
    )

    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"][0]["type"] == "tool_use"
    assert messages[0]["content"][0]["name"] == "search"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"][0]["type"] == "tool_result"
    assert messages[1]["content"][0]["tool_use_id"] == "call-1"


def test_kimi_rejects_remote_image_urls():
    vlm = KimiVLM({"provider": "kimi", "model": "kimi-code", "api_key": "kimi-test-key"})

    with pytest.raises(ValueError, match="local image bytes, paths, or data URLs"):
        vlm.get_vision_completion(prompt="describe", images=["https://example.com/test.png"])


def test_glm_backend_sets_coding_plan_defaults():
    vlm = GLMVLM({"provider": "glm", "api_key": "glm-key"})

    assert vlm.provider == "glm"
    assert vlm.api_base == DEFAULT_GLM_API_BASE
    assert vlm.model == "glm-4.6v"


def test_vlm_factory_routes_first_class_kimi_and_glm_providers():
    kimi_vlm = VLMFactory.create({"provider": "kimi", "api_key": "kimi-key", "model": "kimi-code"})
    glm_vlm = VLMFactory.create({"provider": "zai", "api_key": "glm-key", "model": "glm-4.6v"})

    assert kimi_vlm.__class__.__name__ == "KimiVLM"
    assert glm_vlm.__class__.__name__ == "GLMVLM"


def test_vlm_config_normalizes_kimi_and_glm_aliases():
    config = VLMConfig(
        model="glm-4.6v",
        default_provider="zhipu",
        providers={"kimi-coding": {"api_key": "kimi-key"}, "zai": {"api_key": "glm-key"}},
    )

    provider_config, provider_name = config.get_provider_config()

    assert "kimi" in config.providers
    assert "glm" in config.providers
    assert provider_name == "glm"
    assert provider_config == {"api_key": "glm-key"}


def test_vlm_config_rejects_duplicate_alias_blocks():
    with pytest.raises(ValueError, match="Duplicate VLM provider config"):
        VLMConfig(
            model="glm-4.6v",
            providers={"glm": {"api_key": "glm-a"}, "zai": {"api_key": "glm-b"}},
        )


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_glm_backend_reuses_openai_client_with_coding_endpoint(mock_openai_class):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client

    vlm = GLMVLM({"provider": "glm", "api_key": "glm-key"})
    _ = vlm.get_client()

    call_kwargs = mock_openai_class.call_args.kwargs
    assert call_kwargs["base_url"] == DEFAULT_GLM_API_BASE
    assert call_kwargs["api_key"] == "glm-key"
