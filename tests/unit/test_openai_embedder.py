# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAI Embedder"""

from unittest.mock import MagicMock, patch

from openviking.models.embedder import OpenAIDenseEmbedder
from openviking.utils.embedding_input import (
    EMBEDDING_TRUNCATION_SUFFIX,
    estimate_embedding_input_tokens,
)


class TestOpenAIDenseEmbedder:
    """Test cases for OpenAIDenseEmbedder"""

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_does_not_send_dimensions(self, mock_openai_class):
        """OpenAI embed should omit dimensions param"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1024,
        )

        embedder.embed("Hello world")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "dimensions" not in call_kwargs

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_with_input_type_none(self, mock_openai_class):
        """OpenAI embed should not include extra_body when input_type is None"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
        )

        embedder.embed("Hello world")

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" not in call_kwargs

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_default_input_type_embed_downgrades_multimodal_parts(self, mock_openai_class):
        """Direct embed(parts) should also apply the text-only input guard."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )
        parts = [
            {"type": "text", "text": "find this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ]

        embedder.embed(parts)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == "find this"

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_preserves_openai_batch_text_input(self, mock_openai_class):
        """OpenAI batch text input should keep the historical list[str] passthrough."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )

        embedder.embed(["first text", "second text"])

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == ["first text", "second text"]

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_multimodal_input_type_passes_content_parts(self, mock_openai_class):
        """OpenAI-compatible multimodal mode should pass content parts to embeddings.create."""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="custom-multimodal-embedding",
            api_key="test-api-key",
            api_base="https://example.com/v1",
            dimension=1536,
            input_type="multimodal",
        )
        parts = [
            {"type": "text", "text": "find this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ]

        assert embedder.supports_multimodal is True
        assert embedder.prepare_embedding_input(parts) == parts

        embedder.embed(parts)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert call_kwargs["input"] == parts

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_with_context_query(self, mock_openai_class):
        """OpenAI embed should include extra_body with input_type='query' when is_query=True"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            query_param="query",
        )

        embedder.embed("Hello world", is_query=True)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == {"input_type": "query"}

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_embed_with_context_document(self, mock_openai_class):
        """OpenAI embed should include extra_body with input_type='passage' when is_query=False"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            document_param="passage",
        )

        embedder.embed("Hello world", is_query=False)

        call_kwargs = mock_client.embeddings.create.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == {"input_type": "passage"}

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_telemetry_skipped_when_no_usage(self, mock_openai_class):
        """_update_telemetry_token_usage should no-op when response has no usage"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = None
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )
        result = embedder.embed("Hello world")
        assert result.dense_vector is not None

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_telemetry_skipped_when_module_missing(self, mock_openai_class):
        """_update_telemetry_token_usage should silently no-op when telemetry module is not available"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.total_tokens = 10

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = mock_usage
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )

        with patch("importlib.import_module", side_effect=ImportError("no telemetry")):
            result = embedder.embed("Hello world")

        assert result.dense_vector is not None

    @patch("openviking.models.embedder.openai_embedders.openai.OpenAI")
    def test_telemetry_called_when_module_available(self, mock_openai_class):
        """_update_telemetry_token_usage should call telemetry when module is available"""
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 8
        mock_usage.total_tokens = 8

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = mock_usage
        mock_client.embeddings.create.return_value = mock_response

        embedder = OpenAIDenseEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-api-key",
            dimension=1536,
        )

        mock_telemetry = MagicMock()

        with patch(
            "openviking.models.embedder.openai_embedders.get_current_telemetry",
            return_value=mock_telemetry,
        ):
            result = embedder.embed("Hello world")

        assert result.dense_vector is not None
        mock_telemetry.add_token_usage_by_source.assert_called_once_with("embedding", 8, 0)


