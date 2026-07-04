# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Hierarchical retriever rerank behavior tests."""

import pytest
from pydantic import ValidationError

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig, RetrievalConfig


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
    def __init__(self) -> None:
        self.collection_name = "context"
        self.search_calls = []
        self.child_search_calls = []

    async def collection_exists_bound(self) -> bool:
        return True

    async def search_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        level=None,
        limit: int = 10,
        offset: int = 0,
    ):
        self.search_calls.append(
            {
                "ctx": ctx,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "level": level,
                "limit": limit,
                "offset": offset,
            }
        )
        return [
            _result("viking://resources/root-a", 0.2, level=1, abstract="root A"),
            _result("viking://resources/root-b", 0.8, level=1, abstract="root B"),
        ]

    async def search_children_in_tenant(
        self,
        ctx,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.child_search_calls.append(
            {
                "ctx": ctx,
                "parent_uri": parent_uri,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        if parent_uri == "viking://resources":
            return [
                _result("viking://resources/file-a", 0.2, abstract="child A", category="doc"),
                _result("viking://resources/file-b", 0.8, abstract="child B", category="doc"),
            ]
        return []


class QuickSearchStorage(DummyStorage):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)

    async def search_in_tenant(
        self,
        ctx,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        level=None,
        limit: int = 10,
        offset: int = 0,
    ):
        self.search_calls.append(
            {
                "ctx": ctx,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "level": level,
                "limit": limit,
                "offset": offset,
            }
        )
        return [
            dict(result)
            for result in self.results
            if level is None or result.get("level", 2) in level
        ]

    async def search_children_in_tenant(
        self,
        ctx,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        self.child_search_calls.append(
            {
                "ctx": ctx,
                "parent_uri": parent_uri,
                "query_vector": query_vector,
                "sparse_query_vector": sparse_query_vector,
                "context_type": context_type,
                "target_directories": target_directories,
                "extra_filter": extra_filter,
                "limit": limit,
            }
        )
        return [_result(f"{parent_uri}/should-not-be-returned", 1.0, abstract="child")]


class DirectChildProxy:
    async def search_children_in_tenant(
        self,
        parent_uri: str,
        query_vector=None,
        sparse_query_vector=None,
        context_type=None,
        target_directories=None,
        extra_filter=None,
        limit: int = 10,
    ):
        return [
            _result(f"{parent_uri}/file-a", 0.2, abstract="child A"),
            _result(f"{parent_uri}/file-b", 0.8, abstract="child B"),
        ]


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


def test_retriever_initializes_rerank_client(monkeypatch):
    fake_client = FakeRerankClient([0.9, 0.1])

    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    storage = DummyStorage()
    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    assert retriever._rerank_client is fake_client


@pytest.mark.asyncio
async def test_retrieve_uses_rerank_scores_in_thinking_mode(monkeypatch):
    fake_client = FakeRerankClient([0.95, 0.05, 0.11, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    storage = DummyStorage()
    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls[0] == ("hello", ["root A", "root B"])
    assert fake_client.calls[1] == ("hello", ["child A", "child B"])
    assert storage.search_calls[0]["level"] == [0, 1]


@pytest.mark.asyncio
async def test_retrieve_falls_back_to_vector_scores_when_rerank_returns_none(monkeypatch):
    class NoneRerankClient(FakeRerankClient):
        def rerank_batch(self, query: str, documents: list[str]):
            self.calls.append((query, list(documents)))
            return None

    fake_client = NoneRerankClient([])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )

    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert fake_client.calls


@pytest.mark.asyncio
async def test_quick_mode_uses_single_vector_search_without_rerank_or_recursion(monkeypatch):
    fake_client = FakeRerankClient([0.05, 0.95, 0.95])
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )
    storage = QuickSearchStorage(
        [
            _result("viking://resources/root", 0.95, level=0, abstract="root abstract"),
            _result("viking://resources/file", 0.9, abstract="file abstract"),
            _result("viking://resources/dir", 0.85, level=1, abstract="dir overview"),
        ]
    )

    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=DummyEmbedder(),
        rerank_config=_config(),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=3, mode=RetrieverMode.QUICK)

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/root/.abstract.md",
        "viking://resources/file",
        "viking://resources/dir/.overview.md",
    ]
    assert [ctx.level for ctx in result.matched_contexts] == [0, 2, 1]
    assert [ctx.score for ctx in result.matched_contexts] == [
        pytest.approx(0.95),
        pytest.approx(0.9),
        pytest.approx(0.85),
    ]
    assert len(storage.search_calls) == 1
    assert storage.search_calls[0]["limit"] == retriever.GLOBAL_SEARCH_TOPK
    assert storage.search_calls[0]["extra_filter"] is None
    assert storage.search_calls[0]["level"] is None
    assert storage.child_search_calls == []
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_quick_mode_pushes_explicit_level_filter_to_vector_search():
    storage = QuickSearchStorage(
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
        mode=RetrieverMode.QUICK,
        scope_dsl={"op": "must", "field": "category", "conds": ["doc"]},
        level=[2],
    )

    assert [ctx.uri for ctx in result.matched_contexts] == [
        "viking://resources/file-b",
        "viking://resources/file-a",
    ]
    assert len(storage.search_calls) == 1
    assert storage.search_calls[0]["limit"] == retriever.GLOBAL_SEARCH_TOPK
    assert storage.search_calls[0]["extra_filter"] == {
        "op": "must",
        "field": "category",
        "conds": ["doc"],
    }
    assert storage.search_calls[0]["level"] == [2]
    assert storage.child_search_calls == []


@pytest.mark.asyncio
async def test_quick_mode_threshold_uses_raw_vector_score():
    storage = QuickSearchStorage(
        [
            _result("viking://resources/high", 0.91, abstract="high"),
            _result("viking://resources/exact", 0.9, abstract="exact"),
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
        mode=RetrieverMode.QUICK,
        score_threshold=0.9,
    )
    inclusive_result = await retriever.retrieve(
        _query(),
        ctx=_ctx(),
        limit=2,
        mode=RetrieverMode.QUICK,
        score_threshold=0.9,
        score_gte=True,
    )

    assert [ctx.uri for ctx in strict_result.matched_contexts] == ["viking://resources/high"]
    assert [ctx.uri for ctx in inclusive_result.matched_contexts] == [
        "viking://resources/high",
        "viking://resources/exact",
    ]


@pytest.mark.asyncio
async def test_quick_mode_keeps_scores_pure_when_hotness_and_propagation_configured(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.hotness_score",
        lambda *args, **kwargs: pytest.fail("hotness_score should not be called in QUICK mode"),
    )
    storage = QuickSearchStorage(
        [
            _result(
                "viking://resources/file-a",
                0.8,
                abstract="file A",
                active_count=100,
                updated_at="2026-01-01T00:00:00+00:00",
            )
        ]
    )
    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=DummyEmbedder(),
        rerank_config=None,
        retrieval_config=RetrievalConfig(hotness_alpha=0.5, score_propagation_alpha=0.1),
    )

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=1, mode=RetrieverMode.QUICK)

    assert result.matched_contexts[0].score == pytest.approx(0.8)
    assert storage.child_search_calls == []


