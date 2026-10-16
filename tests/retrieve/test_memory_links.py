# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from collections import Counter

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.retrieve.memory_links import MemoryLinks, _rerank_all, search_with_memory_links
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, In, RawDSL
from openviking.storage.vikingdb_manager import VikingDBManagerProxy
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.retrieve.types import ContextType, FindResult, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import RerankConfig

ROOT = "viking://user/alice/memories"
PERSON = ROOT + "/entities/person/melanie.md"
RACE = ROOT + "/events/race.md"
OTHER = ROOT + "/events/other.md"
THIRD = ROOT + "/events/third.md"
PRIVATE = "viking://user/bob/memories/events/private.md"


def relation(source, target, **kwargs):
    return {"from_uri": source, "to_uri": target, **kwargs}


def memory(*, links=(), backlinks=()):
    return (
        "# Memory\n\n<!-- MEMORY_FIELDS\n"
        + json.dumps(
            {"memory_type": "entities", "links": list(links), "backlinks": list(backlinks)}
        )
        + "\n-->"
    )


def row(uri, score=0.9, abstract=None):
    return {
        "uri": uri,
        "_score": score,
        "context_type": "memory",
        "level": 2,
        "abstract": abstract or uri.rsplit("/", 1)[-1],
    }


def ctx():
    return RequestContext(user=UserIdentifier("account", "alice"), role=Role.USER)


class Files:
    def __init__(self, files):
        self.files = files
        self.reads = Counter()

    async def read_file(self, uri, *, ctx):
        assert ctx.user.user_id == "alice"
        self.reads[uri] += 1
        if uri == PRIVATE:
            raise PermissionError(uri)
        return self.files[uri]


class Storage:
    collection_name = "context"

    def __init__(self, seeds=(), targets=()):
        self.seeds = list(seeds)
        self.targets = list(targets)
        self.lookups = []
        self.recall_calls = []

    async def get_account_backend(self, _account_id):
        return self

    async def collection_exists(self):
        return True

    async def search_in_tenant(self, _ctx, **kwargs):
        self.recall_calls.append(kwargs)
        return self.seeds[: kwargs["limit"]]

    async def search_by_keywords_in_tenant(self, request_ctx, **kwargs):
        return await self.search_in_tenant(request_ctx, **kwargs)

    async def filter_in_tenant(self, request_ctx, **kwargs):
        self.lookups.append({"ctx": request_ctx, **kwargs})
        allowed = kwargs["extra_filter"].conds[0].values
        rows = [r for r in self.targets if r["uri"] in allowed and r["uri"] != PRIVATE]
        start = kwargs.get("offset", 0)
        return rows[start : start + kwargs["limit"]]


class Reranker:
    def __init__(self, *, fail_batch=None):
        self.calls = []
        self.fail_batch = fail_batch

    def rerank_batch(self, query, documents):
        self.calls.append((query, documents))
        if len(self.calls) == self.fail_batch:
            return None
        return [0.99 if text == "race evidence" else 0.2 for text in documents]


def retriever(monkeypatch, storage, fs, *, fail_batch=None):
    reranker = Reranker(fail_batch=fail_batch)
    monkeypatch.setattr(
        "openviking.retrieve.hierarchical_retriever.RerankClient.from_config",
        lambda _config: reranker,
    )
    config = RerankConfig(ak="test", sk="test", threshold=0, batch_size=1)
    return HierarchicalRetriever(storage, None, config), reranker


@pytest.mark.asyncio
async def test_backlink_expansion_can_win_global_rerank_without_following_second_hop(monkeypatch):
    fs = Files(
        {
            PERSON: memory(backlinks=[relation(RACE, PERSON)]),
            RACE: memory(links=[relation(RACE, PERSON), relation(RACE, THIRD)]),
            THIRD: memory(),
        }
    )
    storage = Storage([row(PERSON)], [row(RACE, abstract="race evidence"), row(THIRD)])
    search, model = retriever(monkeypatch, storage, fs)
    query = TypedQuery("charity race", ContextType.MEMORY, "", target_directories=[ROOT])

    result = await search_with_memory_links(search, fs, query, ctx(), limit=1)

    assert [r.uri for r in result.matched_contexts] == [RACE]
    assert model.calls == [("charity race", ["melanie.md"]), ("charity race", ["race evidence"])]
    assert all("third.md" not in documents for _, documents in model.calls)
    assert storage.lookups[0]["ctx"] == ctx()
    assert storage.lookups[0]["target_directories"] == [ROOT]
    assert storage.lookups[0]["context_type"] == "memory"
    assert storage.lookups[0]["level"] == [2]


@pytest.mark.asyncio
async def test_links_are_deduplicated_and_metadata_lookup_preserves_filter():
    scope = {"op": "must", "field": "search_tags", "conds": ["source=allowed"]}
    fs = Files(
        {
            PERSON: memory(
                links=[relation(PERSON, RACE), relation(PERSON, RACE)],
                backlinks=[relation(RACE, PERSON)],
            ),
            RACE: memory(),
        }
    )
    storage = Storage(targets=[row(RACE)])
    links = MemoryLinks(fs, VikingDBManagerProxy(storage, ctx()), ctx(), [ROOT], scope)

    expanded = await links.expand([row(PERSON)])

    assert [r["uri"] for r in expanded] == [PERSON, RACE]
    assert expanded[1]["_score"] == 0
    assert storage.lookups[0]["extra_filter"] == And([In("uri", [RACE]), RawDSL(scope)])
    assert fs.reads == {PERSON: 1, RACE: 1}


