# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM stream configuration support."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


class MockDelta:
    """Mock delta object for streaming chunks."""

    def __init__(self, content=None):
        self.content = content


class MockChoice:
    """Mock choice object for streaming chunks."""

    def __init__(self, delta=None):
        self.delta = delta


class MockChunk:
    """Mock chunk object for streaming response."""

    def __init__(self, content=None, usage=None):
        self.choices = [MockChoice(delta=MockDelta(content=content))] if content is not None else []
        self.usage = usage


class MockUsage:
    """Mock usage object."""

    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class TestVLMStreamConfig:
    """Test stream configuration is passed to OpenAI API calls."""

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_stream_false_by_default(self, mock_openai_class):
        """stream should default to False."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
            }
        )

        vlm.get_completion("test prompt")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is False

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_stream_true_passed_to_api(self, mock_openai_class):
        """stream=True should be passed to API call."""
        mock_client = MagicMock()
        # Simulate streaming response
        chunks = [
            MockChunk(content="Hello"),
            MockChunk(content=" world"),
            MockChunk(content="!", usage=MockUsage(prompt_tokens=10, completion_tokens=3)),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = vlm.get_completion("test prompt")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Hello world!"

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_stream_false_uses_non_streaming_path(self, mock_openai_class):
        """stream=False should use non-streaming response handling."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Non-streaming response"
        mock_response.usage = MockUsage(prompt_tokens=5, completion_tokens=10)
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": False,
            }
        )

        result = vlm.get_completion("test prompt")

        assert result == "Non-streaming response"
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is False

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    async def test_async_stream_true(self, mock_async_openai_class):
        """stream=True should work with async methods."""
        mock_client = MagicMock()

        async def async_generator():
            chunks = [
                MockChunk(content="Async"),
                MockChunk(content=" result"),
                MockChunk(content="!", usage=MockUsage(prompt_tokens=8, completion_tokens=4)),
            ]
            for chunk in chunks:
                yield chunk

        async def mock_create(*args, **kwargs):
            return async_generator()

        mock_client.chat.completions.create = mock_create
        mock_async_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = await vlm.get_completion_async("test prompt")

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Async result!"

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    async def test_async_stream_false(self, mock_async_openai_class):
        """stream=False should work with async methods."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Async non-streaming"
        mock_response.usage = MockUsage(prompt_tokens=5, completion_tokens=5)

        async def mock_create(*args, **kwargs):
            return mock_response

        mock_client.chat.completions.create = mock_create
        mock_async_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": False,
            }
        )

        result = await vlm.get_completion_async("test prompt")

        assert result == "Async non-streaming"

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_vision_completion_stream_true(self, mock_openai_class):
        """stream=True should work with vision completion."""
        mock_client = MagicMock()
        chunks = [
            MockChunk(content="Image"),
            MockChunk(content=" description"),
            MockChunk(content=".", usage=MockUsage(prompt_tokens=20, completion_tokens=5)),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = vlm.get_vision_completion("describe this", ["http://example.com/image.jpg"])

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Image description."

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    async def test_vision_completion_async_stream_true(self, mock_async_openai_class):
        """stream=True should work with async vision completion."""
        mock_client = MagicMock()

        async def async_generator():
            chunks = [
                MockChunk(content="Async"),
                MockChunk(content=" image"),
                MockChunk(
                    content=" result", usage=MockUsage(prompt_tokens=15, completion_tokens=6)
                ),
            ]
            for chunk in chunks:
                yield chunk

        async def mock_create(*args, **kwargs):
            return async_generator()

        mock_client.chat.completions.create = mock_create
        mock_async_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "stream": True,
            }
        )

        result = await vlm.get_vision_completion_async(
            "describe this", ["http://example.com/image.jpg"]
        )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True
        assert result == "Async image result"


class TestVLMBaseStreamConfig:
    """Test VLMBase extracts stream from config."""

    def test_stream_defaults_to_false(self):
        """VLMBase should default stream to False."""

        class StubVLM(OpenAIVLM):
            def get_completion(self, prompt, thinking=False):
                return ""

            async def get_completion_async(self, prompt, thinking=False, max_retries=0):
                return ""

            def get_vision_completion(self, prompt, images, thinking=False):
                return ""

            async def get_vision_completion_async(self, prompt, images, thinking=False):
                return ""

        vlm = StubVLM(
            {
                "api_key": "sk-test",
            }
        )

        assert vlm.stream is False

    def test_stream_extracted_from_config(self):
        """VLMBase should extract stream from config."""

        class StubVLM(OpenAIVLM):
            def get_completion(self, prompt, thinking=False):
                return ""

            async def get_completion_async(self, prompt, thinking=False, max_retries=0):
                return ""

            def get_vision_completion(self, prompt, images, thinking=False):
                return ""

            async def get_vision_completion_async(self, prompt, images, thinking=False):
                return ""

        vlm = StubVLM(
            {
                "api_key": "sk-test",
                "stream": True,
            }
        )

        assert vlm.stream is True


class TestVLMConfigStream:
    """Test VLMConfig passes stream to VLM instance."""

    def test_vlm_config_accepts_stream(self):
        """VLMConfig should accept stream field."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=True,
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "api_base": "https://api.openai.com/v1",
                }
            },
        )

        assert config.stream is True

    def test_vlm_config_stream_defaults_to_false(self):
        """VLMConfig should default stream to False."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        assert config.stream is False

    def test_vlm_config_stream_passed_to_vlm_dict(self):
        """VLMConfig should pass stream to _build_vlm_config_dict."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=True,
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_stream_migrated_to_providers(self):
        """VLMConfig should migrate stream to providers structure."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            stream=True,
        )

        # Verify stream is migrated to providers structure
        assert config.providers["openai"]["stream"] is True

        # Verify _build_vlm_config_dict uses the migrated value
        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_stream_in_providers_takes_precedence(self):
        """stream in providers config should take precedence over flat config."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=False,  # flat config is False
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "stream": True,  # provider config is True, should take precedence
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result["stream"] is True


