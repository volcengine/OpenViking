# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Entity association regressions using real local vector storage, no LLM."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.entity_linking import index as index_module
from openviking.retrieve.entity_linking._entity_rules import (
    _EntityCandidate,
    _resolve_candidates,
    extract_entities_batch,
)
from openviking.retrieve.entity_linking.index import source_fingerprint
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas, TextEmbeddingHandler
from openviking.storage.expr import Eq
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.retrieval_config import EntityLinkingConfig, RetrievalConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


class Embedder:
    def __init__(self):
        self.inputs = []

    def prepare_embedding_input(self, text):
        return text

    async def embed_async(self, text, is_query=False):
        self.inputs.append((text, is_query))
        vectors = {"Alice": [1, 0, 0, 0], "John": [0, 1, 0, 0], "Pizza": [0, 0, 1, 0]}
        return EmbedResult(dense_vector=vectors.get(text, [1, 0, 0, 0]))


def ctx(user="user1", account="account1"):
    return RequestContext(user=UserIdentifier(account, user), role=Role.USER)


def memory(name, *, user="user1", account="account1", abstract=None, md5="version1"):
    return {
        "id": f"{account}-{user}-{name}",
        "uri": f"viking://user/{user}/memories/events/{name}.md",
        "account_id": account,
        "owner_user_id": user,
        "context_type": "memory",
        "abstract": abstract or name,
        "md5": md5,
        "vector": [1, 0, 0, 0],
        "level": 2,
    }


@pytest_asyncio.fixture
async def storage(tmp_path, vector_backend_factory, monkeypatch):
    config = VectorDBBackendConfig(backend="local", path=str(tmp_path), dimension=4)
    store = vector_backend_factory(config)
    assert await store.create_collection(
        "context", CollectionSchemas.context_collection("context", 4)
    )
    monkeypatch.setattr(
        index_module,
        "extract_entities_batch",
        lambda texts: [
            [("PROPER", n) for n in ["Alice", "John", "Pizza"] if n in t] for t in texts
        ],
    )
    yield store
    await store.close()


async def add(store, record, text="Alice"):
    context = ctx(record["owner_user_id"], record["account_id"])
    await store.upsert(record, ctx=context)
    await store.entity_link_index.replace(record, text, Embedder(), context, EntityLinkingConfig())


@pytest.mark.asyncio
async def test_association_matches_existing_memory_without_creating_entity_card(storage):
    record = memory("favorite-pizza")
    await add(storage, record, "Alice likes Pizza")
    boosts = await storage.entity_link_index.boosts(
        "Alice", [record], Embedder(), ctx(), EntityLinkingConfig()
    )
    assert boosts[record["uri"]] == pytest.approx(0.5)
    assert await storage.count(ctx=ctx()) == 1


@pytest.mark.asyncio
async def test_user_and_candidate_isolation(storage):
    own = memory("own")
    foreign = memory("private", user="user2")
    await add(storage, own, "John")
    await add(storage, foreign, "Alice")
    # A high Alice match outside the authorized recall set must not affect own.
    boosts = await storage.entity_link_index.boosts(
        "Alice", [own], Embedder(), ctx(), EntityLinkingConfig(similarity_threshold=0.9)
    )
    assert boosts == {}


@pytest.mark.asyncio
async def test_entity_threshold_uses_cosine_not_affine_local_score(storage):
    own = memory("orthogonal")
    await add(storage, own, "John")
    assert (
        await storage.entity_link_index.boosts(
            "Alice", [own], Embedder(), ctx(), EntityLinkingConfig()
        )
        == {}
    )


@pytest.mark.asyncio
async def test_account_isolation(storage):
    own = memory("same")
    foreign = memory("same", account="account2")
    await add(storage, own, "John")
    await add(storage, foreign, "Alice")
    boosts = await storage.entity_link_index.boosts(
        "Alice", [own], Embedder(), ctx(), EntityLinkingConfig(similarity_threshold=0.9)
    )
    assert boosts == {}