@pytest.mark.asyncio
async def test_score_propagation_alpha_uses_configured_weight():
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(score_propagation_alpha=1.0),
    )

    candidates = await retriever._recursive_search(
        vector_proxy=DirectChildProxy(),
        query="hello",
        query_vector=None,
        sparse_query_vector=None,
        starting_points=[("viking://resources", 0.4)],
        limit=1,
        mode=RetrieverMode.QUICK,
    )

    assert candidates[0]["uri"] == "viking://resources/file-b"
    assert candidates[0]["_final_score"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_default_retrieval_config_uses_semantic_score_without_hotness(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.hotness_score",
        lambda *args, **kwargs: pytest.fail("hotness_score should not be called by default"),
    )
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
    )

    result = await retriever._convert_to_matched_contexts(
        [_result("viking://resources/file-a", 1.0, abstract="child A")],
        ctx=_ctx(),
    )

    assert result[0].score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_retrieval_hotness_alpha_blends_when_configured(monkeypatch):
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.hotness_score",
        lambda *args, **kwargs: 0.5,
    )
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
        retrieval_config=RetrievalConfig(hotness_alpha=0.2),
    )

    result = await retriever._convert_to_matched_contexts(
        [_result("viking://resources/file-a", 1.0, abstract="child A")],
        ctx=_ctx(),
    )

    assert result[0].score == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_convert_to_matched_contexts_returns_empty_relations():
    retriever = HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=None,
        rerank_config=None,
    )

    result = await retriever._convert_to_matched_contexts(
        [_result("viking://resources/file-a", 1.0, abstract="child A")],
        ctx=_ctx(),
    )

    assert result[0].relations == []


