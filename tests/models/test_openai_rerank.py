"""Tests for OpenAIRerankClient — DashScope compatibility and standard format.

Regression for #3459: OpenAIRerankClient was incompatible with DashScope
because it sent a flat request body and parsed ``results`` at the top level,
while DashScope expects a nested envelope and returns ``output.results``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from openviking.models.rerank.openai_rerank import (
    OpenAIRerankClient,
    _is_dashscope,
)


# ─── _is_dashscope ───


class TestIsDashscope:
    def test_dashscope_url(self):
        assert _is_dashscope("https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank") is True

    def test_non_dashscope_url(self):
        assert _is_dashscope("https://api.openai.com/v1/rerank") is False

    def test_empty_url(self):
        assert _is_dashscope("") is False


# ─── Request body construction ───


class TestBuildRequestBody:
    def test_dashscope_nested_body(self):
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank",
            model_name="qwen3-rerank",
        )
        body = client._build_request_body("hello", ["doc1", "doc2"])
        assert "input" in body
        assert body["input"]["query"] == "hello"
        assert body["input"]["documents"] == ["doc1", "doc2"]
        assert body["parameters"]["return_documents"] is False
        assert body["model"] == "qwen3-rerank"
        # flat keys should NOT be present at top level
        assert "query" not in body
        assert "documents" not in body

    def test_standard_flat_body(self):
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://api.openai.com/v1/rerank",
            model_name="rerank-v2",
        )
        body = client._build_request_body("hello", ["doc1", "doc2"])
        assert body["query"] == "hello"
        assert body["documents"] == ["doc1", "doc2"]
        assert body["model"] == "rerank-v2"
        # nested keys should NOT be present
        assert "input" not in body
        assert "parameters" not in body


# ─── Response parsing ───


class TestExtractResults:
    def test_dashscope_nested_results(self):
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank",
            model_name="qwen3-rerank",
        )
        response = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.95},
                    {"index": 1, "relevance_score": 0.12},
                ]
            },
            "request_id": "abc123",
        }
        results = client._extract_results(response)
        assert results is not None
        assert len(results) == 2
        assert results[0]["relevance_score"] == 0.95

    def test_standard_top_level_results(self):
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://api.openai.com/v1/rerank",
            model_name="rerank-v2",
        )
        response = {
            "results": [
                {"index": 0, "relevance_score": 0.88},
                {"index": 1, "relevance_score": 0.42},
            ]
        }
        results = client._extract_results(response)
        assert results is not None
        assert len(results) == 2

    def test_dashscope_missing_output(self):
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank",
            model_name="qwen3-rerank",
        )
        assert client._extract_results({"request_id": "abc"}) is None

    def test_standard_missing_results(self):
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://api.openai.com/v1/rerank",
            model_name="rerank-v2",
        )
        assert client._extract_results({"id": "abc"}) is None


# ─── End-to-end rerank_batch with mocked HTTP ───


class TestRerankBatch:
    def test_dashscope_full_flow(self):
        """DashScope end-to-end: nested request, nested response."""
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank",
            model_name="qwen3-rerank",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 0, "relevance_score": 0.95},
                    {"index": 1, "relevance_score": 0.12},
                ]
            },
            "request_id": "abc123",
        }

        with patch("openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response) as mock_post:
            scores = client.rerank_batch("hello", ["doc1", "doc2"])

        assert scores == [0.95, 0.12]

        # Verify the request body was nested
        _, kwargs = mock_post.call_args
        sent_body = kwargs["json"]
        assert "input" in sent_body
        assert sent_body["input"]["query"] == "hello"
        assert sent_body["input"]["documents"] == ["doc1", "doc2"]

    def test_standard_full_flow(self):
        """Standard OpenAI-compatible end-to-end: flat request, flat response."""
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://api.openai.com/v1/rerank",
            model_name="rerank-v2",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {"index": 0, "relevance_score": 0.88},
                {"index": 1, "relevance_score": 0.42},
            ]
        }

        with patch("openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response) as mock_post:
            scores = client.rerank_batch("hello", ["doc1", "doc2"])

        assert scores == [0.88, 0.42]

        # Verify the request body was flat
        _, kwargs = mock_post.call_args
        sent_body = kwargs["json"]
        assert sent_body["query"] == "hello"
        assert sent_body["documents"] == ["doc1", "doc2"]
        assert "input" not in sent_body

    def test_dashscope_relevance_scores_plural_key(self):
        """Some DashScope models return 'relevance_scores' (plural) instead of singular."""
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank",
            model_name="qwen3-rerank",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "output": {
                "results": [
                    {"index": 0, "relevance_scores": 0.77},
                    {"index": 1, "relevance_scores": 0.33},
                ]
            }
        }

        with patch("openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response):
            scores = client.rerank_batch("hello", ["doc1", "doc2"])

        assert scores == [0.77, 0.33]

    def test_empty_documents(self):
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank",
            model_name="qwen3-rerank",
        )
        assert client.rerank_batch("hello", []) == []

    def test_sparse_results_fill_zero(self):
        """Sparse results: missing indexes should get score 0.0."""
        client = OpenAIRerankClient(
            api_key="sk-test",
            api_base="https://api.openai.com/v1/rerank",
            model_name="rerank-v2",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status.return_value = None
        # Only index 1 returned, index 0 and 2 missing
        mock_response.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.55},
            ]
        }

        with patch("openviking.models.rerank.openai_rerank.requests.post", return_value=mock_response):
            scores = client.rerank_batch("hello", ["doc0", "doc1", "doc2"])

        assert scores == [0.0, 0.55, 0.0]