@pytest.mark.asyncio
async def test_update_removes_previous_entity_and_stale_fingerprint(storage):
    old = memory("changing", abstract="Alice likes Pizza")
    await add(storage, old, "Alice")
    new = {**old, "md5": "version2", "abstract": "John likes Pizza"}
    await storage.upsert(new, ctx=ctx())
    assert (
        await storage.entity_link_index.boosts(
            "Alice", [new], Embedder(), ctx(), EntityLinkingConfig()
        )
        == {}
    )
    await storage.entity_link_index.replace(new, "John", Embedder(), ctx(), EntityLinkingConfig())
    backend = await storage.entity_link_index._backend(ctx())
    rows = await backend.strict_query(filter=Eq("uri", old["uri"]), limit=10)
    assert len(rows) == 1
    assert rows[0]["name"] == "john"


@pytest.mark.asyncio
async def test_late_queue_item_does_not_replace_newer_links(storage):
    old = memory("changing", abstract="Alice")
    new = {**old, "abstract": "John", "md5": "version2"}
    await add(storage, new, "John")
    await storage.entity_link_index.replace(old, "Alice", Embedder(), ctx(), EntityLinkingConfig())
    boosts = await storage.entity_link_index.boosts(
        "John", [new], Embedder(), ctx(), EntityLinkingConfig()
    )
    assert boosts[new["uri"]] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_concurrent_memories_do_not_overwrite_shared_entity_links(storage):
    records = [memory(f"event-{i}") for i in range(8)]
    await asyncio.gather(*(add(storage, record) for record in records))
    boosts = await storage.entity_link_index.boosts(
        "Alice", records, Embedder(), ctx(), EntityLinkingConfig()
    )
    assert set(boosts) == {record["uri"] for record in records}


@pytest.mark.asyncio
async def test_deletion_during_derived_write_cannot_resurrect_entity_links(storage, monkeypatch):
    record = memory("racing-delete")
    await storage.upsert(record, ctx=ctx())
    backend = await storage.entity_link_index._backend(ctx(), create=True)
    original = backend.upsert_many

    async def delete_before_write(links):
        await storage.strict_delete([record["id"]], ctx=ctx())
        return await original(links)

    monkeypatch.setattr(backend, "upsert_many", delete_before_write)
    await storage.entity_link_index.replace(
        record, "Alice", Embedder(), ctx(), EntityLinkingConfig()
    )
    assert await backend.strict_count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "strict_delete", "remove_by_uri", "delete_uris"])
async def test_native_deletion_purges_auxiliary_links(storage, operation):
    record = memory("delete-me")
    await add(storage, record)
    if operation == "remove_by_uri":
        await storage.remove_by_uri(record["uri"], ctx=ctx())
    elif operation == "delete_uris":
        await storage.delete_uris(ctx(), [record["uri"]])
    else:
        await getattr(storage, operation)([record["id"]], ctx=ctx())
    backend = await storage.entity_link_index._backend(ctx())
    assert await backend.strict_count() == 0


@pytest.mark.asyncio
async def test_root_user_deletion_keeps_other_users_links(storage):
    await add(storage, memory("own"))
    await add(storage, memory("other", user="user2"))
    await storage.delete_user_data(
        "account1",
        "user1",
        ctx=RequestContext(user=UserIdentifier("account1", "root"), role=Role.ROOT),
    )
    backend = await storage.entity_link_index._backend(ctx())
    rows = await backend.strict_query(limit=10)
    assert len(rows) == 1
    assert rows[0]["owner_user_id"] == "user2"


@pytest.mark.asyncio
async def test_root_account_deletion_keeps_other_accounts_links(storage):
    await add(storage, memory("own"))
    await add(storage, memory("other", account="account2"))
    await storage.delete_account_data(
        "account1", ctx=RequestContext(user=UserIdentifier("account2", "root"), role=Role.ROOT)
    )
    assert await (await storage.entity_link_index._backend(ctx())).strict_count() == 0
    other = await storage.entity_link_index._backend(ctx(account="account2"))
    assert await other.strict_count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("move", [False, True])
