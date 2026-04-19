# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAI-compatible rerank client (DashScope, etc.)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from openviking.models.rerank import OpenAIRerankClient


class TestOpenAIRerankClient:
    """Test cases for OpenAIRerankClient."""

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_basic(self, mock_post):
        """Basic rerank returns scores in original order."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.3},
            ]
        }
        mock_post.return_value = mock_response

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        scores = client.rerank_batch("What is UCW?", ["doc A", "doc B"])

        assert scores == [0.9, 0.3]
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "qwen3-rerank"
        assert payload["query"] == "What is UCW?"
        assert payload["documents"] == ["doc A", "doc B"]

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_empty_input_returns_empty_list(self, mock_post):
        """Empty document list returns empty list (not None)."""
        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        assert client.rerank_batch("query", []) == []

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_all_empty_returns_none(self, mock_post):
        """All-empty documents signal caller to fall back (return None).

        This is the key correctness fix: when every document is empty/whitespace,
        we must return None (not [0.0, 0.0, ...]) so the retriever's fallback
        to vector scores is triggered.
        """
        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        result = client.rerank_batch("query", ["", "", ""])
        assert result is None
        mock_post.assert_not_called()  # No HTTP call should be made

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_all_whitespace_returns_none(self, mock_post):
        """All-whitespace documents also signal fallback (return None)."""
        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        result = client.rerank_batch("query", ["  ", "\t", "\n"])
        assert result is None
        mock_post.assert_not_called()

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_mixed_empty_and_non_empty(self, mock_post):
        """Mixed empty/non-empty documents: filter empty, rerank non-empty.

        Empty documents get score 0.0 in the returned list; non-empty
        documents get actual rerank scores.
        """
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.75},  # "real doc" at original index 1
            ]
        }
        mock_post.return_value = mock_response

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        scores = client.rerank_batch("query", ["", "real doc", "  "])

        assert scores == [0.0, 0.75, 0.0]
        # Only the non-empty doc should be sent to the API
        payload = mock_post.call_args[1]["json"]
        assert payload["documents"] == ["real doc"]

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_filters_empty_debug_log(self, mock_post, caplog):
        """Filtering empty documents should log the count."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": [{"index": 0, "relevance_score": 0.5}]}
        mock_post.return_value = mock_response

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        with caplog.at_level("DEBUG"):
            client.rerank_batch("q", ["", "doc", "  "])

        assert "Filtered 2 empty documents from 3 total" in caplog.text

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_api_error_returns_none(self, mock_post):
        """API error returns None so caller can fall back."""
        mock_post.side_effect = requests.exceptions.Timeout("connection timeout")

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        result = client.rerank_batch("query", ["doc"])
        assert result is None

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_http_400_returns_none(self, mock_post):
        """HTTP 400 (e.g., DashScope rejecting empty docs without filter) returns None."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("400 Bad Request")
        mock_post.return_value = mock_response

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        result = client.rerank_batch("query", ["doc"])
        assert result is None

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_batch_unexpected_result_length_returns_none(self, mock_post):
        """Mismatched result length signals failure (return None)."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [{"index": 0, "relevance_score": 0.5}]  # only 1 result for 2 docs
        }
        mock_post.return_value = mock_response

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        result = client.rerank_batch("query", ["doc1", "doc2"])
        assert result is None

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_rerank_preserves_original_order(self, mock_post):
        """Rerank results are mapped back to original document order."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        # API returns out of order
        mock_response.json.return_value = {
            "results": [
                {"index": 2, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.50},
                {"index": 1, "relevance_score": 0.01},
            ]
        }
        mock_post.return_value = mock_response

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
        )
        scores = client.rerank_batch("q", ["first", "second", "third"])

        assert scores[0] == 0.50  # "first" was index 0
        assert scores[1] == 0.01  # "second" was index 1
        assert scores[2] == 0.99  # "third" was index 2

    @patch("openviking.models.rerank.openai_rerank.requests.post")
    def test_extra_headers_passed_to_request(self, mock_post):
        """Extra headers from config are included in the request."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": [{"index": 0, "relevance_score": 0.5}]}
        mock_post.return_value = mock_response

        client = OpenAIRerankClient(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/api/v1/rerank",
            model_name="qwen3-rerank",
            extra_headers={"X-Custom-Header": "custom-value"},
        )
        client.rerank_batch("q", ["doc"])

        headers = mock_post.call_args[1]["headers"]
        assert headers["X-Custom-Header"] == "custom-value"
        assert headers["Authorization"] == "Bearer test-key"