# ---------------------------------------------------------------------------
# max_chars_per_doc — #2880 configurable rerank input truncation
# ---------------------------------------------------------------------------


def _cap_config(cap: int) -> RerankConfig:
    return RerankConfig(ak="ak", sk="sk", threshold=0.1, max_chars_per_doc=cap)


def _capped_retriever(monkeypatch, fake_client, cap: int) -> HierarchicalRetriever:
    """A retriever whose rerank client is the fake, with max_chars_per_doc=cap."""
    # Tiny caps in these truncation tests are intentionally below the stability
    # floor; silence the sub-floor soft-warn so it does not spam CI logs (some CI
    # treats warning logs as failures). The dedicated warn tests do not use this
    # helper, so they still assert the warning fires.
    monkeypatch.setattr(
        "openviking_cli.utils.config.rerank_config.logger.warning",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake_client,
    )
    return HierarchicalRetriever(
        storage=DummyStorage(),
        embedder=DummyEmbedder(),
        rerank_config=_cap_config(cap),
    )


def test_rerank_cap_zero_is_byte_identical_parity(monkeypatch):
    fake = FakeRerankClient([0.9, 0.1])
    retriever = _capped_retriever(monkeypatch, fake, 0)

    docs = ["abcdefgh", "xy"]
    retriever._rerank_scores("hello", docs, [0.0, 0.0])

    # cap=0: docs reach rerank_batch untouched (byte-identical, no truncation).
    assert fake.calls[0][1] == ["abcdefgh", "xy"]


def test_rerank_cap_truncates_each_doc(monkeypatch):
    fake = FakeRerankClient([0.9, 0.1])
    retriever = _capped_retriever(monkeypatch, fake, 4)

    retriever._rerank_scores("hello", ["abcdefgh", "xyz"], [0.0, 0.0])

    # Each doc sliced to [:cap]; a doc shorter than cap is unchanged.
    assert fake.calls[0][1] == ["abcd", "xyz"]


def test_rerank_cap_does_not_truncate_the_query(monkeypatch):
    fake = FakeRerankClient([0.9])
    retriever = _capped_retriever(monkeypatch, fake, 4)

    retriever._rerank_scores("hello world", ["abcdefgh"], [0.0])

    assert fake.calls[0][0] == "hello world"  # query is never truncated
    assert fake.calls[0][1] == ["abcd"]


@pytest.mark.parametrize(
    "cap,doc,expected",
    [
        (0, "abcdef", "abcdef"),  # 0 = OFF, not "truncate to 0 chars"
        (10, "abcdef", "abcdef"),  # cap > len: unchanged
        (7, "abcdef", "abcdef"),  # cap == len + 1: unchanged
        (6, "abcdef", "abcdef"),  # cap == len: unchanged
        (1, "abcdef", "a"),  # cap == 1: one codepoint
    ],
)
def test_rerank_cap_boundaries(monkeypatch, cap, doc, expected):
    fake = FakeRerankClient([0.5])
    retriever = _capped_retriever(monkeypatch, fake, cap)

    retriever._rerank_scores("q", [doc], [0.0])

    assert fake.calls[0][1] == [expected]


@pytest.mark.parametrize(
    "cap,doc,expected",
    [
        (2, "你好世界", "你好"),  # CJK: codepoint-safe
        (2, "🧑‍🚀X", "🧑‍"),  # ZWJ emoji: codepoint-safe, NOT grapheme-safe
        (4, "", ""),  # empty abstract stays empty
    ],
)
def test_rerank_cap_multibyte_and_empty(monkeypatch, cap, doc, expected):
    fake = FakeRerankClient([0.5])
    retriever = _capped_retriever(monkeypatch, fake, cap)

    retriever._rerank_scores("q", [doc], [0.0])

    assert fake.calls[0][1] == [expected]


def test_rerank_cap_fail_open_when_rerank_raises(monkeypatch):
    class Raiser(FakeRerankClient):
        def rerank_batch(self, query, documents):
            self.calls.append((query, list(documents)))
            raise RuntimeError("model input overflow")

    fake = Raiser([])
    retriever = _capped_retriever(monkeypatch, fake, 4)

    out = retriever._rerank_scores("q", ["abcdefgh", "xyz"], [0.11, 0.22])

    assert out == [0.11, 0.22]  # falls back to vector scores, len invariant intact
    assert fake.calls[0][1] == ["abcd", "xyz"]  # truncation happened before the call


