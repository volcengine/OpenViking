# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Forwarding of RerankConfig.max_tokens_per_doc to providers that accept it.

Cohere v2, LiteLLM, and OpenAI-compatible (Cohere-schema) endpoints receive a
per-document token-truncation limit; VikingDB/doubao does not (it has no such
field). The parameter is sent only when configured (> 0), so default requests
are byte-identical to before.
"""

from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from openviking.models.rerank.cohere_rerank import CohereRerankClient
from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient
from openviking.models.rerank.openai_rerank import OpenAIRerankClient
from openviking.models.rerank.volcengine_rerank import RerankClient as VikingDBRerankClient
from openviking_cli.utils.config.rerank_config import RerankConfig

# --- Cohere v2 ------------------------------------------------------------


def _cohere_client(max_tokens_per_doc: int) -> CohereRerankClient:
    client = CohereRerankClient(api_key="k", max_tokens_per_doc=max_tokens_per_doc)
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.8}]
    }
    client._client = Mock()
    client._client.post.return_value = resp
    return client


def test_cohere_forwards_max_tokens_per_doc_when_set():
    client = _cohere_client(96)
    client.rerank_batch("q", ["a", "b"])
    body = client._client.post.call_args.kwargs["json"]
    assert body["max_tokens_per_doc"] == 96


def test_cohere_omits_max_tokens_per_doc_when_zero():
    client = _cohere_client(0)
    client.rerank_batch("q", ["a", "b"])
    body = client._client.post.call_args.kwargs["json"]
    assert "max_tokens_per_doc" not in body


def test_cohere_from_config_passes_max_tokens_per_doc():
    config = RerankConfig(api_key="k", max_tokens_per_doc=77)
    client = CohereRerankClient.from_config(config)
    assert client.max_tokens_per_doc == 77


# --- OpenAI-compatible ----------------------------------------------------


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_openai_forwards_max_tokens_per_doc_when_set(mock_post):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"results": [{"index": 0, "relevance_score": 0.9}]}
    mock_post.return_value = resp

    client = OpenAIRerankClient(
        api_key="k", api_base="https://x/v1/rerank", model_name="m", max_tokens_per_doc=64
    )
    client.rerank_batch("q", ["a"])

    body = mock_post.call_args.kwargs["json"]
    assert body["max_tokens_per_doc"] == 64


@patch("openviking.models.rerank.openai_rerank.requests.post")
def test_openai_omits_max_tokens_per_doc_when_zero(mock_post):
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"results": [{"index": 0, "relevance_score": 0.9}]}
    mock_post.return_value = resp

    client = OpenAIRerankClient(api_key="k", api_base="https://x/v1/rerank", model_name="m")
    client.rerank_batch("q", ["a"])

    body = mock_post.call_args.kwargs["json"]
    assert "max_tokens_per_doc" not in body


def test_openai_from_config_passes_max_tokens_per_doc():
    config = RerankConfig(
        api_key="k", api_base="https://x/v1/rerank", model="m", max_tokens_per_doc=55
    )
    client = OpenAIRerankClient.from_config(config)
    assert client.max_tokens_per_doc == 55


# --- LiteLLM --------------------------------------------------------------


def _litellm_response() -> Mock:
    item = Mock(index=0, relevance_score=0.9)
    resp = Mock()
    resp.results = [item]
    resp.model_dump.return_value = {}
    return resp


@patch("litellm.rerank")
def test_litellm_forwards_max_tokens_per_doc_when_set(mock_rerank):
    mock_rerank.return_value = _litellm_response()

    client = LiteLLMRerankClient(
        api_key="k", api_base="https://x", model_name="m", max_tokens_per_doc=128
    )
    client.rerank_batch("q", ["a"])

    assert mock_rerank.call_args.kwargs["max_tokens_per_doc"] == 128


@patch("litellm.rerank")
def test_litellm_omits_max_tokens_per_doc_when_zero(mock_rerank):
    mock_rerank.return_value = _litellm_response()

    client = LiteLLMRerankClient(api_key="k", api_base="https://x", model_name="m")
    client.rerank_batch("q", ["a"])

    assert "max_tokens_per_doc" not in mock_rerank.call_args.kwargs


def test_litellm_from_config_passes_max_tokens_per_doc():
    config = RerankConfig(provider="litellm", model="m", max_tokens_per_doc=33)
    client = LiteLLMRerankClient.from_config(config)
    assert client.max_tokens_per_doc == 33


# --- Config validation ----------------------------------------------------


def test_config_default_max_tokens_per_doc_is_zero():
    assert RerankConfig(ak="ak", sk="sk").max_tokens_per_doc == 0


def test_config_rejects_negative_max_tokens_per_doc():
    with pytest.raises(ValidationError):
        RerankConfig(ak="ak", sk="sk", max_tokens_per_doc=-1)


def test_config_rejects_non_int_max_tokens_per_doc_under_strict():
    with pytest.raises(ValidationError):
        RerankConfig(ak="ak", sk="sk", max_tokens_per_doc="5")


# --- VikingDB is intentionally not wired ----------------------------------


def test_vikingdb_ignores_max_tokens_per_doc():
    # VikingDB/doubao has no token-truncation field; setting max_tokens_per_doc
    # must not affect it — the value is simply not carried into the client.
    config = RerankConfig(ak="ak", sk="sk", max_tokens_per_doc=100)
    client = VikingDBRerankClient.from_config(config)

    assert isinstance(client, VikingDBRerankClient)
    assert not hasattr(client, "max_tokens_per_doc")
