# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.models.rerank import FallbackRerankClient, RerankClient
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.utils.config import RerankConfig


class FakeRerankClient:
    def __init__(self, provider: str, result):
        self.provider = provider
        self.model_name = provider
        self.result = result
        self.calls = []

    def rerank_batch(self, query, documents):
        self.calls.append((query, list(documents)))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_primary_success_does_not_call_fallback():
    primary = FakeRerankClient("primary", [0.9, 0.2])
    fallback = FakeRerankClient("fallback", [0.1, 0.8])
    client = FallbackRerankClient(primary, fallback)

    assert client.rerank_batch("query", ["a", "b"]) == [0.9, 0.2]
    assert fallback.calls == []


@pytest.mark.parametrize(
    "primary_result",
    [None, [], [0.9], RuntimeError("provider unavailable")],
)
def test_unusable_primary_result_uses_fallback(primary_result):
    primary = FakeRerankClient("primary", primary_result)
    fallback = FakeRerankClient("fallback", [0.8, 0.3])
    client = FallbackRerankClient(primary, fallback)

    assert client.rerank_batch("query", ["a", "b"]) == [0.8, 0.3]
    assert primary.calls == [("query", ["a", "b"])]
    assert fallback.calls == [("query", ["a", "b"])]


def test_fallback_applies_its_own_input_budget():
    document = "summary-start " + ("filler " * 600) + "relevant-tail"
    primary = FakeRerankClient("primary", None)
    fallback = FakeRerankClient("fallback", [0.8])
    client = FallbackRerankClient(primary, fallback, fallback_max_input_tokens=128)

    assert client.rerank_batch("query", [document]) == [0.8]
    fallback_query, fallback_documents = fallback.calls[0]
    bounded_document = fallback_documents[0]
    assert estimate_text_tokens(fallback_query) + estimate_text_tokens(bounded_document) <= 128
    assert "summary-start" in bounded_document
    assert "relevant-tail" in bounded_document


def test_config_factory_wraps_primary_with_fallback():
    config = RerankConfig(
        provider="openai",
        api_key="primary-key",
        api_base="https://primary.example/v1/rerank",
        model="primary-model",
        fallback={
            "provider": "openai",
            "api_key": "fallback-key",
            "api_base": "http://127.0.0.1:8731/v1/rerank",
            "model": "fallback-model",
            "max_input_tokens": 2600,
            "timeout": 90,
        },
    )

    client = RerankClient.from_config(config)

    assert isinstance(client, FallbackRerankClient)
    assert client.primary.provider == "openai"
    assert client.fallback.provider == "openai"
    assert client.fallback_max_input_tokens == 2600
    client.close()
