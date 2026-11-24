# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Wire contracts for the native DashScope and OpenAI-compatible rerank endpoints."""

import json
from unittest.mock import Mock

import pytest
import requests

from openviking.models.rerank import RerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig

NATIVE_URL = "https://gateway.example.com/api/v1/services/rerank/text-rerank"
COMPATIBLE_URL = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
DOCUMENTS = ["doc A", "doc B", "doc C"]


@pytest.fixture
def provider_post(monkeypatch):
    post = Mock()
    monkeypatch.setattr("openviking.models.rerank.openai_rerank.requests.post", post)
    return post


def provider_response(body: dict, status: int = 200) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def test_native_rerank_uses_nested_protocol_and_aligns_sparse_scores(provider_post):
    provider_post.return_value = provider_response(
        {
            "output": {
                "results": [
                    {"index": 2, "relevance_score": 0.7},
                    {"index": 0, "relevance_score": 0.9},
                ]
            }
        }
    )
    reranker = RerankClient.from_config(
        RerankConfig(
            provider="openai",
            api_key="test-key",
            api_base=NATIVE_URL,
            model="gte-rerank-v2",
            timeout=120,
            extra_headers={
                "X-Gateway-Key": "gateway-key",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
    )
    assert reranker is not None
    assert reranker.rerank_batch("search", DOCUMENTS) == [0.9, 0.0, 0.7]
    provider_post.assert_called_once_with(
        url=NATIVE_URL,
        headers={
            "Authorization": "Bearer test-key",
            "Content-Type": "application/json; charset=utf-8",
            "X-Gateway-Key": "gateway-key",
        },
        json={
            "model": "gte-rerank-v2",
            "input": {"query": "search", "documents": DOCUMENTS},
            "parameters": {"return_documents": False},
        },
        timeout=120,
    )


def test_compatible_rerank_uses_flat_protocol_and_default_request_options(provider_post):
    provider_post.return_value = provider_response(
        {"results": [{"index": 1, "relevance_score": 0.6}, {"index": 0, "relevance_score": 0.8}]}
    )
    reranker = RerankClient.from_config(RerankConfig(api_key="test-key", api_base=COMPATIBLE_URL))
    assert reranker is not None
    assert reranker.rerank_batch("search", DOCUMENTS[:2]) == [0.8, 0.6]
    provider_post.assert_called_once_with(
        url=COMPATIBLE_URL,
        headers={"Authorization": "Bearer test-key", "Content-Type": "application/json"},
        json={"model": "qwen3-rerank", "query": "search", "documents": DOCUMENTS[:2], "top_n": 2},
        timeout=30,
    )


def test_native_rerank_accepts_legacy_plural_score_field(provider_post):
    provider_post.return_value = provider_response(
        {"output": {"results": [{"index": 0, "relevance_scores": 0.4}]}}
    )
    reranker = RerankClient.from_config(RerankConfig(api_key="test-key", api_base=NATIVE_URL))
    assert reranker is not None
    assert reranker.rerank_batch("search", DOCUMENTS[:1]) == [0.4]


def test_empty_documents_do_not_call_rerank_provider(provider_post):
    reranker = RerankClient.from_config(RerankConfig(api_key="test-key", api_base=COMPATIBLE_URL))
    assert reranker is not None
    assert reranker.rerank_batch("search", []) == []
    provider_post.assert_not_called()


@pytest.mark.parametrize(
    ("url", "status", "body"),
    [
        pytest.param(NATIVE_URL, 200, {"output": []}, id="invalid-native-envelope"),
        pytest.param(COMPATIBLE_URL, 200, {}, id="missing-flat-results"),
        pytest.param(
            COMPATIBLE_URL, 200, {"results": [{"relevance_score": 0.9}]}, id="missing-index"
        ),
        pytest.param(
            COMPATIBLE_URL, 200, {"results": [{"index": 3, "relevance_score": 0.9}]}, id="bad-index"
        ),
        pytest.param(
            COMPATIBLE_URL,
            503,
            {"results": [{"index": 0, "relevance_score": 0.9}]},
            id="http-failure",
        ),
    ],
)
def test_invalid_provider_response_signals_vector_score_fallback(provider_post, url, status, body):
    provider_post.return_value = provider_response(body, status)
    reranker = RerankClient.from_config(RerankConfig(api_key="test-key", api_base=url))
    assert reranker is not None
    assert reranker.rerank_batch("search", DOCUMENTS) is None
    provider_post.assert_called_once()
