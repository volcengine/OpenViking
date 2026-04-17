# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for DashScopeDenseEmbedder.
Pattern: patch at module import path, use MagicMock, never make real API calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.models.embedder.dashscope_embedders import (
    DashScopeDenseEmbedder,
    get_dashscope_model_default_dimension,
)

# ---------------------------------------------------------------------------
# Dimension helper
# ---------------------------------------------------------------------------


class TestDashScopeDimensionHelper:
    """Test get_dashscope_model_default_dimension() for all known models."""

    def test_text_embedding_v3(self):
        assert get_dashscope_model_default_dimension("text-embedding-v3") == 1024

    def test_text_embedding_v4(self):
        assert get_dashscope_model_default_dimension("text-embedding-v4") == 1024

    def test_tongyi_vision_plus(self):
        assert get_dashscope_model_default_dimension("tongyi-embedding-vision-plus") == 1152

    def test_tongyi_vision_flash(self):
        assert get_dashscope_model_default_dimension("tongyi-embedding-vision-flash") == 768

    def test_qwen3_vl_embedding(self):
        assert get_dashscope_model_default_dimension("qwen3-vl-embedding") == 2560

    def test_qwen2_5_vl_embedding(self):
        assert get_dashscope_model_default_dimension("qwen2.5-vl-embedding") == 1024

    def test_unknown_model_fallback(self):
        assert get_dashscope_model_default_dimension("some-unknown-model") == 1024

    def test_prefix_match_tongyi_vision_plus_variant(self):
        # Prefix match: starts with "tongyi-embedding-vision-plus"
        assert get_dashscope_model_default_dimension("tongyi-embedding-vision-plus-v2") == 1152

    def test_prefix_match_tongyi_vision_flash_variant(self):
        assert get_dashscope_model_default_dimension("tongyi-embedding-vision-flash-2025") == 768


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestDashScopeInit:
    """Test DashScopeDenseEmbedder initialization."""

    def test_requires_api_key(self):
        with pytest.raises(ValueError, match="api_key is required"):
            DashScopeDenseEmbedder(model_name="text-embedding-v4", api_key=None)

    def test_requires_api_key_empty_string(self):
        with pytest.raises(ValueError, match="api_key is required"):
            DashScopeDenseEmbedder(model_name="text-embedding-v4", api_key="")

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_stores_text_mode_params(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-test",
            input_type="text",
        )
        assert embedder._input_type == "text"
        assert embedder.api_key == "sk-test"
        assert embedder.api_base == "https://dashscope.aliyuncs.com"
        assert embedder.provider == "dashscope"

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_stores_multimodal_params(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
            enable_fusion=True,
            res_level=2,
            max_video_frames=16,
        )
        assert embedder._input_type == "multimodal"
        assert embedder.enable_fusion is True
        assert embedder.res_level == 2
        assert embedder.max_video_frames == 16

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_default_dimension_from_helper(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-test",
        )
        assert embedder.get_dimension() == 1024

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_custom_dimension_overrides_helper(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-test",
            dimension=512,
        )
        assert embedder.get_dimension() == 512

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_custom_api_base(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-test",
            api_base="https://custom-endpoint.com",
        )
        assert embedder.api_base == "https://custom-endpoint.com"

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_api_base_trailing_slash_stripped(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-test",
            api_base="https://custom-endpoint.com/",
        )
        assert embedder.api_base == "https://custom-endpoint.com"


# ---------------------------------------------------------------------------
# Text mode embed
# ---------------------------------------------------------------------------