async def test_transfer_reuses_vectors_and_rewrites_owner_without_embedding(storage, move):
    record = memory("source")
    await add(storage, record)
    destination = "viking://user/user2/memories/events/target.md"
    operation = storage.update_uri_mapping if move else storage.copy_uri_mapping
    await operation(ctx(), record["uri"], destination)
    targets = await storage.query(filter=Eq("uri", destination), ctx=ctx("user2"))
    boosts = await storage.entity_link_index.boosts(
        "Alice", targets, Embedder(), ctx("user2"), EntityLinkingConfig()
    )
    assert boosts[destination] == pytest.approx(0.5)
    auxiliary = await storage.entity_link_index._backend(ctx())
    links = await auxiliary.strict_query(filter=Eq("uri", destination), limit=10)
    assert links[0]["owner_user_id"] == "user2"
    assert bool(await auxiliary.strict_query(filter=Eq("uri", record["uri"]))) is not move


@pytest.mark.asyncio
async def test_clear_removes_links_only_in_requested_account(storage):
    await add(storage, memory("own"))
    await add(storage, memory("other", account="account2"))
    await storage.clear(ctx=ctx())
    assert await (await storage.entity_link_index._backend(ctx())).strict_count() == 0
    assert (
        await (await storage.entity_link_index._backend(ctx(account="account2"))).strict_count()
        == 1
    )


@pytest.mark.asyncio
async def test_auxiliary_cleanup_error_does_not_replay_primary_deletion(storage, monkeypatch):
    record = memory("delete")
    await add(storage, record)
    monkeypatch.setattr(
        storage.entity_link_index,
        "remove_ids",
        AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )
    assert await storage.strict_delete([record["id"]], ctx=ctx()) == 1
    assert not await storage.get_strict([record["id"]], ctx=ctx())


@pytest.mark.asyncio
async def test_missing_auxiliary_index_does_not_embed_or_create_collection(storage):
    embedder = Embedder()
    assert (
        await storage.entity_link_index.boosts(
            "Alice", [memory("old")], embedder, ctx(), EntityLinkingConfig()
        )
        == {}
    )
    assert embedder.inputs == []
    assert storage.entity_link_index._backends == {}


@pytest.mark.asyncio
async def test_closed_index_cannot_open_a_new_collection(storage):
    await add(storage, memory("closed"))
    await storage.entity_link_index.close()
    with pytest.raises(RuntimeError, match="closing"):
        await storage.entity_link_index._backend(ctx(), create=True)


@pytest.mark.asyncio
async def test_failed_entity_embeddings_keep_primary_memory(storage):
    record = memory("persisted")
    await storage.upsert(record, ctx=ctx())
    embedder = Embedder()
    embedder.embed_async = AsyncMock(side_effect=RuntimeError("technical failure"))
    with pytest.raises(RuntimeError, match="technical failure"):
        await storage.entity_link_index.replace(
            record, "Alice", embedder, ctx(), EntityLinkingConfig()
        )
    assert await storage.get_strict([record["id"]], ctx=ctx())


@pytest.mark.asyncio
async def test_queue_retains_primary_when_auxiliary_index_fails(storage, monkeypatch):
    config = EntityLinkingConfig(enabled=True)
    monkeypatch.setattr(index_module, "linking_config", lambda: config)
    index = storage.entity_link_index
    monkeypatch.setattr(index, "replace", AsyncMock(side_effect=RuntimeError("auxiliary failure")))
    handler = TextEmbeddingHandler(storage, SimpleNamespace(bind=lambda _: Embedder()))
    record = memory("queue")
    result = await handler.on_dequeue(
        {"data": EmbeddingMsg(message="Alice", context_data=record).to_json()}
    )
    assert result.outcome is ProcessOutcome.SUCCESS
    assert await storage.count(ctx=ctx()) == 1


@pytest.mark.asyncio
async def test_queue_indexes_complete_prose_without_persisting_internal_field(storage, monkeypatch):
    monkeypatch.setattr(index_module, "linking_config", lambda: EntityLinkingConfig(enabled=True))
    spy = AsyncMock(wraps=storage.entity_link_index.replace)
    monkeypatch.setattr(storage.entity_link_index, "replace", spy)
    record = {**memory("prose"), "_entity_link_text": "Full memory mentions John"}
    handler = TextEmbeddingHandler(storage, SimpleNamespace(bind=lambda _: Embedder()))
    result = await handler.on_dequeue(
        {"data": EmbeddingMsg(message="Alice", context_data=record).to_json()}
    )
    assert result.outcome is ProcessOutcome.SUCCESS
    assert spy.call_args.args[1] == "Full memory mentions John"
    backend = await storage.entity_link_index._backend(ctx())
    links = await backend.strict_query(limit=10)
    assert links[0]["name"] == "john"
    persisted = await storage.get_strict([spy.call_args.args[0]["id"]], ctx=ctx())
    assert "_entity_link_text" not in persisted[0]


