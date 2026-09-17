# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAIRerankClient configurable HTTP timeout support."""

from unittest.mock import Mock, patch

from openviking.models.rerank.openai_rerank import OpenAIRerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig


def test_openai_rerank_client_default_timeout():
    """Client defaults to a 30s timeout when none is provided."""
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="qwen3-rerank",
    )

    assert client.timeout == 30.0


def test_openai_rerank_client_custom_timeout():
    """Client stores an explicitly provided timeout."""
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="qwen3-rerank",
        timeout=120.0,
    )

    assert client.timeout == 120.0


def test_rerank_config_default_timeout():
    """RerankConfig defaults timeout to 30s for backwards compatibility."""
    config = RerankConfig(
        model="qwen3-rerank",
        api_key="test-key",
        api_base="https://api.example.com/v1",
    )

    assert config.timeout == 30.0


def test_openai_rerank_from_config_with_custom_timeout():
    """from_config threads a custom timeout through to the client."""
    config = RerankConfig(
        model="qwen3-rerank",
        api_key="test-key",
        api_base="https://api.example.com/v1",
        timeout=120.0,
    )

    client = OpenAIRerankClient.from_config(config)

    assert client.timeout == 120.0


def test_openai_rerank_from_config_default_timeout():
    """from_config preserves the 30s default when timeout is unset."""
    config = RerankConfig(
        model="qwen3-rerank",
        api_key="test-key",
        api_base="https://api.example.com/v1",
    )

    client = OpenAIRerankClient.from_config(config)

    assert client.timeout == 30.0


def test_openai_rerank_from_config_can_disable_environment_proxy_lookup():
    """from_config passes trust_env through to the client."""
    config = RerankConfig(
        model="qwen3-rerank",
        api_key="test-key",
        api_base="http://rerank.internal:8081/v1/rerank",
        trust_env=False,
    )

    client = OpenAIRerankClient.from_config(config)

    assert client is not None
    assert client.trust_env is False


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_rerank_batch_uses_configured_timeout(mock_post):
    """rerank_batch passes the configured timeout to requests.post."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]
    }
    mock_post.return_value = mock_response

    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="qwen3-rerank",
        timeout=120.0,
    )

    client.rerank_batch(query="test query", documents=["doc1", "doc2"])

    assert mock_post.called
    assert mock_post.call_args.kwargs["timeout"] == 120.0


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_rerank_batch_uses_default_timeout(mock_post):
    """rerank_batch falls back to the 30s default when no timeout is configured."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [{"index": 0, "relevance_score": 0.9}]}
    mock_post.return_value = mock_response

    client = OpenAIRerankClient(
        api_key="test-key", api_base="https://api.example.com/v1", model_name="qwen3-rerank"
    )

    client.rerank_batch(query="test query", documents=["doc1"])

    assert mock_post.called
    assert mock_post.call_args.kwargs["timeout"] == 30.0


@patch("openviking.models.rerank.openai_rerank.requests.Session")
def test_rerank_batch_can_disable_environment_proxy_lookup(mock_session):
    """A LAN endpoint can opt out of proxy environment discovery."""
    response = Mock()
    response.json.return_value = {"results": [{"index": 0, "relevance_score": 0.9}]}
    session = mock_session.return_value
    session.post.return_value = response

    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="http://rerank.internal:8081/v1/rerank",
        model_name="qwen3-rerank",
        trust_env=False,
    )

    assert client.rerank_batch(query="test query", documents=["doc1"]) == [0.9]
    assert session.trust_env is False
    session.post.assert_called_once()
    session.close.assert_called_once()
