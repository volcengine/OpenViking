# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAIRerankClient extra_headers support."""
from unittest.mock import Mock, patch
import pytest

from openviking_cli.utils.config.rerank_config import RerankConfig
from openviking.models.rerank.openai_rerank import OpenAIRerankClient


def test_openai_rerank_client_init_with_extra_headers():
    """Test that OpenAIRerankClient accepts and stores extra_headers."""
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4",
        extra_headers={"x-gw-apikey": "Bearer real-key"}
    )

    assert client.extra_headers == {"x-gw-apikey": "Bearer real-key"}


def test_openai_rerank_client_init_without_extra_headers():
    """Test that OpenAIRerankClient defaults to empty dict when extra_headers is None."""
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4",
        extra_headers=None
    )

    assert client.extra_headers == {}


def test_openai_rerank_from_config_with_extra_headers():
    """Test that from_config correctly extracts extra_headers from RerankConfig."""
    config = RerankConfig(
        model="gpt-4",
        api_key="test-key",
        api_base="https://api.example.com/v1",
        extra_headers={"x-custom": "value"}
    )

    client = OpenAIRerankClient.from_config(config)

    assert client.extra_headers == {"x-custom": "value"}


def test_openai_rerank_from_config_without_extra_headers():
    """Test that from_config handles None extra_headers correctly."""
    config = RerankConfig(
        model="gpt-4",
        api_key="test-key",
        api_base="https://api.example.com/v1"
    )

    client = OpenAIRerankClient.from_config(config)

    assert client.extra_headers == {}


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_rerank_batch_includes_extra_headers(mock_post):
    """Test that rerank_batch includes extra_headers in the API request."""
    # Setup mock response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.8}
        ]
    }
    mock_post.return_value = mock_response

    # Create client with extra_headers
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4",
        extra_headers={
            "x-gw-apikey": "Bearer real-key",
            "X-Custom-Header": "custom-value"
        }
    )

    # Call rerank_batch
    result = client.rerank_batch(
        query="test query",
        documents=["doc1", "doc2"]
    )

    # Verify the request included extra_headers
    assert mock_post.called
    call_kwargs = mock_post.call_args.kwargs
    headers = call_kwargs["headers"]

    # Check default headers
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"

    # Check extra headers are merged
    assert headers["x-gw-apikey"] == "Bearer real-key"
    assert headers["X-Custom-Header"] == "custom-value"


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_rerank_batch_without_extra_headers(mock_post):
    """Test that rerank_batch works correctly when no extra_headers provided."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"index": 0, "relevance_score": 0.9}
        ]
    }
    mock_post.return_value = mock_response

    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4"
    )

    result = client.rerank_batch(
        query="test query",
        documents=["doc1"]
    )

    assert mock_post.called
    call_kwargs = mock_post.call_args.kwargs
    headers = call_kwargs["headers"]

    # Should only have default headers
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"
    # No extra headers
    assert "x-gw-apikey" not in headers


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_extra_headers_can_override_defaults(mock_post):
    """Test that extra_headers can override default headers if needed."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": []}
    mock_post.return_value = mock_response

    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4",
        extra_headers={"Content-Type": "application/json; charset=utf-8"}
    )

    client.rerank_batch(query="test", documents=["doc"])

    call_kwargs = mock_post.call_args.kwargs
    headers = call_kwargs["headers"]

    # Extra header overrides default
    assert headers["Content-Type"] == "application/json; charset=utf-8"