@pytest.mark.asyncio
async def test_inaccessible_missing_and_malformed_targets_are_not_exposed():
    missing = ROOT + "/events/deleted.md"
    fs = Files(
        {
            PERSON: memory(
                links=[
                    relation(PERSON, PRIVATE),
                    relation(PERSON, missing),
                    relation(OTHER, RACE),  # Not an outgoing link of PERSON.
                    relation(PERSON, PERSON),
                    relation(PERSON, "https://example.com/not-memory"),
                    relation(PERSON, RACE),
                ]
            ),
            RACE: memory(),
        }
    )
    storage = Storage(targets=[row(RACE), row(missing)])
    links = MemoryLinks(fs, VikingDBManagerProxy(storage, ctx()), ctx(), [ROOT], None)
    match = FindResult.from_dict({"memories": [row(PERSON)]}).memories[0]

    await links.attach([match])

    assert [link["to_uri"] for link in match.links] == [RACE]
    assert match.backlinks == []
    assert PRIVATE not in fs.reads


@pytest.mark.asyncio
async def test_failed_later_batch_discards_all_model_scores_and_link_only_candidates(monkeypatch):
    fs = Files({PERSON: memory(links=[relation(PERSON, RACE)]), RACE: memory()})
    storage = Storage([row(PERSON, 0.75)], [row(RACE, abstract="race evidence")])
    search, model = retriever(monkeypatch, storage, fs, fail_batch=2)

    result = await search_with_memory_links(
        search,
        fs,
        TypedQuery("race", ContextType.MEMORY, ""),
        ctx(),
        limit=2,
        score_threshold=-1,
    )

    assert len(model.calls) == 2
    assert [(r.uri, r.score) for r in result.matched_contexts] == [(PERSON, 0.75)]


@pytest.mark.asyncio
async def test_default_search_never_reads_link_files(monkeypatch):
    fs = Files({})
    storage = Storage([row(PERSON)])
    search, _model = retriever(monkeypatch, storage, fs)

    result = await search.retrieve(TypedQuery("race", ContextType.MEMORY, ""), ctx())

    assert fs.reads == {}
    assert storage.lookups == []
    payload = FindResult(memories=result.matched_contexts, resources=[], skills=[]).to_dict()
    assert "links" not in payload["memories"][0]
    assert "backlinks" not in payload["memories"][0]


@pytest.mark.asyncio
async def test_links_are_ignored_without_reranker():
    storage = Storage([row(PERSON)])
    fs = Files({PERSON: memory(links=[relation(PERSON, RACE)])})
    search = HierarchicalRetriever(storage, None)
    query = TypedQuery("race", ContextType.MEMORY, "")

    result = await search_with_memory_links(search, fs, query, ctx())

    payload = FindResult(memories=result.matched_contexts, resources=[], skills=[]).to_dict()
    assert fs.reads == {}
    assert storage.lookups == []
    assert "links" not in payload["memories"][0]
    assert "backlinks" not in payload["memories"][0]


@pytest.mark.asyncio
async def test_expansion_keeps_all_one_hop_candidates():
    targets = [ROOT + f"/events/{i}.md" for i in range(12)]
    fs = Files(
        {
            PERSON: memory(links=[relation(PERSON, u) for u in targets]),
            **{u: memory() for u in targets},
        }
    )
    storage = Storage(targets=[row(u) for u in targets])
    links = MemoryLinks(fs, VikingDBManagerProxy(storage, ctx()), ctx(), [], None)

    expanded = await links.expand([row(PERSON)])

    assert len(expanded) == 13  # No top-k cap before reranking linked targets.


@pytest.mark.asyncio
async def test_empty_abstract_targets_are_not_added_to_rerank_candidates():
    fs = Files({PERSON: memory(links=[relation(PERSON, RACE)]), RACE: memory()})
    storage = Storage(targets=[{**row(RACE), "abstract": " "}])
    links = MemoryLinks(fs, VikingDBManagerProxy(storage, ctx()), ctx(), [], None)

    assert await links.expand([row(PERSON)]) == [row(PERSON)]


@pytest.mark.asyncio
async def test_nonfinite_model_score_discards_link_only_candidates(monkeypatch):
    fs = Files({PERSON: memory(links=[relation(PERSON, RACE)]), RACE: memory()})
    storage = Storage([row(PERSON, 0.75)], [row(RACE, abstract="race evidence")])
    search, model = retriever(monkeypatch, storage, fs)
    model.rerank_batch = lambda _query, _documents: [float("nan")]

    result = await search_with_memory_links(
        search,
        fs,
        TypedQuery("race", ContextType.MEMORY, ""),
        ctx(),
        score_threshold=-1,
    )
    assert [(r.uri, r.score) for r in result.matched_contexts] == [(PERSON, 0.75)]


