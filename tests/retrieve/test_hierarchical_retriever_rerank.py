# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Global retrieval and final rerank behavior tests."""

import asyncio
import threading

import pytest

from openviking.core.context import ContextLevel
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import render_abstract_overview
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig


def _result(uri, score, level=2, abstract=None, **extra):
    result = {
        "uri": uri,
        "abstract": abstract if abstract is not None else uri.rsplit("/", 1)[-1],
        "_score": score,
        "level": level,
        "context_type": "resource",
    }
    result.update(extra)
    return result


class DummyEmbedResult:
    def __init__(self) -> None:
        self.dense_vector = [1.0]
        self.sparse_vector = {"hello": 1.0}


class DummyEmbedder:
    def prepare_embedding_input(self, text: str) -> str:
        return text

    def embed(self, _query: str, is_query: bool = False) -> DummyEmbedResult:
        return DummyEmbedResult()

    async def embed_async(self, text: str, is_query: bool = False) -> DummyEmbedResult:
        return self.embed(text, is_query=is_query)


class DummyStorage:
    collection_name = "context"

    def __init__(self, results=()):
        self.results = list(results)
        self.search_calls = []

    async def get_account_backend(self, account_id):
        assert account_id
        return self

    async def collection_exists(self):
        return True

    async def search_in_tenant(self, ctx, **kwargs):
        self.search_calls.append({"ctx": ctx, **kwargs})
        level = kwargs.get("level")
        results = [
            dict(result)
            for result in self.results
            if level is None or result.get("level", 2) in level
        ]
        results.sort(key=lambda result: result["_score"], reverse=True)
        return results[: kwargs["limit"]]


class FakeRerankClient:
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []
        self._cursor = 0

    def rerank_batch(self, query: str, documents: list[str]):
        self.calls.append((query, list(documents)))
        start = self._cursor
        end = start + len(documents)
        self._cursor = end
        return list(self.scores[start:end])


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)


def _query() -> TypedQuery:
    return TypedQuery(query="hello", context_type=ContextType.RESOURCE, intent="")


def _config() -> RerankConfig:
    return RerankConfig(ak="ak", sk="sk", threshold=0.1)


def test_rerank_max_input_tokens_accepts_zero_or_at_least_128():
    assert RerankConfig(max_input_tokens=0).max_input_tokens == 0
    with pytest.raises(ValueError, match="max_input_tokens"):
        RerankConfig(max_input_tokens=127)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, RetrieverMode.THINKING])
async def test_retrieve_reranks_global_candidates_once(monkeypatch, mode):
    fake_client = FakeRerankClient([0.1, 0.2, 0.95, 0.99])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )
    storage = DummyStorage(
        [
            _result("viking://resources/root", 0.95, level=0, abstract="root abstract"),
            _result("viking://resources/dir", 0.85, level=1, abstract="dir overview"),
            _result("viking://resources/file-c", 0.2, abstract="file C"),
            _result(
                "viking://resources/file-d",
                0.05,
                abstract="file D",
                _origin_score=0.5,
                _time_score=0.1,
            ),
            _result("viking://resources/outside-pool", 0.01, abstract="outside pool"),
        ]
    )
    retriever = HierarchicalRetriever(storage, DummyEmbedder(), rerank_config=_config())
    query = _query()
    query.target_directories = ["viking://resources"]
    scope = {"op": "must", "field": "category", "conds": ["doc"]}

    result = await retriever.retrieve(
        query,
        ctx=_ctx(),
        limit=2,
        mode=mode,
        scope_dsl=scope,
        score_threshold=0.9,
        events_time_decay_protection="0",
    )

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-d",
        "viking://resources/file-c",
    ]
    assert [ctx.score for ctx in result.matched_contexts] == [0.99, 0.95]
    # Thresholding and final ranking use model scores, without repeating recall decay.
    assert result.matched_contexts[0].origin_score == 0.5
    assert result.matched_contexts[0].time_score == 0.1
    assert fake_client.calls == [("hello", ["root abstract", "dir overview", "file C", "file D"])]
    assert len(storage.search_calls) == 1
    assert storage.search_calls[0]["limit"] == 4
    assert storage.search_calls[0]["level"] is None
    assert storage.search_calls[0]["target_directories"] == ["viking://resources"]
    assert storage.search_calls[0]["extra_filter"] == scope
    assert storage.search_calls[0]["ctx"] == _ctx()
    assert storage.search_calls[0]["query_vector"] == [1.0]
    assert storage.search_calls[0]["sparse_query_vector"] == {"hello": 1.0}


