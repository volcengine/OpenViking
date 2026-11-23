# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ollama embedding configuration as used by the OpenAI-compatible endpoint."""

import json

import httpx
import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingConfig

MODEL = "nomic-embed-text"


@pytest.fixture
def embedding_requests(monkeypatch):
    requests = []

    def send(client, request, **kwargs):
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "object": "list",
                "model": MODEL,
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    return requests


def test_ollama_embedding_works_without_credentials_or_endpoint(embedding_requests):
    config = EmbeddingConfig.model_validate(
        {"dense": {"provider": "ollama", "model": MODEL, "dimension": 2}}
    )
    embedder = config.get_embedder()
    try:
        assert embedder.embed("short note").dense_vector == [0.1, 0.2]
    finally:
        embedder.close()

    [request] = embedding_requests
    assert str(request.url) == "http://localhost:11434/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer no-key"
    body = json.loads(request.content)
    assert (body["model"], body["input"]) == (MODEL, "short note")


def test_ollama_embedding_uses_configured_endpoint_credentials_and_input_limit(embedding_requests):
    config = EmbeddingConfig.model_validate(
        {
            "max_input_tokens": 100,
            "dense": {
                "provider": "ollama",
                "model": MODEL,
                "dimension": 2,
                "api_base": "https://gateway.example.com/v1",
                "api_key": "gateway-key",
            },
        }
    )
    embedder = config.get_embedder()
    try:
        assert embedder.embed("中文" * 80).dense_vector == [0.1, 0.2]
    finally:
        embedder.close()

    [request] = embedding_requests
    assert str(request.url) == "https://gateway.example.com/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer gateway-key"
    assert json.loads(request.content)["input"] == "中文" * 50 + "\n...(truncated for embedding)"
