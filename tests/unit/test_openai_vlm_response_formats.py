# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for OpenAIVLM response format handling (Issue #801)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

class TestOpenAIVLMResponseFormats:
    """Test OpenAIVLM handles various response formats correctly."""

    @pytest.fixture()
    def vlm(self):
        return OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
            }
        )

    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            ("plain string response", "plain string response"),
            ({"content": "dict content"}, "dict content"),
            ({"text": "dict text"}, "dict text"),
            (
                {"choices": [{"message": {"content": "dict choice content"}}]},
                "dict choice content",
            ),
            ({"choices": [{"text": "dict choice text"}]}, "dict choice text"),
            (None, ""),
            ({}, ""),
        ],
    )
    def test_extract_content_from_common_response_formats(self, vlm, response, expected):
        assert vlm._extract_content_from_response(response) == expected

    def test_extract_content_from_standard_openai_response(self, vlm):
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="standard response content")
                )
            ]
        )
        assert vlm._extract_content_from_response(response) == "standard response content"

    def test_extract_content_from_choice_text_response(self, vlm):
        response = SimpleNamespace(choices=[SimpleNamespace(text="choice text content")])
        assert vlm._extract_content_from_response(response) == "choice text content"

    @patch.object(OpenAIVLM, "get_client")
    def test_get_completion_with_str_response(self, mock_get_client, vlm):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = "plain string completion"

        assert vlm.get_completion("Hello") == "plain string completion"

    @patch.object(OpenAIVLM, "get_client")
    def test_get_completion_with_standard_response(self, mock_get_client, vlm):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        mock_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="standard completion content")
                )
            ],
            usage=None,
        )
        mock_client.chat.completions.create.return_value = mock_response

        assert vlm.get_completion("Hello") == "standard completion content"

    @patch.object(OpenAIVLM, "get_async_client")
    @pytest.mark.asyncio
    async def test_get_completion_async_with_str_response(self, mock_get_async_client, vlm):
        mock_client = MagicMock()
        mock_get_async_client.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(
            return_value="async plain string completion"
        )

        assert (
            await vlm.get_completion_async("Hello")
            == "async plain string completion"
        )

    @patch.object(OpenAIVLM, "get_client")
    def test_get_vision_completion_with_str_response(self, mock_get_client, vlm):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = "plain string vision response"

        assert (
            vlm.get_vision_completion("Describe this image", ["https://example.com/image.jpg"])
            == "plain string vision response"
        )

    @patch.object(OpenAIVLM, "get_async_client")
    @pytest.mark.asyncio
    async def test_get_vision_completion_async_with_str_response(self, mock_get_async_client, vlm):
        mock_client = MagicMock()
        mock_get_async_client.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(
            return_value="async plain string vision response"
        )

        assert (
            await vlm.get_vision_completion_async("Describe this image", ["https://example.com/image.jpg"])
            == "async plain string vision response"
        )


