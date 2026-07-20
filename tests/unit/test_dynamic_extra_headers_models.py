# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Request-scoped extra header tests for embedding and rerank providers."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openviking.models.embedder.litellm_embedders import LiteLLMDenseEmbedder
from openviking.models.embedder.minimax_embedders import MinimaxDenseEmbedder
from openviking.models.embedder.openai_embedders import OpenAIDenseEmbedder
from openviking.models.embedder.volcengine_embedders import VolcengineDenseEmbedder
from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient
from openviking.models.rerank.openai_rerank import OpenAIRerankClient
from openviking.utils.request_headers import bind_request_headers
from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

DYNAMIC_HEADERS = {
    "X-Static": "fixed",
    "X-Tenant": "@request.header.X-Tenant-Source",
}


@pytest.mark.parametrize(
    ("provider", "client_path"),
    [("openai", "openai.OpenAI"), ("azure", "openai.AzureOpenAI")],
)
def test_openai_embedding_keeps_only_static_client_headers_and_resolves_per_call(
    provider, client_path
):
    configured = dict(DYNAMIC_HEADERS)
    client = MagicMock()
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])], usage=None
    )

    with patch(client_path, return_value=client) as client_class:
        embedder = OpenAIDenseEmbedder(
            model_name="embedding-model",
            api_key="test-key",
            api_base="https://example.test/v1",
            dimension=2,
            provider=provider,
            extra_headers=configured,
        )

        assert client_class.call_args.kwargs["default_headers"] == {"X-Static": "fixed"}
        with bind_request_headers({"X-Tenant-Source": "tenant-a"}):
            embedder.embed("first")
        with bind_request_headers({"X-Tenant-Source": "tenant-b"}):
            embedder.embed("second")

    calls = client.embeddings.create.call_args_list
    assert calls[0].kwargs["extra_headers"] == {"X-Tenant": "tenant-a"}
    assert calls[1].kwargs["extra_headers"] == {"X-Tenant": "tenant-b"}
    assert configured == DYNAMIC_HEADERS


def test_ollama_factory_forwards_extra_headers():
    client = MagicMock()
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])], usage=None
    )
    config = EmbeddingModelConfig(
        provider="ollama",
        model="nomic-embed-text",
        dimension=2,
        extra_headers=DYNAMIC_HEADERS,
    )

    with patch("openai.OpenAI", return_value=client) as client_class:
        embedder = EmbeddingConfig(dense=config)._create_embedder("ollama", "dense", config)
        with bind_request_headers({"X-Tenant-Source": "ollama-tenant"}):
            embedder.embed("text")

    assert client_class.call_args.kwargs["default_headers"] == {"X-Static": "fixed"}
    assert client.embeddings.create.call_args.kwargs["extra_headers"] == {
        "X-Tenant": "ollama-tenant"
    }


def test_litellm_embedding_resolves_headers_for_each_send():
    configured = dict(DYNAMIC_HEADERS)
    response = SimpleNamespace(data=[{"embedding": [0.1, 0.2]}], usage=None)
    embedder = LiteLLMDenseEmbedder(
        model_name="openai/embedding-model",
        dimension=2,
        extra_headers=configured,
    )

    with patch(
        "openviking.models.embedder.litellm_embedders.litellm.embedding",
        return_value=response,
    ) as call:
        with bind_request_headers({"X-Tenant-Source": "tenant-a"}):
            embedder.embed("first")
        with bind_request_headers({"X-Tenant-Source": "tenant-b"}):
            embedder.embed("second")

    assert call.call_args_list[0].kwargs["extra_headers"] == {
        "X-Static": "fixed",
        "X-Tenant": "tenant-a",
    }
    assert call.call_args_list[1].kwargs["extra_headers"]["X-Tenant"] == "tenant-b"
    assert configured == DYNAMIC_HEADERS