@pytest.mark.asyncio
async def test_rerank_scores_does_not_truncate_by_default(monkeypatch):
    oversized_document = "summary-start " + ("填充内容" * 600) + " relevant-tail"
    fake_client = FakeRerankClient([0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=RerankConfig(ak="ak", sk="sk"),
    )

    await retriever._rerank_scores("query", [oversized_document], [0.2])

    assert retriever.rerank_max_input_tokens == 0
    assert fake_client.calls == [("query", [oversized_document])]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "oversized_document",
    [
        "summary-start " + ("filler " * 600) + " relevant-tail",
        "摘要开头" + ("填充内容" * 600) + "相关结论",
    ],
)
async def test_rerank_scores_bounds_oversized_documents_and_preserves_tail(
    monkeypatch, oversized_document
):
    fake_client = FakeRerankClient([0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=RerankConfig(ak="ak", sk="sk", max_input_tokens=128),
    )

    rerank = await retriever._rerank_scores("query", [oversized_document], [0.2])

    assert rerank.scores == [0.95]
    rerank_query, rerank_documents = fake_client.calls[0]
    bounded_document = rerank_documents[0]
    assert estimate_text_tokens(rerank_query) + estimate_text_tokens(bounded_document) <= 128
    assert "summary-start" in bounded_document or "摘要开头" in bounded_document
    assert "relevant-tail" in bounded_document or "相关结论" in bounded_document
    assert bounded_document != oversized_document


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, [0.9], RuntimeError("provider unavailable")])
async def test_retrieve_falls_back_to_vector_scores_when_rerank_fails(monkeypatch, failure):
    class FailedRerankClient(FakeRerankClient):
        def rerank_batch(self, query, documents):
            self.calls.append((query, list(documents)))
            if isinstance(failure, Exception):
                raise failure
            return failure

    fake_client = FailedRerankClient([])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )
    storage = DummyStorage(
        [
            _result(
                "viking://resources/a/deep-a.md",
                0.2,
                abstract="deep A",
                _origin_score=0.8,
                _time_score=0.25,
            ),
            _result("viking://resources/b/deep-b.md", 0.8, abstract="deep B"),
            _result("viking://resources/b/deep-c.md", 0.05, abstract="deep C"),
        ]
    )
    retriever = HierarchicalRetriever(storage, DummyEmbedder(), rerank_config=_config())

    result = await retriever.retrieve(
        _query(), ctx=_ctx(), limit=3, events_time_decay_protection="0"
    )
    explicit = await retriever.retrieve(
        _query(), ctx=_ctx(), limit=3, score_threshold=0.5, events_time_decay_protection="0"
    )

    assert [(hit.uri, hit.score) for hit in result.matched_contexts] == [
        ("viking://resources/b/deep-b.md", 0.8),
        ("viking://resources/a/deep-a.md", 0.2),
        ("viking://resources/b/deep-c.md", 0.05),
    ]
    assert result.matched_contexts[1].origin_score == 0.8
    assert result.matched_contexts[1].time_score == 0.25
    assert [hit.uri for hit in explicit.matched_contexts] == ["viking://resources/b/deep-b.md"]
    assert fake_client.calls == [("hello", ["deep B", "deep A", "deep C"])] * 2


@pytest.mark.asyncio
async def test_rerank_threshold_filters_only_model_scores(monkeypatch):
    fake_client = FakeRerankClient([0.05, 0.9, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )
    storage = DummyStorage(
        [
            _result("viking://resources/demoted", 0.9, abstract="demoted"),
            _result("viking://resources/relevant", 0.8, abstract="relevant"),
            _result("viking://resources/no-abstract", 0.05, abstract=""),
            _result("viking://resources/blank-abstract", 0.04, abstract="   "),
            _result("viking://resources/promoted", 0.01, abstract="promoted"),
        ]
    )
    retriever = HierarchicalRetriever(storage, DummyEmbedder(), rerank_config=_config())

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=5)

    assert fake_client.calls == [("hello", ["demoted", "relevant", "promoted"])]
    # The empty abstract was never reranked, so its vector score skips rerank.threshold.
    assert [(ctx.uri, ctx.score) for ctx in result.matched_contexts] == [
        ("viking://resources/promoted", 0.95),
        ("viking://resources/relevant", 0.9),
        ("viking://resources/no-abstract", 0.05),
        ("viking://resources/blank-abstract", 0.04),
    ]


@pytest.mark.asyncio
async def test_rerank_provider_does_not_block_the_event_loop(monkeypatch):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    released = threading.Event()

    class BlockingClient:
        def rerank_batch(self, query, documents):
            loop.call_soon_threadsafe(started.set)
            if not released.wait(timeout=2):
                raise TimeoutError("Event loop could not release provider")
            return [0.9]

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: BlockingClient(),
    )
    storage = DummyStorage([_result("viking://resources/doc.md", 0.2, abstract="doc")])
    retriever = HierarchicalRetriever(storage, DummyEmbedder(), rerank_config=_config())

    task = asyncio.create_task(retriever.retrieve(_query(), ctx=_ctx(), limit=1))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
    finally:
        released.set()
    result = await asyncio.wait_for(task, timeout=2)

    (hit,) = result.matched_contexts
    assert (hit.uri, hit.score) == ("viking://resources/doc.md", 0.9)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rerank_config, mode",
    [(None, RetrieverMode.THINKING), (RerankConfig(), None), (_config(), RetrieverMode.QUICK)],
)
async def test_retrieve_without_rerank_keeps_recall_limit_and_vector_threshold(
    monkeypatch, rerank_config, mode
):
    fake_client = FakeRerankClient([])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )
    storage = DummyStorage(
        [
            _result("viking://resources/root", 0.95, level=0, abstract="root abstract"),
            _result("viking://resources/file", 0.9, abstract="file abstract"),
            _result("viking://resources/dir", 0.05, level=1, abstract="dir overview"),
            _result("viking://resources/extra", 0.01, abstract="extra"),
        ]
    )
    retriever = HierarchicalRetriever(storage, DummyEmbedder(), rerank_config=rerank_config)

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=3, mode=mode)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/root/.abstract.md",
        "viking://resources/file",
        "viking://resources/dir/.overview.md",
    ]
    assert fake_client.calls == []
    assert [ctx.level for ctx in result.matched_contexts] == [0, 2, 1]
    assert [ctx.score for ctx in result.matched_contexts] == [0.95, 0.9, 0.05]
    assert len(storage.search_calls) == 1
    assert storage.search_calls[0]["limit"] == 3
    assert storage.search_calls[0]["level"] is None

    filtered = await retriever.retrieve(
        _query(), ctx=_ctx(), limit=3, mode=mode, score_threshold=0.5
    )
    assert [hit.uri for hit in filtered.matched_contexts] == [
        "viking://resources/root/.abstract.md",
        "viking://resources/file",
    ]


