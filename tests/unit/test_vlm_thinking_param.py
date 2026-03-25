# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for VLM thinking parameter support."""

from unittest.mock import MagicMock, patch

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


def _make_mock_response():
    """Create a mock OpenAI-style response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "test response"
    mock_response.choices[0].message.tool_calls = None
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_response.usage.total_tokens = 15
    mock_response.usage.prompt_tokens_details = None
    return mock_response


class TestOpenAIThinkingParam:
    """Test thinking parameter is wired to OpenAI API calls."""

    def _make_vlm_with_mock_client(self):
        """Create an OpenAIVLM with a mocked sync client."""
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
            }
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_response()
        vlm._sync_client = mock_client
        return vlm, mock_client

    def test_openai_thinking_disabled_passes_extra_body(self):
        """When thinking=False, extra_body should contain enable_thinking=False."""
        vlm, mock_client = self._make_vlm_with_mock_client()

        vlm.get_completion(prompt="hello", thinking=False)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["enable_thinking"] is False

    def test_openai_thinking_enabled_no_extra_body(self):
        """When thinking=True, enable_thinking should NOT be set to False."""
        vlm, mock_client = self._make_vlm_with_mock_client()

        vlm.get_completion(prompt="hello", thinking=True)

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        extra_body = call_kwargs.get("extra_body", {})
        assert extra_body.get("enable_thinking") is not False

    def test_openai_vision_thinking_disabled_passes_extra_body(self):
        """Vision completion with thinking=False should pass enable_thinking=False."""
        vlm, mock_client = self._make_vlm_with_mock_client()

        vlm.get_vision_completion(
            prompt="describe this",
            images=["https://example.com/img.png"],
            thinking=False,
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["enable_thinking"] is False


class TestLiteLLMThinkingParam:
    """Test thinking parameter is wired to LiteLLM API calls."""

    @patch("openviking.models.vlm.backends.litellm_vlm.completion")
    def test_litellm_thinking_disabled_passes_extra_body(self, mock_completion):
        """When thinking=False, extra_body should contain enable_thinking=False."""
        mock_completion.return_value = _make_mock_response()

        vlm = LiteLLMVLMProvider(
            {
                "model": "qwen3.5-plus",
                "provider": "dashscope",
                "api_key": "sk-test",
            }
        )

        vlm.get_completion(prompt="hello", thinking=False)

        call_kwargs = mock_completion.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["enable_thinking"] is False

    @patch("openviking.models.vlm.backends.litellm_vlm.completion")
    def test_litellm_thinking_enabled_no_extra_body(self, mock_completion):
        """When thinking=True, enable_thinking should NOT be set to False."""
        mock_completion.return_value = _make_mock_response()

        vlm = LiteLLMVLMProvider(
            {
                "model": "qwen3.5-plus",
                "provider": "dashscope",
                "api_key": "sk-test",
            }
        )

        vlm.get_completion(prompt="hello", thinking=True)

        call_kwargs = mock_completion.call_args[1]
        extra_body = call_kwargs.get("extra_body", {})
        assert extra_body.get("enable_thinking") is not False