@pytest.mark.asyncio
async def test_all_513_linked_targets_are_reranked_before_global_top_one(monkeypatch):
    targets = [ROOT + f"/events/{i:04}.md" for i in range(513)]
    fs = Files(
        {
            PERSON: memory(links=[relation(PERSON, u) for u in targets]),
            **{u: memory() for u in targets},
        }
    )
    storage = Storage(
        [row(PERSON)],
        [row(u, abstract="race evidence" if u == targets[-1] else "other event") for u in targets],
    )
    search, model = retriever(monkeypatch, storage, fs)
    search.rerank_config.batch_size = 500  # VikingDB must still cap batches at 100.

    result = await search_with_memory_links(
        search,
        fs,
        TypedQuery("race", ContextType.MEMORY, "", target_directories=[ROOT]),
        ctx(),
        limit=1,
    )

    assert [r.uri for r in result.matched_contexts] == [targets[-1]]
    assert [len(documents) for _, documents in model.calls] == [100, 100, 100, 100, 100, 14]
    assert [lookup["limit"] for lookup in storage.lookups] == [200, 200, 113]
    assert fs.reads == {PERSON: 1, **dict.fromkeys(targets, 1)}


@pytest.mark.asyncio
async def test_filesystem_denied_target_never_reaches_reranker(monkeypatch):
    fs = Files({PERSON: memory(links=[relation(PERSON, PRIVATE)])})
    storage = Storage([row(PERSON)])

    # A stale index grant must not override the filesystem's current permissions.
    async def lookup(_ctx, **_kwargs):
        return [row(PRIVATE, abstract="private evidence")]

    storage.filter_in_tenant = lookup
    search, model = retriever(monkeypatch, storage, fs)

    result = await search_with_memory_links(
        search,
        fs,
        TypedQuery("race", ContextType.MEMORY, ""),
        ctx(),
    )
    assert result.matched_contexts[0].links == []
    assert all("private evidence" not in documents for _, documents in model.calls)


@pytest.mark.asyncio
async def test_duplicate_index_rows_do_not_crowd_out_other_link_targets():
    fs = Files(
        {
            PERSON: memory(links=[relation(PERSON, RACE), relation(PERSON, OTHER)]),
            RACE: memory(),
            OTHER: memory(),
        }
    )
    storage = Storage(targets=[row(RACE), row(RACE), row(OTHER)])
    links = MemoryLinks(fs, VikingDBManagerProxy(storage, ctx()), ctx(), [], None)

    expanded = await links.expand([row(PERSON)])

    assert {row["uri"] for row in expanded} == {PERSON, RACE, OTHER}
    assert [lookup["offset"] for lookup in storage.lookups] == [0, 2]


@pytest.mark.asyncio
async def test_low_vector_score_link_seed_is_not_filtered_before_rerank(monkeypatch):
    fs = Files({PERSON: memory(links=[relation(PERSON, RACE)]), RACE: memory()})
    storage = Storage([row(PERSON, -0.3)], [row(RACE, abstract="race evidence")])
    search, _model = retriever(monkeypatch, storage, fs)

    result = await search_with_memory_links(
        search,
        fs,
        TypedQuery("race", ContextType.MEMORY, ""),
        ctx(),
        limit=1,
        score_threshold=0.5,
    )
    assert [match.uri for match in result.matched_contexts] == [RACE]


@pytest.mark.asyncio
async def test_link_rerank_preserves_per_pair_token_budget(monkeypatch):
    search, model = retriever(monkeypatch, Storage(), Files({}))
    search.rerank_config.max_input_tokens = 128

    await _rerank_all(search, "question " * 500, [row(PERSON, abstract="answer " * 500)])

    query, documents = model.calls[0]
    assert estimate_text_tokens(query) <= 96
    assert estimate_text_tokens(query) + estimate_text_tokens(documents[0]) <= 128


@pytest.mark.asyncio
async def test_link_pipeline_preserves_keyword_recall_and_scope(monkeypatch):
    fs = Files({PERSON: memory(links=[relation(PERSON, RACE)]), RACE: memory()})
    storage = Storage([row(PERSON)], [row(RACE, abstract="race evidence")])
    search, _model = retriever(monkeypatch, storage, fs)
    scope = {"op": "must", "field": "search_tags", "conds": ["source=allowed"]}

    result = await search_with_memory_links(
        search,
        fs,
        TypedQuery("race", ContextType.MEMORY, "", target_directories=[ROOT]),
        ctx(),
        limit=1,
        search_type="keywords",
        scope_dsl=scope,
        level=[2],
    )

    assert [match.uri for match in result.matched_contexts] == [RACE]
    assert storage.recall_calls == [
        {
            "query": "race",
            "context_type": "memory",
            "target_directories": [ROOT],
            "extra_filter": scope,
            "level": [2],
            "limit": 2,
            "offset": 0,
        }
    ]
    assert storage.lookups[0]["extra_filter"] == And([In("uri", [RACE]), RawDSL(scope)])