@pytest.mark.asyncio
async def test_retrieve_pushes_explicit_level_filter_to_vector_search():
    storage = DummyStorage(
        [
            _result("viking://resources/root", 0.99, level=0, abstract="root abstract"),
            _result("viking://resources/dir", 0.98, level=1, abstract="dir overview"),
            _result("viking://resources/file-a", 0.5, abstract="file A"),
            _result("viking://resources/file-b", 0.7, abstract="file B"),
        ]
    )
    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=DummyEmbedder(),
        rerank_config=None,
    )

    result = await retriever.retrieve(
        _query(),
        ctx=_ctx(),
        limit=3,
        scope_dsl={"op": "must", "field": "category", "conds": ["doc"]},
        level=[2],
    )

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert len(storage.search_calls) == 1
    assert storage.search_calls[0]["limit"] == 3
    assert storage.search_calls[0]["extra_filter"] == {
        "op": "must",
        "field": "category",
        "conds": ["doc"],
    }
    assert storage.search_calls[0]["level"] == [2]


@pytest.mark.asyncio
async def test_retrieve_without_rerank_filters_by_vector_score():
    storage = DummyStorage(
        [
            _result("viking://resources/high", 0.91, abstract="high"),
            _result(
                "viking://resources/exact",
                0.9,
                abstract="exact",
                _origin_score=1.0,
                _time_score=0.9,
            ),
        ]
    )
    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=DummyEmbedder(),
        rerank_config=None,
    )

    strict_result = await retriever.retrieve(
        _query(),
        ctx=_ctx(),
        limit=2,
        score_threshold=0.9,
        events_time_decay_protection="0",
    )
    inclusive_result = await retriever.retrieve(
        _query(),
        ctx=_ctx(),
        limit=2,
        score_threshold=0.9,
        score_gte=True,
        events_time_decay_protection="0",
    )

    assert [ctx.uri for ctx in strict_result.matched_contexts] == ["viking://resources/high"]
    assert [ctx.uri for ctx in inclusive_result.matched_contexts] == [
        "viking://resources/high",
        "viking://resources/exact",
    ]


@pytest.mark.asyncio
async def test_retrieve_returns_visible_previews_and_normalized_tags():
    abstract_uri = "viking://resources/demo"
    overview_uri = "viking://resources/docs"
    metadata = {
        "source": {"kind": "http", "uri": "https://example.com/private.pdf"},
        "generated_by": {"component": "SemanticProcessor", "trigger": "ingest"},
    }
    markdown = "---\ntitle: User document\n---\n\nVisible body."
    storage = DummyStorage(
        [
            _result(
                abstract_uri,
                1.0,
                level=int(ContextLevel.ABSTRACT),
                abstract=render_abstract_overview(
                    ContextLevel.ABSTRACT, abstract_uri, "Visible abstract.", metadata
                ),
                search_tags=["default", "team=infra", "bad=", "project=viking"],
            ),
            _result(
                overview_uri,
                0.9,
                level=int(ContextLevel.OVERVIEW),
                abstract=render_abstract_overview(
                    ContextLevel.OVERVIEW, overview_uri, "# Visible overview", metadata
                ),
            ),
            _result("viking://resources/demo.md", 0.8, level=2, abstract=markdown),
            _result(
                "viking://resources/malformed",
                0.7,
                level=int(ContextLevel.ABSTRACT),
                abstract="---\n",
            ),
        ]
    )
    retriever = HierarchicalRetriever(storage, DummyEmbedder())

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=4)

    assert [(hit.uri, hit.abstract, hit.search_tags) for hit in result.matched_contexts] == [
        (f"{abstract_uri}/.abstract.md", "Visible abstract.", ["team=infra", "project=viking"]),
        (f"{overview_uri}/.overview.md", "# Visible overview", []),
        ("viking://resources/demo.md", markdown, []),
        ("viking://resources/malformed/.abstract.md", "", []),
    ]