@pytest.mark.asyncio
async def test_entity_boost_changes_topk_but_cannot_rescue_below_threshold(storage):
    alice = {**memory("alice"), "vector": [0.8, 0.6, 0, 0]}
    john = {**memory("john"), "vector": [1, 0, 0, 0]}
    await add(storage, alice, "Alice")
    await add(storage, john, "John")
    query = TypedQuery(
        "Alice", ContextType.MEMORY, "", target_directories=["viking://user/user1/memories"]
    )
    config = EntityLinkingConfig(enabled=True, similarity_threshold=0.9)
    retriever = HierarchicalRetriever(storage, Embedder(), entity_linking_config=config)
    result = await retriever.retrieve(query, ctx(), limit=1)
    assert result.matched_contexts[0].uri == alice["uri"]
    result = await retriever.retrieve(query, ctx(), limit=1, score_threshold=0.95)
    assert result.matched_contexts[0].uri == john["uri"]


@pytest.mark.asyncio
async def test_disabled_linking_makes_no_auxiliary_call(storage, monkeypatch):
    await storage.upsert(memory("plain"), ctx=ctx())
    spy = AsyncMock(side_effect=AssertionError("must not access auxiliary index"))
    monkeypatch.setattr(storage.entity_link_index, "boosts", spy)
    retriever = HierarchicalRetriever(
        storage, Embedder(), entity_linking_config=EntityLinkingConfig()
    )
    await retriever.retrieve(TypedQuery("Alice", ContextType.MEMORY, ""), ctx())
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_preserves_ordinary_ranking(storage, monkeypatch):
    await storage.upsert(memory("plain"), ctx=ctx())
    monkeypatch.setattr(storage.entity_link_index, "boosts", AsyncMock(side_effect=TimeoutError()))
    retriever = HierarchicalRetriever(
        storage, Embedder(), entity_linking_config=EntityLinkingConfig(enabled=True)
    )
    result = await retriever.retrieve(TypedQuery("Alice", ContextType.MEMORY, ""), ctx())
    assert result.matched_contexts


def test_feature_is_opt_in_and_configuration_is_validated():
    assert not RetrievalConfig().entity_linking.enabled
    with pytest.raises(ValueError):
        EntityLinkingConfig(weight=-1)


def test_distinct_substring_names_are_not_merged():
    candidates = [
        _EntityCandidate("PROPER", "Sam", "ner", 0, 1, 0.95, 0),
        _EntityCandidate("PROPER", "Samsung", "ner", 3, 4, 0.95, 0),
    ]
    assert _resolve_candidates(candidates) == [("PROPER", "Sam"), ("PROPER", "Samsung")]


def test_source_fingerprint_changes_with_full_file_md5():
    before = memory("same-preview")
    assert source_fingerprint(before) != source_fingerprint({**before, "md5": "version2"})


def test_missing_spacy_returns_aligned_empty_batches(monkeypatch):
    monkeypatch.setattr("openviking.retrieve.entity_linking.nlp.get_nlp_full", lambda: None)
    assert extract_entities_batch(["Alice", "John"]) == [[], []]


def test_real_spacy_extracts_entities_and_identifiers(monkeypatch):
    spacy = pytest.importorskip("spacy")
    pytest.importorskip("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")
    monkeypatch.setattr("openviking.retrieve.entity_linking.nlp.get_nlp_full", lambda: nlp)
    entities = extract_entities_batch(["Alice works at Microsoft and uses package.module."])
    names = {name for _, name in entities[0]}
    assert "Alice" in names
    assert "Microsoft" in names
    assert "package.module" in names
