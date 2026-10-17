# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Hugging Face Text Embeddings Inference rerank client."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import ValidationError

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
        assert kwargs["timeout"] == 30.0
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
            timeout=120,
            batch_size=16,
        )

        client = RerankClient.from_config(config)

        assert isinstance(client, TEIRerankClient)
        assert client.api_base == "http://tei.local:8080"
        assert client.api_key == "key"
        assert client.model_name == "BAAI/bge-reranker-v2-m3"
        assert client.extra_headers == {"X-Test": "1"}
        assert client.timeout == 120.0
        assert client.batch_size == 16

    def test_config_default_batch_size_is_tei_safe(self):
        config = RerankConfig(provider="tei", api_base="http://tei.local:8080")

        assert config.batch_size == 32


# Mock-only protocol regressions: no running TEI server is required.


@patch("openviking.models.rerank.tei_rerank.requests.post")
def test_actual_three_chunk_requests_timeout_and_headers(mock_post):
    responses = []
    for rows in (
        [{"index": 1, "score": 0.2}, {"index": 0, "score": 0.1}],
        [{"index": 1, "score": 0.4}, {"index": 0, "score": 0.3}],
        [{"index": 0, "score": 0.5}],
    ):
        response = MagicMock()
        response.json.return_value = rows
        responses.append(response)
    mock_post.side_effect = responses
    client = RerankClient.from_config(
        RerankConfig(
            provider="tei",
            api_base="http://tei.local/rerank/",
            api_key="token",
            batch_size=2,
            timeout=7.5,
            extra_headers={"Authorization": "Bearer override", "X-Test": "yes"},
        )
    )
    assert client.rerank_batch("q", ["a", "b", "c", "d", "e"]) == [0.1, 0.2, 0.3, 0.4, 0.5]
    calls = mock_post.call_args_list
    assert [len(call.kwargs["json"]["texts"]) for call in calls] == [2, 2, 1]
    assert [call.kwargs["json"]["texts"] for call in calls] == [["a", "b"], ["c", "d"], ["e"]]
    for call in calls:
        assert call.kwargs["timeout"] == 7.5
        assert call.kwargs["url"] == "http://tei.local/rerank"
        assert call.kwargs["headers"] == {
            "Content-Type": "application/json",
            "Authorization": "Bearer override",
            "X-Test": "yes",
        }


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        None,
        "invalid",
        [None],
        [{"score": 0.4}],
        [{"index": -1, "score": 0.4}],
        [{"index": True, "score": 0.4}],
        [{"index": 0.0, "score": 0.4}],
        [{"index": "0", "score": 0.4}],
        [{"index": 0}],
        [{"index": 0, "score": "invalid"}],
        [{"index": 0, "score": float("nan")}],
        [{"index": 0, "score": float("inf")}],
        [{"index": 0, "score": 0.4}, {"index": 0, "score": 0.5}],
    ],
)
@patch("openviking.models.rerank.tei_rerank.requests.post")
def test_malformed_response_falls_back(mock_post, payload):
    mock_post.return_value.json.return_value = payload
    assert TEIRerankClient("http://tei.local").rerank_batch("q", ["a"]) is None


@pytest.mark.parametrize(
    "error", [requests.Timeout("timeout"), requests.ConnectionError("connection")]
)
@patch("openviking.models.rerank.tei_rerank.requests.post")
def test_network_errors_fall_back(mock_post, error):
    mock_post.side_effect = error
    assert TEIRerankClient("http://tei.local").rerank_batch("q", ["a"]) is None


@patch("openviking.models.rerank.tei_rerank.requests.post")
def test_http_error_falls_back(mock_post):
    mock_post.return_value.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
    assert TEIRerankClient("http://tei.local").rerank_batch("q", ["a"]) is None


@patch("openviking.models.rerank.tei_rerank.requests.post")
def test_invalid_json_falls_back(mock_post):
    mock_post.return_value.json.side_effect = ValueError("invalid JSON")
    assert TEIRerankClient("http://tei.local").rerank_batch("q", ["a"]) is None


@patch("openviking.models.rerank.tei_rerank.requests.post")
def test_failed_second_chunk_discards_partial_result(mock_post):
    first = MagicMock()
    first.json.return_value = [{"index": 0, "score": 0.5}]
    mock_post.side_effect = [first, requests.Timeout("timeout")]
    assert (
        TEIRerankClient("http://tei.local", batch_size=1).rerank_batch("q", ["a", "b", "c"]) is None
    )
    assert mock_post.call_count == 2


@patch("openviking.models.rerank.tei_rerank.requests.post")
def test_empty_documents_do_not_send_request(mock_post):
    assert TEIRerankClient("http://tei.local").rerank_batch("q", []) == []
    mock_post.assert_not_called()


@pytest.mark.parametrize("batch_size", [0, -1])
def test_config_rejects_invalid_batch_size(batch_size):
    with pytest.raises(ValidationError):
        RerankConfig(provider="tei", api_base="http://tei.local", batch_size=batch_size)
    with pytest.raises(ValueError):
        TEIRerankClient("http://tei.local", batch_size=batch_size)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, None),
        ({"api_key": "key"}, "cohere"),
        ({"ak": "ak", "sk": "sk", "api_base": "http://tei.local"}, "vikingdb"),
        ({"api_key": "key", "api_base": "https://api.typesafe.ai"}, "jev"),
        ({"provider": "TEI", "api_key": "key", "api_base": "http://tei.local"}, "tei"),
    ],
)
def test_auto_detection_preserves_provider_precedence(kwargs, expected):
    assert RerankConfig(**kwargs)._effective_provider() == expected


def test_factory_without_config_is_unavailable():
    assert TEIRerankClient.from_config(None) is None
    assert RerankClient.from_config(RerankConfig()) is None
