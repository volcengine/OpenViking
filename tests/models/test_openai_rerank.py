# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenAI-compatible reranking through the configured HTTP endpoint."""

from unittest.mock import patch

import pytest

from openviking.models.rerank import OpenAIRerankClient, RerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_native_full_flow(post):
    endpoint = "https://gateway.example/api/v1/services/rerank/text-rerank"
    config = RerankConfig(
        provider="openai",
        api_base=endpoint,
        api_key="test-key",
        model="gte-rerank-v2",
        extra_headers={
            "X-Gateway-Key": "gateway-key",
            "Content-Type": "application/json; charset=utf-8",
        },
        timeout=120.0,
    )
    client = RerankClient.from_config(config)
    assert isinstance(client, OpenAIRerankClient)
    post.return_value.json.return_value = {
        "output": {
            "results": [
                {"index": 1, "relevance_scores": 0.12},
                {"index": 0, "relevance_score": 0.95},
            ]
        }
    }

    assert client.rerank_batch("hello", ["doc1", "doc2"]) == [0.95, 0.12]
    post.assert_called_once_with(
        url=endpoint,
        json={
            "model": "gte-rerank-v2",
            "input": {"query": "hello", "documents": ["doc1", "doc2"]},
            "parameters": {"return_documents": False},
        },
        headers={
            "Authorization": "Bearer test-key",
            "Content-Type": "application/json; charset=utf-8",
            "X-Gateway-Key": "gateway-key",
        },
        timeout=120.0,
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
        "https://gateway.example/v1/rerank",
    ],
    ids=["dashscope-compatible", "generic-compatible"],
)
@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_compatible_api_full_flow(post, endpoint):
    client = RerankClient.from_config(RerankConfig(api_base=endpoint, api_key="test-key"))
    assert isinstance(client, OpenAIRerankClient)
    post.return_value.json.return_value = {"results": [{"index": 1, "relevance_score": 0.42}]}

    assert client.rerank_batch("hello", ["doc1", "doc2"]) == [0.0, 0.42]
    post.assert_called_once_with(
        url=endpoint,
        json={"model": "qwen3-rerank", "query": "hello", "documents": ["doc1", "doc2"], "top_n": 2},
        headers={"Authorization": "Bearer test-key", "Content-Type": "application/json"},
        timeout=30.0,
    )


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_empty_documents(post):
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://gateway.example/v1/rerank",
        model_name="qwen3-rerank",
    )

    assert client.rerank_batch("hello", []) == []
    post.assert_not_called()
