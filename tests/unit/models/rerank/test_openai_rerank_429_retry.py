# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for OpenAIRerankClient 429 retry behavior."""

from unittest.mock import Mock, patch

from openviking.models.rerank.openai_rerank import OpenAIRerankClient


def _client() -> OpenAIRerankClient:
    return OpenAIRerankClient(
        api_key="test-key",
        api_base="https://api.example.com/v1",
        model_name="gpt-4",
    )


@patch("openviking.models.rerank.openai_rerank.time.sleep")
@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_rerank_batch_retries_after_429_then_succeeds(mock_post, mock_sleep):
    """A single 429 with Retry-After should be retried once and then succeed."""
    rate_limited = Mock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "1.5"}

    ok_response = Mock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.json.return_value = {
        "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.4}]
    }

    mock_post.side_effect = [rate_limited, ok_response]

    client = _client()
    scores = client.rerank_batch(query="q", documents=["doc1", "doc2"])

    assert scores == [0.9, 0.4]
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()
    assert mock_sleep.call_args.args[0] == 1.5


@patch("openviking.models.rerank.openai_rerank.time.sleep")
@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_rerank_batch_gives_up_after_repeated_429(mock_post, mock_sleep):
    """Persistent 429s should exhaust retries and return None (caller falls back)."""
    rate_limited = Mock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "0.2"}
    rate_limited.raise_for_status.side_effect = Exception("429 Too Many Requests")

    mock_post.side_effect = [rate_limited, rate_limited]

    client = _client()
    scores = client.rerank_batch(query="q", documents=["doc1"])

    assert scores is None
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()


@patch("openviking.models.rerank.openai_rerank.time.sleep")
@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_rerank_batch_no_retry_without_429(mock_post, mock_sleep):
    """A first-try success should not trigger any retry/sleep."""
    ok_response = Mock()
    ok_response.status_code = 200
    ok_response.headers = {}
    ok_response.json.return_value = {"results": [{"index": 0, "relevance_score": 0.9}]}
    mock_post.return_value = ok_response

    client = _client()
    scores = client.rerank_batch(query="q", documents=["doc1"])

    assert scores == [0.9]
    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()
