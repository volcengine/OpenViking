# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAI-compatible rerank client and factory dispatch."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from openviking.models.rerank import OpenAIRerankClient, RerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig


class TestOpenAIRerankClient:
    def _make_client(self):
        return OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank",
            model_name="gte-rerank-v2",
        )

    def test_rerank_batch_success(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.3},
                    {"index": 2, "relevance_score": 0.7},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            scores = client.rerank_batch("test query", ["doc1", "doc2", "doc3"])

        assert scores == [0.9, 0.3, 0.7]

    def test_rerank_batch_out_of_order_results(self):
        """Results returned out-of-order should be re-ordered by index."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 2, "relevance_score": 0.7},
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 1, "relevance_score": 0.3},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            scores = client.rerank_batch("test query", ["doc1", "doc2", "doc3"])

        assert scores == [0.9, 0.3, 0.7]

    def test_rerank_batch_empty_documents(self):
        client = self._make_client()
        scores = client.rerank_batch("query", [])
        assert scores == []

    def test_rerank_batch_unexpected_format_returns_none(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "format"}
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None

    def test_rerank_batch_sparse_results_fill_missing_scores_with_zero(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 2, "relevance_score": 0.7},
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ), patch("openviking.models.rerank.openai_rerank.logger.warning") as warning:
            result = client.rerank_batch("query", ["doc1", "doc2", "doc3"])

        assert result == [0.9, 0.0, 0.7]
        warning.assert_called_once_with(
            "[OpenAIRerankClient] Sparse rerank results: expected=%s actual=%s", 3, 2
        )

    def test_rerank_batch_out_of_bounds_index_returns_none(self):
        """An index that is >= len(documents) should return None."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 5, "relevance_score": 0.9},  # only 1 doc
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None

    def test_rerank_batch_missing_index_field_returns_none(self):
        """A result item with no 'index' key should return None."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"relevance_score": 0.9},  # missing 'index'
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None

    def test_rerank_batch_http_error_returns_none(self):
        client = self._make_client()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post",
            side_effect=Exception("connection error"),
        ):
            result = client.rerank_batch("query", ["doc1"])

        assert result is None

    def test_rerank_batch_sends_correct_request(self):
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output": {"results": [{"index": 0, "relevance_score": 0.8}]}
        }
        mock_response.raise_for_status = MagicMock()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response
        ) as mock_post:
            client.rerank_batch("my query", ["doc1"])

        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["url"] == "https://dashscope.aliyuncs.com/api/v1/services/rerank"
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-key"
        body = call_kwargs.kwargs["json"]
        assert body["model"] == "gte-rerank-v2"
        assert body["input"]["query"] == "my query"
        assert body["input"]["documents"] == ["doc1"]
        assert body["parameters"]["return_documents"] is False

    def test_from_config(self):
        config = RerankConfig(
            provider="openai",
            api_key="my-key",
            api_base="https://example.com/rerank",
            model="qwen3-rerank",
        )
        client = OpenAIRerankClient.from_config(config)
        assert isinstance(client, OpenAIRerankClient)
        assert client.api_key == "my-key"
        assert client.api_base == "https://example.com/rerank"
        assert client.model_name == "qwen3-rerank"

    def test_from_config_default_model(self):
        config = RerankConfig(
            provider="openai",
            api_key="my-key",
            api_base="https://example.com/rerank",
        )
        client = OpenAIRerankClient.from_config(config)
        assert client.model_name == "qwen3-rerank"

    def test_from_config_unavailable_returns_none(self):
        result = OpenAIRerankClient.from_config(None)
        assert result is None


class TestRerankClientFactoryDispatch:
    def test_factory_dispatches_to_openai_client(self):
        config = RerankConfig(
            provider="openai",
            api_key="test-key",
            api_base="https://example.com/rerank",
            model="qwen3-rerank",
        )
        client = RerankClient.from_config(config)
        assert isinstance(client, OpenAIRerankClient)

    def test_factory_dispatches_to_vikingdb_client(self):
        config = RerankConfig(
            provider="vikingdb",
            ak="test-ak",
            sk="test-sk",
        )
        client = RerankClient.from_config(config)
        assert isinstance(client, RerankClient)
        assert not isinstance(client, OpenAIRerankClient)

    def test_factory_defaults_to_vikingdb(self):
        """Config without provider field defaults to vikingdb."""
        config = RerankConfig(ak="test-ak", sk="test-sk")
        client = RerankClient.from_config(config)
        assert isinstance(client, RerankClient)
        assert not isinstance(client, OpenAIRerankClient)

    def test_factory_returns_none_for_none_config(self):
        assert RerankClient.from_config(None) is None

    def test_factory_returns_none_for_unavailable_vikingdb_config(self):
        config = RerankConfig()  # no ak/sk
        assert RerankClient.from_config(config) is None

    def test_factory_returns_none_for_unavailable_openai_config(self):
        # This should raise validation error since openai requires api_key + api_base
        with pytest.raises(ValidationError):
            RerankConfig(provider="openai")


class TestRerankConfig:
    def test_vikingdb_is_available(self):
        config = RerankConfig(ak="ak", sk="sk")
        assert config.is_available() is True

    def test_vikingdb_not_available_without_credentials(self):
        config = RerankConfig()
        assert config.is_available() is False

    def test_openai_is_available(self):
        config = RerankConfig(
            provider="openai",
            api_key="key",
            api_base="https://example.com/rerank",
        )
        assert config.is_available() is True

    def test_openai_requires_api_key_and_api_base(self):
        with pytest.raises(ValidationError):
            RerankConfig(provider="openai", api_key="key")

        with pytest.raises(ValidationError):
            RerankConfig(provider="openai", api_base="https://example.com/rerank")

    def test_default_provider_is_vikingdb(self):
        config = RerankConfig()
        assert config.provider == "vikingdb"

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="provider"):
            RerankConfig(provider="cohere", ak="ak", sk="sk")