def test_rerank_cap_fail_open_on_wrong_length(monkeypatch):
    fake = FakeRerankClient([0.9])  # one score returned for two documents
    retriever = _capped_retriever(monkeypatch, fake, 4)

    out = retriever._rerank_scores("q", ["abcdefgh", "xyz"], [0.11, 0.22])

    assert out == [0.11, 0.22]


@pytest.mark.asyncio
async def test_thinking_mode_truncates_docs_at_both_sites(monkeypatch):
    fake = FakeRerankClient([0.95, 0.05, 0.11, 0.95])
    retriever = _capped_retriever(monkeypatch, fake, 4)

    await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    # cap=4: DummyStorage abstracts collapse under [:4] at both rerank sites.
    # Site A (global) and Site B (child recursion) both funnel through _rerank_scores.
    assert fake.calls[0] == ("hello", ["root", "root"])  # "root A"[:4]/"root B"[:4] -> "root"
    assert fake.calls[1] == ("hello", ["chil", "chil"])  # "child A"[:4]/"child B"[:4] -> "chil"


@pytest.mark.asyncio
async def test_truncation_does_not_alter_returned_abstract(monkeypatch):
    fake = FakeRerankClient([0.95, 0.05, 0.11, 0.95])
    retriever = _capped_retriever(monkeypatch, fake, 4)

    result = await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.THINKING)

    abstracts = {ctx.uri: ctx.abstract for ctx in result.matched_contexts}
    # Truncation is model-input-only; the returned abstract is the full original.
    assert abstracts["viking://resources/file-b"] == "child B"
    assert abstracts["viking://resources/file-a"] == "child A"


@pytest.mark.asyncio
async def test_quick_mode_never_reranks_even_with_cap_set(monkeypatch):
    fake = FakeRerankClient([0.5, 0.5, 0.5])
    monkeypatch.setattr(
        "openviking_cli.utils.config.rerank_config.logger.warning",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda config: fake,
    )
    storage = QuickSearchStorage(
        [
            _result("viking://resources/root", 0.95, level=0, abstract="root abstract"),
            _result("viking://resources/file", 0.9, abstract="file abstract"),
        ]
    )
    retriever = HierarchicalRetriever(
        storage=storage,
        embedder=DummyEmbedder(),
        rerank_config=_cap_config(4),
    )

    await retriever.retrieve(_query(), ctx=_ctx(), limit=2, mode=RetrieverMode.QUICK)

    assert fake.calls == []


def test_rerank_config_default_cap_is_zero():
    assert RerankConfig(ak="ak", sk="sk").max_chars_per_doc == 0


def test_rerank_config_rejects_negative_cap():
    with pytest.raises(ValidationError):
        RerankConfig(ak="ak", sk="sk", max_chars_per_doc=-1)


def test_rerank_config_rejects_non_int_cap_under_strict():
    with pytest.raises(ValidationError):
        RerankConfig(ak="ak", sk="sk", max_chars_per_doc="5")


def test_rerank_config_rejects_unknown_field():
    with pytest.raises(ValidationError):
        RerankConfig(ak="ak", sk="sk", max_chars_pr_doc=4)  # typo, extra=forbid


def test_rerank_config_warns_on_sub_floor_cap(monkeypatch):
    from openviking_cli.utils.config import rerank_config as rc_mod

    warnings: list = []
    monkeypatch.setattr(rc_mod.logger, "warning", lambda *a, **k: warnings.append((a, k)))

    RerankConfig(ak="ak", sk="sk", max_chars_per_doc=50)

    assert warnings, "expected a soft-warn for a non-zero cap below the stability floor"
    # The configured cap value is surfaced in the warning so the misconfig is actionable.
    assert 50 in warnings[0][0]


def test_rerank_config_does_not_warn_when_cap_disabled(monkeypatch):
    from openviking_cli.utils.config import rerank_config as rc_mod

    warnings: list = []
    monkeypatch.setattr(rc_mod.logger, "warning", lambda *a, **k: warnings.append((a, k)))

    RerankConfig(ak="ak", sk="sk", max_chars_per_doc=0)
    RerankConfig(ak="ak", sk="sk", max_chars_per_doc=2000)

    assert warnings == []
