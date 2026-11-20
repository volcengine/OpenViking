# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import Mock

import pytest
import requests

from openviking.models.model_error import ModelError, ModelErrorKind
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig


def test_minimax_embeddings_distinguish_documents_and_queries(monkeypatch):
    calls = []

    def post(_session, url, **kwargs):
        assert url == "https://api.minimax.chat/v1/embeddings"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert "GroupId" not in kwargs["headers"]
        assert kwargs["params"] == {"GroupId": "group-123"}
        calls.append(kwargs["json"])
        return Mock(
            json=lambda: {
                "base_resp": {"status_code": 0},
                "vectors": [[0.1, 0.2] if kwargs["json"]["type"] == "db" else [0.3, 0.4]],
            }
        )

    monkeypatch.setattr(requests.Session, "post", post)
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="minimax",
            model="embo-01",
            api_key="test-key",
            dimension=2,
            extra_headers={"GroupId": "group-123"},
        )
    )
    embedder = config.get_embedder()
    try:
        assert embedder.embed("document").dense_vector == [0.1, 0.2]
        assert embedder.embed("query", is_query=True).dense_vector == [0.3, 0.4]
    finally:
        embedder.close()
    assert calls == [
        {"model": "embo-01", "type": "db", "texts": ["document"]},
        {"model": "embo-01", "type": "query", "texts": ["query"]},
    ]


def test_minimax_provider_requires_api_key():
    with pytest.raises(ValueError, match="MiniMax provider requires 'api_key'"):
        EmbeddingModelConfig(provider="minimax", model="embo-01")


def test_minimax_quota_error_in_successful_http_response(monkeypatch):
    post = Mock(
        return_value=Mock(
            json=lambda: {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}
        )
    )
    monkeypatch.setattr(requests.Session, "post", post)
    config = EmbeddingConfig(
        dense=EmbeddingModelConfig(
            provider="minimax", model="embo-01", api_key="test-key", dimension=2
        )
    )
    embedder = config.get_embedder()
    try:
        with pytest.raises(ModelError) as caught:
            embedder.embed("document")
    finally:
        embedder.close()
    assert caught.value.kind is ModelErrorKind.QUOTA_EXHAUSTED
    assert caught.value.code == "1008"
    post.assert_called_once()
