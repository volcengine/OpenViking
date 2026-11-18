# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for reranking visual candidates rather than their captions."""

from unittest.mock import AsyncMock

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.utils.image_search import image_bytes_to_data_uri
from openviking_cli.retrieve.types import TypedQuery
from tests.retrieve.test_hierarchical_retriever_image import MultimodalEmbedder
from tests.retrieve.test_hierarchical_retriever_rerank import DummyStorage, _ctx, _result


@pytest.mark.asyncio
@pytest.mark.parametrize("image_query", [False, True])
async def test_rerank_receives_image_content_even_with_misleading_abstract(
    monkeypatch, image_query
):
    image_uri = "viking://resources/photos/red.png"
    storage = DummyStorage(
        [
            _result("viking://resources/blue.md", 0.9, abstract="blue square"),
            _result(image_uri, 0.8, abstract="incorrect caption: blue square"),
        ]
    )
    read = AsyncMock(return_value=b"synthetic-image")
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: type("FS", (), {"read_file_bytes": read})(),
    )
    calls = []

    class VisualReranker:
        supports_multimodal = True

        def rerank_batch(self, query, documents):
            calls.append((query, documents))
            return [0.1, 0.99]

    image_input = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cXVlcnk="}}]
    query = TypedQuery(
        query="red square",
        context_type=None,
        intent="",
        image_query=image_query,
        embedding_input=image_input if image_query else None,
    )
    retriever = HierarchicalRetriever(storage, MultimodalEmbedder())
    retriever._rerank_client = VisualReranker()
    ctx = _ctx()
    result = await retriever.retrieve(query, ctx=ctx, limit=1)

    assert len(calls) == 1
    assert calls[0][0] == (image_input if image_query else "red square")
    assert calls[0][1] == [
        "blue square",
        [
            {
                "type": "image_url",
                "image_url": {"url": image_bytes_to_data_uri(b"synthetic-image", image_uri)},
            }
        ],
    ]
    assert read.await_args.args == (image_uri,)
    assert read.await_args.kwargs["ctx"] is ctx
    assert storage.search_calls[0]["limit"] == 2
    assert result.matched_contexts[0].uri == image_uri
    assert result.matched_contexts[0].score == 0.99


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [PermissionError("denied"), FileNotFoundError("missing")])
async def test_unreadable_image_keeps_vector_score_and_is_not_sent(monkeypatch, failure):
    from unittest.mock import Mock

    image_uri = "viking://resources/photos/red.png"
    storage = DummyStorage(
        [
            _result(image_uri, 0.05, abstract="misleading caption"),
            _result("viking://resources/text.md", 0.9, abstract="text"),
        ]
    )
    read = AsyncMock(side_effect=failure)
    monkeypatch.setattr(
        "openviking.storage.viking_fs.get_viking_fs",
        lambda: type("FS", (), {"read_file_bytes": read})(),
    )
    reranker = Mock(supports_multimodal=True)
    reranker.rerank_batch.return_value = [0.8]
    retriever = HierarchicalRetriever(storage, MultimodalEmbedder())
    retriever.rerank_threshold = 0.5
    retriever._rerank_client = reranker
    query = TypedQuery(query="red", context_type=None, intent="")
    result = await retriever.retrieve(query, _ctx(), limit=2)
    assert reranker.rerank_batch.call_args.args == ("red", ["text"])
    assert [(item.uri, item.score) for item in result.matched_contexts] == [
        ("viking://resources/text.md", 0.8),
        (image_uri, 0.05),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, multimodal", [(RetrieverMode.QUICK, True), (None, False)])
async def test_image_query_does_not_read_or_rerank_when_unsupported(monkeypatch, mode, multimodal):
    from unittest.mock import Mock

    get_fs = Mock(side_effect=AssertionError("must not read images"))
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", get_fs)
    storage = DummyStorage([_result("viking://resources/red.png", 0.9)])
    retriever = HierarchicalRetriever(storage, MultimodalEmbedder())
    retriever._rerank_client = Mock(supports_multimodal=multimodal)
    query = TypedQuery(
        query="",
        context_type=None,
        intent="",
        image_query=True,
        embedding_input=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}],
    )
    await retriever.retrieve(query, _ctx(), limit=1, mode=mode, level=[0, 1, 2])
    assert storage.search_calls[0]["level"] == [0, 1, 2]
    assert storage.search_calls[0]["limit"] == 1
    retriever._rerank_client.rerank_batch.assert_not_called()
    get_fs.assert_not_called()


@pytest.mark.asyncio
async def test_directory_with_image_suffix_uses_text_without_reading_bytes(monkeypatch):
    from unittest.mock import Mock

    get_fs = Mock(side_effect=AssertionError("directory is not an image"))
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", get_fs)
    storage = DummyStorage(
        [_result("viking://resources/red.png", 0.9, level=0, abstract="summary")]
    )
    retriever = HierarchicalRetriever(storage, MultimodalEmbedder())
    retriever._rerank_client = Mock(supports_multimodal=True)
    retriever._rerank_client.rerank_batch.return_value = [0.7]
    await retriever.retrieve(TypedQuery(query="red", context_type=None, intent=""), _ctx())
    assert retriever._rerank_client.rerank_batch.call_args.args == ("red", ["summary"])
    get_fs.assert_not_called()