class TestDashScopeTextEmbed:
    """Test text mode embed with mocked openai.OpenAI."""

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_returns_correct_vector(self, mock_httpx, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1024
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = MagicMock(prompt_tokens=10, total_tokens=10)
        mock_client.embeddings.create.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        result = embedder.embed("hello world")
        assert result.dense_vector == [0.1] * 1024
        assert result.is_dense

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_sends_dimensions_param(self, mock_httpx, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 512
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = MagicMock(prompt_tokens=5, total_tokens=5)
        mock_client.embeddings.create.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-test",
            input_type="text",
            dimension=512,
        )
        embedder.embed("hello")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["dimensions"] == 512
        assert call_kwargs["model"] == "text-embedding-v4"
        assert call_kwargs["input"] == "hello"

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_api_error_raises_runtime_error(self, mock_httpx, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = Exception("API error")

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        with pytest.raises(RuntimeError, match="DashScope embedding failed"):
            embedder.embed("hello")


# ---------------------------------------------------------------------------
# Text batch embed
# ---------------------------------------------------------------------------


class TestDashScopeTextBatch:
    """Test text mode batch embedding."""

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_batch_splits_into_chunks_of_10(self, mock_httpx, mock_openai_class):
        """15 texts should produce 2 API calls (10 + 5)."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        # First call: 10 items, second call: 5 items
        mock_response_1 = MagicMock()
        mock_response_1.data = [MagicMock(embedding=[0.1 * (i + 1)] * 1024) for i in range(10)]
        mock_response_1.usage = MagicMock(prompt_tokens=100, total_tokens=100)

        mock_response_2 = MagicMock()
        mock_response_2.data = [MagicMock(embedding=[0.2 * (i + 1)] * 1024) for i in range(5)]
        mock_response_2.usage = MagicMock(prompt_tokens=50, total_tokens=50)

        mock_client.embeddings.create.side_effect = [mock_response_1, mock_response_2]

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        results = embedder.embed_batch([f"text-{i}" for i in range(15)])

        assert len(results) == 15
        assert mock_client.embeddings.create.call_count == 2
        # Verify first call had 10 texts, second had 5
        first_call_input = mock_client.embeddings.create.call_args_list[0][1]["input"]
        assert len(first_call_input) == 10
        second_call_input = mock_client.embeddings.create.call_args_list[1][1]["input"]
        assert len(second_call_input) == 5

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_batch_empty_returns_empty(self, mock_httpx, mock_openai_class):
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        results = embedder.embed_batch([])
        assert results == []

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_batch_single_item(self, mock_httpx, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.5] * 1024
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = MagicMock(prompt_tokens=3, total_tokens=3)
        mock_client.embeddings.create.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        results = embedder.embed_batch(["hello"])
        assert len(results) == 1
        assert results[0].dense_vector == [0.5] * 1024
        mock_client.embeddings.create.assert_called_once()

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_batch_error_raises_runtime_error(self, mock_httpx, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = Exception("batch fail")

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        with pytest.raises(RuntimeError, match="DashScope batch embedding failed"):
            embedder.embed_batch(["a", "b"])


# ---------------------------------------------------------------------------
# Multimodal embed
# ---------------------------------------------------------------------------


class TestDashScopeMultimodalEmbed:
    """Test multimodal mode embed with mocked httpx.Client."""

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_returns_correct_vector(self, mock_httpx_class, mock_openai):
        mock_client = MagicMock()
        mock_httpx_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {"embeddings": [{"embedding": [0.1] * 768}]},
            "usage": {"total_tokens": 10, "input_tokens": 10},
        }
        mock_client.post.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        result = embedder.embed("hello world")
        assert result.dense_vector == [0.1] * 768
        assert result.is_dense

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_sends_multimodal_params(self, mock_httpx_class, mock_openai):
        mock_client = MagicMock()
        mock_httpx_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {"embeddings": [{"embedding": [0.1] * 768}]},
            "usage": {"total_tokens": 10, "input_tokens": 10},
        }
        mock_client.post.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
            enable_fusion=True,
            res_level=2,
            max_video_frames=16,
        )
        embedder.embed("test text")

        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        assert body["model"] == "tongyi-embedding-vision-flash"
        assert body["input"]["contents"] == [{"text": "test text"}]
        assert body["parameters"]["enable_fusion"] is True
        assert body["parameters"]["res_level"] == 2
        assert body["parameters"]["max_video_frames"] == 16

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_without_optional_params(self, mock_httpx_class, mock_openai):
        mock_client = MagicMock()
        mock_httpx_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {"embeddings": [{"embedding": [0.1] * 768}]},
            "usage": {"total_tokens": 5, "input_tokens": 5},
        }
        mock_client.post.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        embedder.embed("hello")

        call_kwargs = mock_client.post.call_args[1]
        body = call_kwargs["json"]
        # dimension is always set (from model default), but optional params should be absent
        assert "dimension" in body.get("parameters", {})
        assert "enable_fusion" not in body.get("parameters", {})
        assert "res_level" not in body.get("parameters", {})
        assert "max_video_frames" not in body.get("parameters", {})

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_api_error_raises_runtime_error(self, mock_httpx_class, mock_openai):
        mock_client = MagicMock()
        mock_httpx_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 500")
        mock_client.post.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        with pytest.raises(RuntimeError, match="DashScope embedding failed"):
            embedder.embed("hello")

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_multimodal_url_uses_correct_endpoint(self, mock_httpx_class, mock_openai):
        mock_client = MagicMock()
        mock_httpx_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {"embeddings": [{"embedding": [0.1] * 768}]},
            "usage": {"total_tokens": 5, "input_tokens": 5},
        }
        mock_client.post.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        embedder.embed("test")

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1].get("url")
        assert "multimodal-embedding" in url


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------


class TestDashScopeAsync:
    """Test async methods with mocked async clients."""

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    @patch("openviking.models.embedder.dashscope_embedders.openai.AsyncOpenAI")
    @pytest.mark.anyio
    async def test_embed_async_text_mode(self, mock_async_openai_class, mock_httpx, mock_openai):
        mock_async_client = MagicMock()
        mock_async_openai_class.return_value = mock_async_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.3] * 1024
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = MagicMock(prompt_tokens=8, total_tokens=8)
        mock_async_client.embeddings.create = AsyncMock(return_value=mock_response)

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        result = await embedder.embed_async("hello async")
        assert result.dense_vector == [0.3] * 1024

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.AsyncClient")
    @pytest.mark.anyio
    async def test_embed_async_multimodal_mode(
        self, mock_async_httpx_class, mock_httpx, mock_openai
    ):
        mock_async_client = MagicMock()
        mock_async_httpx_class.return_value = mock_async_client

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {"embeddings": [{"embedding": [0.4] * 768}]},
            "usage": {"total_tokens": 12, "input_tokens": 12},
        }
        mock_async_client.post = AsyncMock(return_value=mock_response)

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        result = await embedder.embed_async("hello multimodal async")
        assert result.dense_vector == [0.4] * 768

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    @patch("openviking.models.embedder.dashscope_embedders.openai.AsyncOpenAI")
    @pytest.mark.anyio
    async def test_embed_async_text_error_raises_runtime_error(
        self, mock_async_openai_class, mock_httpx, mock_openai
    ):
        mock_async_client = MagicMock()
        mock_async_openai_class.return_value = mock_async_client
        mock_async_client.embeddings.create = AsyncMock(side_effect=Exception("async fail"))

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        with pytest.raises(RuntimeError, match="DashScope embedding failed"):
            await embedder.embed_async("fail text")

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    @patch("openviking.models.embedder.dashscope_embedders.openai.AsyncOpenAI")
    @pytest.mark.anyio
    async def test_embed_batch_async_text_mode(
        self, mock_async_openai_class, mock_httpx, mock_openai
    ):
        mock_async_client = MagicMock()
        mock_async_openai_class.return_value = mock_async_client

        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=[0.1] * 1024),
            MagicMock(embedding=[0.2] * 1024),
        ]
        mock_response.usage = MagicMock(prompt_tokens=20, total_tokens=20)
        mock_async_client.embeddings.create = AsyncMock(return_value=mock_response)

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        results = await embedder.embed_batch_async(["a", "b"])
        assert len(results) == 2
        assert results[0].dense_vector == [0.1] * 1024
        assert results[1].dense_vector == [0.2] * 1024


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestDashScopeErrors:
    """Test error handling across modes."""

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_text_api_error(self, mock_httpx, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = Exception("connection refused")

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        with pytest.raises(RuntimeError, match="DashScope embedding failed") as exc_info:
            embedder.embed("hello")
        assert "connection refused" in str(exc_info.value.__cause__)

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_multimodal_api_error(self, mock_httpx_class, mock_openai):
        mock_client = MagicMock()
        mock_httpx_class.return_value = mock_client
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 503 Service Unavailable")
        mock_client.post.return_value = mock_response

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        with pytest.raises(RuntimeError, match="DashScope embedding failed") as exc_info:
            embedder.embed("hello")
        assert "503" in str(exc_info.value.__cause__)

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_embed_batch_error(self, mock_httpx, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.embeddings.create.side_effect = Exception("timeout")

        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4", api_key="sk-test", input_type="text"
        )
        with pytest.raises(RuntimeError, match="DashScope batch embedding failed"):
            embedder.embed_batch(["text1", "text2"])

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_multimodal_batch_sends_one_at_a_time(self, mock_httpx_class, mock_openai):
        """Multimodal batch sends requests one text at a time (no chunking)."""
        mock_client = MagicMock()
        mock_httpx_class.return_value = mock_client

        # Each call returns a single embedding
        responses = []
        for i in range(3):
            resp = MagicMock()
            resp.json.return_value = {
                "output": {"embeddings": [{"embedding": [float(i)] * 768}]},
                "usage": {"total_tokens": 5, "input_tokens": 5},
            }
            responses.append(resp)
        mock_client.post.side_effect = responses

        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        results = embedder.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        assert mock_client.post.call_count == 3
        assert results[0].dense_vector == [0.0] * 768
        assert results[1].dense_vector == [1.0] * 768
        assert results[2].dense_vector == [2.0] * 768


# ---------------------------------------------------------------------------
# Multimodal params builder
# ---------------------------------------------------------------------------


class TestDashScopeMultimodalParams:
    """Test _multimodal_params and _multimodal_body helpers."""

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_params_excludes_none_values(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        params = embedder._multimodal_params()
        assert "dimension" in params  # always set from model default
        assert "enable_fusion" not in params
        assert "res_level" not in params
        assert "max_video_frames" not in params

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_params_includes_all_set_values(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
            enable_fusion=True,
            res_level=3,
            max_video_frames=8,
        )
        params = embedder._multimodal_params()
        assert params["dimension"] == 768  # default for flash
        assert params["enable_fusion"] is True
        assert params["res_level"] == 3
        assert params["max_video_frames"] == 8

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_body_structure(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="tongyi-embedding-vision-flash",
            api_key="sk-test",
            input_type="multimodal",
        )
        body = embedder._multimodal_body("some text")
        assert body["model"] == "tongyi-embedding-vision-flash"
        assert body["input"]["contents"] == [{"text": "some text"}]
        assert "parameters" in body

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_body_no_params_when_all_none_except_dimension(self, mock_httpx, mock_openai):
        """When only dimension is set (from default), parameters dict still exists."""
        embedder = DashScopeDenseEmbedder(
            model_name="text-embedding-v4",
            api_key="sk-test",
            input_type="multimodal",
            enable_fusion=None,
            res_level=None,
            max_video_frames=None,
        )
        body = embedder._multimodal_body("hello")
        # dimension is always set, so parameters should exist
        assert "parameters" in body
        assert body["parameters"]["dimension"] == 1024


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestDashScopeLifecycle:
    """Test lifecycle methods."""

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_close_calls_httpx_client_close(self, mock_httpx_class, mock_openai):
        mock_httpx_client = MagicMock()
        mock_httpx_class.return_value = mock_httpx_client

        embedder = DashScopeDenseEmbedder(model_name="text-embedding-v4", api_key="sk-test")
        embedder.close()
        mock_httpx_client.close.assert_called_once()

    @patch("openviking.models.embedder.dashscope_embedders.openai.OpenAI")
    @patch("openviking.models.embedder.dashscope_embedders.httpx.Client")
    def test_get_dimension(self, mock_httpx, mock_openai):
        embedder = DashScopeDenseEmbedder(
            model_name="qwen3-vl-embedding",
            api_key="sk-test",
        )
        assert embedder.get_dimension() == 2560
