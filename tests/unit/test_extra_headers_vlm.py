# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM extra_headers support."""

from unittest.mock import AsyncMock, MagicMock, patch

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


class TestVLMExtraHeaders:
    """Test extra_headers is passed to OpenAI client."""

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_extra_headers_passed_to_sync_client(self, mock_openai_class):
        """extra_headers should be passed as default_headers to sync OpenAI client."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        headers = {"HTTP-Referer": "https://example.com", "X-Title": "My App"}
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "extra_headers": headers,
            }
        )

        # Trigger client creation
        _ = vlm.get_client()

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert call_kwargs.get("default_headers") == headers

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    def test_extra_headers_passed_to_async_client(self, mock_async_openai_class):
        """extra_headers should be passed as default_headers to async OpenAI client."""
        mock_client = MagicMock()
        mock_async_openai_class.return_value = mock_client

        headers = {"HTTP-Referer": "https://example.com", "X-Title": "My App"}
        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "extra_headers": headers,
            }
        )

        # Trigger async client creation
        _ = vlm.get_async_client()

        mock_async_openai_class.assert_called_once()
        call_kwargs = mock_async_openai_class.call_args[1]
        assert call_kwargs.get("default_headers") == headers

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_no_extra_headers_omits_default_headers(self, mock_openai_class):
        """When extra_headers is not provided, default_headers should NOT be set."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
            }
        )

        # Trigger client creation
        _ = vlm.get_client()

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        assert "default_headers" not in call_kwargs

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_extra_headers_empty_dict_omits_default_headers(self, mock_openai_class):
        """When extra_headers is empty dict, default_headers should NOT be set."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "extra_headers": {},
            }
        )

        # Trigger client creation
        _ = vlm.get_client()

        mock_openai_class.assert_called_once()
        call_kwargs = mock_openai_class.call_args[1]
        # Empty dict is falsy, so default_headers should not be set
        assert "default_headers" not in call_kwargs

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_dashscope_text_completion_passes_enable_thinking_in_extra_body(
        self, mock_openai_class
    ):
        """DashScope-compatible OpenAI backends should pass thinking via extra_body."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"), finish_reason="stop")]
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "dashscope/qwen3.5-plus",
            }
        )

        vlm.get_completion("hello", thinking=False)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"enable_thinking": False}

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI")
    async def test_dashscope_async_vision_completion_passes_enable_thinking_in_extra_body(
        self, mock_async_openai_class
    ):
        """DashScope-compatible async vision calls should pass thinking via extra_body."""
        mock_client = MagicMock()
        mock_async_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"), finish_reason="stop")]
        mock_response.usage = None
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                "model": "qwen3.5-flash",
            }
        )

        await vlm.get_vision_completion_async(
            prompt="describe",
            images=[b"\x89PNG\r\n\x1a\n0000"],
            thinking=True,
        )

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"enable_thinking": True}

    @patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
    def test_official_openai_text_completion_does_not_set_enable_thinking(self, mock_openai_class):
        """Official OpenAI API should not receive DashScope-specific extra_body flags."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"), finish_reason="stop")]
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        vlm = OpenAIVLM(
            {
                "api_key": "sk-test",
                "api_base": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
            }
        )

        vlm.get_completion("hello", thinking=False)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "extra_body" not in call_kwargs

    @patch("openviking.models.vlm.backends.openai_vlm.openai.AzureOpenAI")
    def test_azure_text_completion_does_not_set_enable_thinking(self, mock_azure_openai_class):
        """Azure OpenAI should not receive DashScope-specific extra_body flags."""
        mock_client = MagicMock()
        mock_azure_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"), finish_reason="stop")]
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        vlm = OpenAIVLM(
            {
                "provider": "azure",
                "api_key": "sk-test",
                "api_base": "https://example-resource.openai.azure.com",
                "api_version": "2025-01-01-preview",
                "model": "gpt-4o-mini",
            }
        )

        vlm.get_completion("hello", thinking=False)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "extra_body" not in call_kwargs


class TestVLMBaseExtraHeaders:
    """Test VLMBase extracts extra_headers from config."""

    def test_extra_headers_extracted_from_config(self):
        """VLMBase should extract extra_headers from config."""

        class StubVLM(OpenAIVLM):
            def get_completion(self, prompt, thinking=False):
                return ""

            async def get_completion_async(self, prompt, thinking=False, max_retries=0):
                return ""

            def get_vision_completion(self, prompt, images, thinking=False):
                return ""

            async def get_vision_completion_async(self, prompt, images, thinking=False):
                return ""

        headers = {"X-Custom-Header": "custom-value"}
        vlm = StubVLM(
            {
                "api_key": "sk-test",
                "extra_headers": headers,
            }
        )

        assert vlm.extra_headers == headers

    def test_extra_headers_none_when_not_in_config(self):
        """VLMBase should set extra_headers to None when not in config."""

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

        assert vlm.extra_headers is None


class TestVLMConfigExtraHeaders:
    """Test VLMConfig passes extra_headers to VLM instance."""

    def test_vlm_config_accepts_extra_headers_in_providers(self):
        """VLMConfig should accept extra_headers in providers config."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "api_base": "https://api.openai.com/v1",
                    "extra_headers": {"HTTP-Referer": "https://example.com"},
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result["extra_headers"] == {"HTTP-Referer": "https://example.com"}

    def test_vlm_config_extra_headers_none_when_not_set(self):
        """VLMConfig should not include extra_headers when not set."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "api_base": "https://api.openai.com/v1",
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result.get("extra_headers") is None

    def test_vlm_config_accepts_flat_extra_headers(self):
        """VLMConfig should accept extra_headers as flat config field (legacy style)."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            api_key="sk-test",
            api_base="https://openrouter.ai/api/v1",
            extra_headers={"HTTP-Referer": "https://example.com", "X-Title": "My App"},
        )

        # Verify flat extra_headers is stored
        assert config.extra_headers == {"HTTP-Referer": "https://example.com", "X-Title": "My App"}

        # Verify it's migrated to providers structure
        config._migrate_legacy_config()
        assert config.providers["openai"]["extra_headers"] == {
            "HTTP-Referer": "https://example.com",
            "X-Title": "My App",
        }

        # Verify _build_vlm_config_dict includes it
        result = config._build_vlm_config_dict()
        assert result["extra_headers"] == {
            "HTTP-Referer": "https://example.com",
            "X-Title": "My App",
        }


class TestLiteLLMVLMModelResolution:
    """Regression tests for LiteLLM model prefix resolution."""

    def test_zhipu_zai_model_keeps_existing_zai_prefix(self):
        """Zhipu GLM models already using LiteLLM's zai/ prefix must not be double-prefixed."""
        vlm = LiteLLMVLMProvider(
            {
                "model": "zai/glm-4.5",
                "provider": "litellm",
            }
        )

        assert vlm._resolve_model("zai/glm-4.5") == "zai/glm-4.5"

    def test_non_zhipu_provider_still_applies_prefix(self):
        """The zai/ exception should not affect other providers."""
        vlm = LiteLLMVLMProvider(
            {
                "model": "zai/custom-model",
                "provider": "gemini",
                "api_key": "sk-test",
            }
        )

        assert vlm._resolve_model("zai/custom-model") == "gemini/zai/custom-model"
