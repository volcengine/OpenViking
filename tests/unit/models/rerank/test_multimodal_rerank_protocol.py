# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from unittest.mock import Mock

import pytest

from openviking.models.rerank.input import text_content
from openviking.models.rerank.openai_rerank import OpenAIRerankClient
from openviking.retrieve.rerank_scoring import rerank_scores
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.utils.config import RerankConfig

IMAGE = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cGljdHVyZQ=="}}]


def client(native=False, **kwargs):
    return OpenAIRerankClient(
        api_key="test-key",
        api_base="https://example.com/api/v1/services/rerank/text-rerank"
        if native
        else "https://example.com/rerank",
        model_name="visual-reranker",
        input_type="multimodal",
        **kwargs,
    )


@pytest.mark.parametrize("native", [False, True])
def test_multimodal_http_payload_and_score_alignment(monkeypatch, native):
    response = Mock()
    results = [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]
    response.json.return_value = (
        {"output": {"results": results}} if native else {"results": results}
    )
    post = Mock(return_value=response)
    monkeypatch.setattr("openviking.models.rerank.openai_rerank.requests.post", post)
    reranker = client(native, timeout=60, extra_headers={"X-Test": "value"})

    assert reranker.rerank_batch(IMAGE, ["text", IMAGE]) == [0.1, 0.9]
    body = post.call_args.kwargs["json"]
    payload = body["input"] if native else body
    expected = {"image": IMAGE[0]["image_url"]["url"]} if native else {"content": IMAGE}
    assert payload["query"] == expected
    assert payload["documents"] == ["text", expected]
    assert post.call_args.kwargs["timeout"] == 60
    assert post.call_args.kwargs["headers"]["X-Test"] == "value"
    assert reranker._estimate_tokens(IMAGE) == 1


def test_flat_protocol_preserves_mixed_and_multiple_image_query():
    query = [{"type": "text", "text": "red square"}, *IMAGE, *IMAGE]
    assert client()._build_request_body(query, [IMAGE])["query"] == {"content": query}


def test_native_protocol_rejects_unsupported_mixed_query_without_dropping_parts(monkeypatch):
    post = Mock()
    monkeypatch.setattr("openviking.models.rerank.openai_rerank.requests.post", post)
    assert client(True).rerank_batch([{"type": "text", "text": "red"}, *IMAGE], [IMAGE]) is None
    post.assert_not_called()


def test_multimodal_config_is_explicit_and_only_supported_by_openai():
    assert RerankConfig().input == "text"
    with pytest.raises(ValueError, match="Multimodal"):
        RerankConfig(provider="cohere", api_key="test", input="multimodal")
    config = RerankConfig(
        provider="openai", api_key="test", api_base="https://example.com/rerank", input="multimodal"
    )
    assert OpenAIRerankClient.from_config(config).supports_multimodal
    assert not OpenAIRerankClient.from_config(
        config.model_copy(update={"input": "text"})
    ).supports_multimodal


@pytest.mark.asyncio
async def test_truncation_preserves_images_and_filters_only_empty_documents():
    query = [{"type": "text", "text": "查询" * 600}, *IMAGE]
    document = [{"type": "text", "text": "文档" * 600}, *IMAGE]
    reranker = Mock()
    reranker.rerank_batch.return_value = [0.9, 0.7]
    result = await rerank_scores(reranker, query, [document, "  ", IMAGE], [0.1, 0.2, 0.3], 128)
    assert result.scores == [0.9, 0.2, 0.7]
    assert result.reranked == [True, False, True]
    sent_query, sent_documents = reranker.rerank_batch.call_args.args
    assert sent_query[-1] == IMAGE[0]
    assert sent_documents[0][-1] == IMAGE[0]
    assert sent_documents[1] == IMAGE
    assert (
        estimate_text_tokens(text_content(sent_query))
        + estimate_text_tokens(text_content(sent_documents[0]))
        <= 128
    )
    assert query[0]["text"] == "查询" * 600


@pytest.mark.asyncio
@pytest.mark.parametrize("scores", [None, [], [0.9], [float("nan"), float("inf")]])
async def test_multimodal_failure_keeps_vector_scores(scores):
    reranker = Mock()
    reranker.rerank_batch.return_value = scores
    result = await rerank_scores(reranker, IMAGE, [IMAGE, "text"], [0.3, 0.4])
    assert result.scores == [0.3, 0.4]
    assert result.reranked == [False, False]
