# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ollama embedding via its OpenAI-compatible endpoint."""

import json

import httpx
import pytest

from openviking_cli.utils.config.embedding_config import EmbeddingConfig


@pytest.mark.parametrize(
    ("settings", "endpoint", "authorization"),
    [
        ({}, "http://localhost:11434/v1/embeddings", "Bearer no-key"),
        (
            {"api_base": "http://ollama.example/v1", "api_key": "gateway-key"},
            "http://ollama.example/v1/embeddings",
            "Bearer gateway-key",
        ),
    ],
    ids=["local-default", "authenticated-gateway"],
)
def test_ollama_embedding_request(monkeypatch, settings, endpoint, authorization):
    def respond(_transport, request):
        assert request.method == "POST"
        assert str(request.url) == endpoint
        assert request.headers["Authorization"] == authorization
        payload = json.loads(request.content)
        assert payload["model"] == "nomic-embed-text"
        assert payload["input"] == "offline document"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    config = EmbeddingConfig.model_validate(
        {"dense": {"provider": "ollama", "model": "nomic-embed-text", "dimension": 3, **settings}}
    )
    embedder = config.get_embedder()
    try:
        assert embedder.embed("offline document").dense_vector == [0.1, 0.2, 0.3]
    finally:
        embedder.close()