class TestEmbeddingInputTruncationGuard:
    """Plain list[str] inputs must respect max_input_tokens at both guard layers.

    Regression tests for the truncation guard being skipped for plain text
    batches: the OpenAI-compatible lower guard passed them through untouched,
    while the base guard collapsed them to an empty string for text-only
    embedders and left them unbounded for multimodal embedders.
    """

    OVERSIZED_TEXT = "x" * 5000
    TOKEN_BUDGET = 64

    def _make_embedder(self, input_type=None, max_input_tokens=TOKEN_BUDGET):
        config = {}
        if max_input_tokens is not None:
            config["max_input_tokens"] = max_input_tokens
        kwargs = {
            "model_name": "embedding-3",
            "api_key": "test-api-key",
            "dimension": 1536,
            "config": config,
        }
        if input_type is not None:
            kwargs["input_type"] = input_type
        return OpenAIDenseEmbedder(**kwargs)

    def _assert_within_budget(self, item):
        """Each truncated item keeps its content within the budget (suffix overhead allowed)."""
        assert item.endswith(EMBEDDING_TRUNCATION_SUFFIX.lstrip())
        suffix_budget = estimate_embedding_input_tokens(EMBEDDING_TRUNCATION_SUFFIX)
        assert estimate_embedding_input_tokens(item) <= self.TOKEN_BUDGET + suffix_budget

    def test_lower_guard_truncates_plain_list_items(self):
        """_prepare_embedding_input should bound every item of a plain text batch."""
        embedder = self._make_embedder()
        result = embedder._prepare_embedding_input([self.OVERSIZED_TEXT, "short text"])

        assert isinstance(result, list)
        assert len(result) == 2
        self._assert_within_budget(result[0])
        assert result[1] == "short text"

    def test_lower_guard_keeps_list_when_no_budget(self):
        """Without max_input_tokens the plain text batch is returned as-is."""
        embedder = self._make_embedder(max_input_tokens=None)
        batch = ["first text", "second text"]

        assert embedder._prepare_embedding_input(batch) == batch

    def test_upper_guard_does_not_collapse_plain_list_for_text_only(self):
        """Text-only embedders must not fold a plain text batch into an empty string."""
        embedder = self._make_embedder()
        assert embedder.supports_multimodal is False

        result = embedder.prepare_embedding_input([self.OVERSIZED_TEXT])

        assert isinstance(result, list)
        assert len(result) == 1
        self._assert_within_budget(result[0])

    def test_upper_guard_truncates_plain_list_for_multimodal(self):
        """Multimodal embedders must not pass plain text batch items through unbounded."""
        embedder = self._make_embedder(input_type="multimodal")
        assert embedder.supports_multimodal is True

        result = embedder.prepare_embedding_input([self.OVERSIZED_TEXT])

        assert isinstance(result, list)
        assert len(result) == 1
        self._assert_within_budget(result[0])

    def test_embed_request_carries_truncated_list(self):
        """embed() must send the truncated batch, not the raw oversized one."""
        mock_client = MagicMock()
        with patch(
            "openviking.models.embedder.openai_embedders.openai.OpenAI"
        ) as mock_openai_class:
            mock_openai_class.return_value = mock_client
            mock_embedding = MagicMock()
            mock_embedding.embedding = [0.1] * 1536
            mock_response = MagicMock()
            mock_response.data = [mock_embedding]
            mock_client.embeddings.create.return_value = mock_response

            embedder = self._make_embedder()
            embedder.embed([self.OVERSIZED_TEXT, "short text"])

        call_kwargs = mock_client.embeddings.create.call_args[1]
        sent_input = call_kwargs["input"]
        assert isinstance(sent_input, list)
        assert len(sent_input) == 2
        self._assert_within_budget(sent_input[0])
        assert sent_input[1] == "short text"

    def test_single_string_truncation_unchanged(self):
        """The single-string path keeps its existing truncation behavior."""
        embedder = self._make_embedder()

        result = embedder.prepare_embedding_input(self.OVERSIZED_TEXT)

        assert isinstance(result, str)
        self._assert_within_budget(result)