def test_volcengine_embedding_resolves_headers_for_each_send():
    configured = dict(DYNAMIC_HEADERS)
    client = MagicMock()
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])], usage=None
    )

    with patch("volcenginesdkarkruntime.Ark", return_value=client):
        embedder = VolcengineDenseEmbedder(
            model_name="embedding-model",
            api_key="test-key",
            dimension=2,
            input_type="text",
            extra_headers=configured,
        )
        with bind_request_headers({"X-Tenant-Source": "tenant-a"}):
            embedder.embed("first")
        with bind_request_headers({"X-Tenant-Source": "tenant-b"}):
            embedder.embed("second")

    calls = client.embeddings.create.call_args_list
    assert calls[0].kwargs["extra_headers"]["X-Tenant"] == "tenant-a"
    assert calls[1].kwargs["extra_headers"]["X-Tenant"] == "tenant-b"
    assert "X-Client-Request-Id" in calls[0].kwargs["extra_headers"]
    assert configured == DYNAMIC_HEADERS


def test_minimax_resolves_dynamic_group_id_as_query_parameter():
    configured = {
        "Authorization": "@request.header.Authorization",
        "GroupId": "@request.header.X-Group-Id",
        "X-Tenant": "@request.header.X-Tenant-Source",
        "X-Static": "fixed",
    }
    response = MagicMock()
    response.json.return_value = {"base_resp": {"status_code": 0}, "vectors": [[0.1, 0.2]]}
    embedder = MinimaxDenseEmbedder(api_key="test-key", dimension=2, extra_headers=configured)
    embedder.session.post = MagicMock(return_value=response)

    with bind_request_headers(
        {
            "Authorization": "Bearer dynamic-minimax",
            "X-Group-Id": "group-a",
            "X-Tenant-Source": "tenant-a",
        }
    ):
        embedder.embed("text")

    request = embedder.session.post.call_args.kwargs
    assert request["params"] == {"GroupId": "group-a"}
    assert request["headers"]["Authorization"] == "Bearer dynamic-minimax"
    assert request["headers"]["X-Tenant"] == "tenant-a"
    assert request["headers"]["X-Static"] == "fixed"
    assert "GroupId" not in request["headers"]
    assert configured["GroupId"] == "@request.header.X-Group-Id"


def test_minimax_falls_back_to_api_key_when_dynamic_authorization_is_missing():
    response = MagicMock()
    response.json.return_value = {"base_resp": {"status_code": 0}, "vectors": [[0.1, 0.2]]}
    embedder = MinimaxDenseEmbedder(
        api_key="fallback-key",
        dimension=2,
        extra_headers={"Authorization": "@request.header.Authorization"},
    )
    embedder.session.post = MagicMock(return_value=response)

    with bind_request_headers({"X-Unrelated": "value"}):
        embedder.embed("text")

    request = embedder.session.post.call_args.kwargs
    assert request["headers"]["Authorization"] == "Bearer fallback-key"


def test_openai_rerank_resolves_headers_for_each_send():
    response = MagicMock()
    response.json.return_value = {"results": [{"index": 0, "relevance_score": 0.9}]}
    client = OpenAIRerankClient(
        api_key="test-key",
        api_base="https://example.test/rerank",
        model_name="rerank-model",
        extra_headers=DYNAMIC_HEADERS,
    )

    with patch(
        "openviking.models.rerank.openai_rerank.requests.post", return_value=response
    ) as post:
        with bind_request_headers({"X-Tenant-Source": "tenant-a"}):
            client.rerank_batch("query", ["document"])
        with bind_request_headers({"X-Tenant-Source": "tenant-b"}):
            client.rerank_batch("query", ["document"])

    assert post.call_args_list[0].kwargs["headers"]["X-Tenant"] == "tenant-a"
    assert post.call_args_list[1].kwargs["headers"]["X-Tenant"] == "tenant-b"


def test_litellm_rerank_constructor_from_config_and_call_forward_extra_headers():
    configured = dict(DYNAMIC_HEADERS)
    config = SimpleNamespace(
        api_key="test-key",
        api_base="https://example.test/v1",
        model="rerank-model",
        extra_headers=configured,
        is_available=lambda: True,
    )
    client = LiteLLMRerankClient.from_config(config)
    response = MagicMock()
    response.model_dump.return_value = {}
    response.results = [SimpleNamespace(index=0, relevance_score=0.9)]

    with patch("litellm.rerank", return_value=response) as rerank:
        with bind_request_headers({"X-Tenant-Source": "tenant-a"}):
            assert client.rerank_batch("query", ["document"]) == [0.9]

    assert client.extra_headers == configured
    assert rerank.call_args.kwargs["headers"] == {
        "X-Static": "fixed",
        "X-Tenant": "tenant-a",
    }
    assert configured == DYNAMIC_HEADERS