class TestStreamingResponseProcessing:
    """Test streaming response processing logic."""

    def test_process_streaming_response_with_content(self):
        """_process_streaming_response should extract content from chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        chunks = [
            MockChunk(content="Hello"),
            MockChunk(content=" "),
            MockChunk(content="world"),
        ]

        result = vlm._process_streaming_response(iter(chunks))
        assert result == "Hello world"

    def test_process_streaming_response_with_usage(self):
        """_process_streaming_response should extract usage from chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        chunks = [
            MockChunk(content="Hello", usage=MockUsage(prompt_tokens=10, completion_tokens=5)),
        ]

        with patch.object(vlm, "update_token_usage") as mock_update:
            vlm._process_streaming_response(iter(chunks))

            mock_update.assert_called_once_with(
                model_name="gpt-4o-mini",
                provider="openai",
                prompt_tokens=10,
                completion_tokens=5,
            )

    def test_process_streaming_response_empty_chunks(self):
        """_process_streaming_response should handle empty chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        result = vlm._process_streaming_response(iter([]))
        assert result == ""

    @pytest.mark.asyncio
    async def test_process_streaming_response_async(self):
        """_process_streaming_response_async should extract content from async chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        async def async_chunks():
            yield MockChunk(content="Async")
            yield MockChunk(content=" result")
            yield MockChunk(content="!", usage=MockUsage(prompt_tokens=5, completion_tokens=3))

        result = await vlm._process_streaming_response_async(async_chunks())
        assert result == "Async result!"

    @pytest.mark.asyncio
    async def test_process_streaming_response_async_with_usage(self):
        """_process_streaming_response_async should extract usage from chunks."""
        vlm = OpenAIVLM({"api_key": "sk-test"})

        async def async_chunks():
            yield MockChunk(content="Test")
            yield MockChunk(content="", usage=MockUsage(prompt_tokens=15, completion_tokens=8))

        with patch.object(vlm, "update_token_usage") as mock_update:
            await vlm._process_streaming_response_async(async_chunks())

            mock_update.assert_called_once_with(
                model_name="gpt-4o-mini",
                provider="openai",
                prompt_tokens=15,
                completion_tokens=8,
            )
