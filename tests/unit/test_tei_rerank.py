# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Hugging Face Text Embeddings Inference rerank client."""

from unittest.mock import MagicMock, patch

from openviking.models.rerank import RerankClient, TEIRerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig


class TestTEIRerankClient:
    """Test cases for TEIRerankClient."""

    @patch("openviking.models.rerank.tei_rerank.requests.post")
    def test_rerank_batch_basic(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"index": 1, "score": 0.95},
            {"index": 0, "score": 0.42},
            {"index": 2, "score": 0.10},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        client = TEIRerankClient(
            api_base="http://tei.local:8080",
            api_key="test-key",
            model_name="BAAI/bge-reranker-v2-m3",
        )
        scores = client.rerank_batch("What is UCW?", ["doc A", "doc B", "doc C"])

        assert scores == [0.42, 0.95, 0.10]
        kwargs = mock_post.call_args.kwargs
        assert kwargs["url"] == "http://tei.local:8080/rerank"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert kwargs["json"] == {
            "query": "What is UCW?",
            "texts": ["doc A", "doc B", "doc C"],
            "raw_scores": False,
        }

    @patch("openviking.models.rerank.tei_rerank.requests.post")
    def test_rerank_batch_accepts_full_endpoint(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"index": 0, "score": 0.9}]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        client = TEIRerankClient(api_base="http://tei.local:8080/rerank")
        assert client.rerank_batch("q", ["doc"]) == [0.9]
        assert mock_post.call_args.kwargs["url"] == "http://tei.local:8080/rerank"
        assert "Authorization" not in mock_post.call_args.kwargs["headers"]

    @patch("openviking.models.rerank.tei_rerank.requests.post")
    def test_rerank_batch_fills_missing_top_n_results(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"index": 2, "score": 0.99}]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        client = TEIRerankClient(api_base="http://tei.local:8080")
        assert client.rerank_batch("q", ["first", "second", "third"]) == [0.0, 0.0, 0.99]

    @patch("openviking.models.rerank.tei_rerank.requests.post")
    def test_rerank_batch_supports_results_wrapper(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.7},
                {"index": 1, "relevance_score": 0.2},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        client = TEIRerankClient(api_base="http://tei.local:8080")
        assert client.rerank_batch("q", ["first", "second"]) == [0.7, 0.2]

    @patch("openviking.models.rerank.tei_rerank.requests.post")
    def test_rerank_batch_invalid_index_returns_none(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"index": 10, "score": 0.99}]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        client = TEIRerankClient(api_base="http://tei.local:8080")
        assert client.rerank_batch("q", ["doc"]) is None

    @patch("openviking.models.rerank.tei_rerank.requests.post")
    def test_rerank_batch_api_error_returns_none(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = RuntimeError("boom")
        mock_post.return_value = mock_response

        client = TEIRerankClient(api_base="http://tei.local:8080")
        assert client.rerank_batch("q", ["doc"]) is None

    def test_rerank_batch_empty(self):
        client = TEIRerankClient(api_base="http://tei.local:8080")
        assert client.rerank_batch("query", []) == []

    @patch("openviking.models.rerank.tei_rerank.requests.post")
    def test_rerank_batch_chunks_documents(self, mock_post):
        first_response = MagicMock()
        first_response.json.return_value = [
            {"index": 1, "score": 0.2},
            {"index": 0, "score": 0.1},
        ]
        first_response.raise_for_status = MagicMock()
        second_response = MagicMock()
        second_response.json.return_value = [{"index": 0, "score": 0.3}]
        second_response.raise_for_status = MagicMock()
        mock_post.side_effect = [first_response, second_response]

        client = TEIRerankClient(api_base="http://tei.local:8080", batch_size=2)

        assert client.rerank_batch("q", ["a", "b", "c"]) == [0.1, 0.2, 0.3]
        assert mock_post.call_count == 2
        assert mock_post.call_args_list[0].kwargs["json"]["texts"] == ["a", "b"]
        assert mock_post.call_args_list[1].kwargs["json"]["texts"] == ["c"]


class TestTEIRerankConfig:
    """Test TEI rerank config parsing and dispatch."""

    def test_config_requires_api_base(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RerankConfig(provider="tei")

    def test_config_available_without_api_key(self):
        config = RerankConfig(provider="tei", api_base="http://tei.local:8080")

        assert config._effective_provider() == "tei"
        assert config.is_available() is True

    def test_config_auto_detects_tei_without_api_key(self):
        config = RerankConfig(api_base="http://tei.local:8080")

        assert config._effective_provider() == "tei"
        assert config.is_available() is True

    def test_config_api_key_and_api_base_auto_detects_openai(self):
        config = RerankConfig(api_key="key", api_base="https://example.com/rerank")

        assert config._effective_provider() == "openai"
        assert config.is_available() is True

    def test_from_config_creates_tei_client(self):
        config = RerankConfig(
            provider="tei",
            api_base="http://tei.local:8080",
            api_key="key",
            model="BAAI/bge-reranker-v2-m3",
            extra_headers={"X-Test": "1"},
            batch_size=16,
        )

        client = RerankClient.from_config(config)

        assert isinstance(client, TEIRerankClient)
        assert client.api_base == "http://tei.local:8080"
        assert client.api_key == "key"
        assert client.model_name == "BAAI/bge-reranker-v2-m3"
        assert client.extra_headers == {"X-Test": "1"}
        assert client.batch_size == 16

    def test_config_default_batch_size_is_tei_safe(self):
        config = RerankConfig(provider="tei", api_base="http://tei.local:8080")

        assert config.batch_size == 32
