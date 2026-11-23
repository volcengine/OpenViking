# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def test_minimax_embedding_sends_query_and_document_types(monkeypatch):
    calls = []
    vector = [0.1, 0.2]

    def send(session, request, **kwargs):
        calls.append(
            (
                parse_qs(urlparse(request.url).query),
                request.headers["Authorization"],
                json.loads(request.body),
            )
        )
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(
            {"base_resp": {"status_code": 0}, "vectors": [vector]}
        ).encode()
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    config = EmbeddingConfig.model_validate(
        {
            "dense": {
                "provider": "minimax",
                "model": "embo-01",
                "api_key": "test-key",
                "dimension": 2,
                "extra_headers": {"GroupId": "group-123"},
                "query_param": "query",
                "document_param": "db",
            },
        }
    )
    embedder = config.get_embedder()
    try:
        assert embedder.embed("question", is_query=True).dense_vector == vector
        assert embedder.embed("document").dense_vector == vector
    finally:
        embedder.close()

    assert calls == [
        (
            {"GroupId": ["group-123"]},
            "Bearer test-key",
            {"model": "embo-01", "type": "query", "texts": ["question"]},
        ),
        (
            {"GroupId": ["group-123"]},
            "Bearer test-key",
            {"model": "embo-01", "type": "db", "texts": ["document"]},
        ),
    ]


def test_minimax_provider_requires_api_key():
    with pytest.raises(ValueError, match="MiniMax provider requires 'api_key'"):
        EmbeddingModelConfig(provider="minimax", model="embo-01")
