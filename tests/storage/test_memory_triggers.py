# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Triggers augment existing memories without replacing evidence or its ACL."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from openviking.models.embedder.base import EmbedResult
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever, RetrieverMode
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.retrieval_triggers import (
    generate,
    source_hash,
    valid_cached,
    validate_views,
)
from openviking.session.memory.utils.content_visibility import visible_content
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.expr import Contains
from openviking.storage.memory_trigger_index import MemoryTriggerIndex
from openviking.storage.vector_ids import vector_record_id
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking.storage.vikingdb_manager import VikingDBManager
from openviking_cli.retrieve.types import ContextType, TypedQuery
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.memory_config import MemoryConfig
from openviking_cli.utils.config.memory_trigger_config import MemoryTriggerConfig
from openviking_cli.utils.config.rerank_config import RerankConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

EVENT = "viking://user/alice/memories/events/2026/09/21/dinner.md"
PREFERENCE = "viking://user/alice/memories/preferences/food.md"
ENTITY = "viking://user/alice/memories/entities/people/mei.md"
BODY = "# Summary\nAlice is allergic to shrimp.\n# ChatLog\nAlice: I am allergic to shrimp."
VIEWS = [
    {
        "family": "entity",
        "text": "shellfish allergy",
        "anchor": "allergic to shrimp",
        "confidence": 0.9,
    },
    {
        "family": "bridge",
        "text": "choosing a restaurant for a team dinner",
        "anchor": "allergic to shrimp",
        "confidence": 0.9,
    },
]


def context(user="alice", account="test", role=Role.USER):
    return RequestContext(user=UserIdentifier(account_id=account, user_id=user), role=role)


@pytest_asyncio.fixture
async def store(tmp_path):
    manager = VikingDBManager(VectorDBBackendConfig(path=str(tmp_path), dimension=4))
    await manager.create_collection("context", CollectionSchemas.context_collection("context", 4))
    manager.trigger_index = MemoryTriggerIndex(manager, MemoryTriggerConfig(enabled=True))
    aux = manager.trigger_index.store
    await aux.create_collection(
        aux.collection_name, CollectionSchemas.context_collection(aux.collection_name, 4)
    )
    try:
        yield manager
    finally:
        await manager.close()


async def add(store, uri=EVENT, body=BODY, *, triggers=True, ctx=None):
    ctx = ctx or context()
    record = {
        "id": vector_record_id(ctx.account_id, uri, 2),
        "uri": uri,
        "account_id": ctx.account_id,
        "owner_user_id": ctx.user.user_id,
        "context_type": "memory",
        "level": 2,
        "abstract": body,
        "vector": [0.0, 1.0, 0.0, 0.0],
        "search_tags": ["dinner"],
    }
    if triggers:
        record["_memory_trigger_embeddings"] = [
            {
                "view": view,
                "source_sha256": source_hash(body),
                "vector": [1.0, 0.0, 0.0, 0.0],
                "sparse_vector": {},
            }
            for view in VIEWS
        ]
    await store.upsert(record, ctx=ctx)
    return record["id"]


def filesystem(mapping=None):
    mapping = mapping or {EVENT: BODY, PREFERENCE: "Alice prefers quiet restaurants.", ENTITY: BODY}

    async def read(uri, **kwargs):
        if uri not in mapping:
            raise FileNotFoundError(uri)
        return mapping[uri]

    return SimpleNamespace(read_file=AsyncMock(side_effect=read))


async def recall(store, fs=None, ctx=None, **kwargs):
    return await store.trigger_index.search(
        ctx=ctx or context(),
        fs=fs or filesystem(),
        query_vector=[1.0, 0.0, 0.0, 0.0],
        context_type="memory",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_generation_is_cached_hidden_and_bound_to_current_body():
    memory = MemoryFile(uri=EVENT, content=BODY)
    model = SimpleNamespace(
        model="test", get_completion_async=AsyncMock(return_value=json.dumps({"views": VIEWS}))
    )
    config = SimpleNamespace(
        memory=MemoryConfig(triggers=MemoryTriggerConfig(enabled=True)),
        vlm=SimpleNamespace(get_vlm_instance=lambda: model),
    )
    assert await generate(memory, config=config) == VIEWS
    assert await generate(memory, config=config) == VIEWS
    assert model.get_completion_async.call_count == 1
    raw = MemoryFileUtils.write(memory)
    assert visible_content(raw, uri=EVENT).strip() == BODY
    assert "team dinner" not in visible_content(raw, uri=EVENT)
    memory.content = "Alice is no longer allergic."
    assert valid_cached(memory, config.memory.triggers) is None


@pytest.mark.parametrize(
    "change",
    [
        {"anchor": "made-up fact"},
        {"family": "topic"},
        {"confidence": float("nan")},
        {"confidence": True},
        {"text": " "},
        {"text": "x" * 501},
    ],
)
def test_invalid_cues_are_not_indexable(change):
    with pytest.raises(ValueError):
        validate_views([{**VIEWS[0], **change}], BODY, "events", MemoryTriggerConfig())


def test_event_only_views_do_not_reclassify_entities_or_preferences():
    view = {**VIEWS[0], "family": "horizon"}
    assert validate_views([view], BODY, "events", MemoryTriggerConfig())
    for kind in ("entities", "preferences"):
        with pytest.raises(ValueError):
            validate_views([view], BODY, kind, MemoryTriggerConfig())


@pytest.mark.asyncio
async def test_invalid_generation_retries_only_invalid_outputs():
    good = json.dumps({"views": VIEWS})
    model = SimpleNamespace(
        model="test", get_completion_async=AsyncMock(side_effect=["broken", good])
    )
    config = SimpleNamespace(
        memory=MemoryConfig(), vlm=SimpleNamespace(get_vlm_instance=lambda: model)
    )
    memory = MemoryFile(uri=EVENT, content=BODY)
    assert await generate(memory, config=config) == VIEWS
    assert await generate(memory, config=config) == VIEWS
    assert model.get_completion_async.call_count == 2


@pytest.mark.asyncio
async def test_invalid_anchor_does_not_discard_other_grounded_views():
    bad = {**VIEWS[0], "anchor": "Alice is allergic. I am allergic to shrimp."}
    model = SimpleNamespace(
        model="test",
        get_completion_async=AsyncMock(return_value=json.dumps({"views": [*VIEWS, bad]})),
    )
    config = SimpleNamespace(
        memory=MemoryConfig(), vlm=SimpleNamespace(get_vlm_instance=lambda: model)
    )
    memory = MemoryFile(uri=EVENT, content=BODY)
    assert await generate(memory, config=config) == VIEWS
    assert valid_cached(memory, config.memory.triggers) == VIEWS
    assert model.get_completion_async.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("uri", [EVENT, ENTITY, PREFERENCE])
async def test_auxiliary_vectors_point_to_original_files(store, uri):
    key = await add(store, uri=uri)
    primary = (await store.get_strict([key], ctx=context()))[0]
    assert primary["vector"] == [0.0, 1.0, 0.0, 0.0]
    assert primary["abstract"] == BODY
    hits = await recall(store, fs=filesystem({uri: BODY}))
    assert [h["uri"] for h in hits] == [uri]
    assert hits[0]["abstract"] == BODY
    assert await store.trigger_index.store.count(ctx=context()) == 2


@pytest.mark.asyncio
async def test_scope_and_current_files_are_rechecked(store):
    await add(store)
    assert not await recall(store, ctx=context(user="bob"))
    assert not await recall(store, ctx=context(account="elsewhere"))
    assert not await recall(store, target_directories=["viking://user/alice/memories/preferences"])
    assert not await recall(store, extra_filter=Contains("search_tags", "missing"))
    assert not await recall(store, fs=filesystem({EVENT: BODY + "\nCorrection."}))
    assert await recall(store, extra_filter=Contains("search_tags", "dinner"))


@pytest.mark.asyncio
async def test_revoked_access_and_deleted_primary_cannot_return_auxiliary_hit(store):
    key = await add(store)
    denied = filesystem()
    denied.read_file.side_effect = PermissionError()
    assert not await recall(store, fs=denied)
    await VikingVectorIndexBackend.delete(store, [key], ctx=context())
    assert await store.trigger_index.store.count(ctx=context()) == 2
    assert not await recall(store)


@pytest.mark.asyncio
async def test_tag_refresh_preserves_vectors_and_edit_invalidates(store):
    key = await add(store)
    await store.update_search_tags(EVENT, ["updated"], mode="replace", ctx=context())
    assert await recall(store, extra_filter=Contains("search_tags", "updated"))
    await store.update({"id": key, "abstract": "Changed evidence"}, ctx=context())
    assert await store.trigger_index.store.count(ctx=context()) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "uri", "uris", "user", "account", "clear"])
async def test_lifecycle_cleanup(store, operation):
    key = await add(store)
    root = context(role=Role.ROOT)
    if operation == "delete":
        await store.delete([key], ctx=context())
    elif operation == "uri":
        await store.remove_by_uri(EVENT, ctx=context())
    elif operation == "uris":
        await store.delete_uris(context(), [EVENT])
    elif operation == "user":
        await store.delete_user_data("test", "alice", ctx=root)
    elif operation == "account":
        await store.delete_account_data("test", ctx=root)
    else:
        await store.clear(ctx=context())
    assert await store.trigger_index.store.count(ctx=context()) == 0


@pytest.mark.asyncio
async def test_copy_move_retain_all_views_without_id_collisions(store):
    await add(store)
    copied, moved = EVENT.replace("dinner.md", "copy.md"), EVENT.replace("dinner.md", "moved.md")
    await store.copy_uri_mapping(context(), EVENT, copied)
    assert await store.trigger_index.store.count(ctx=context()) == 4
    await store.update_uri_mapping(context(), copied, moved)
    assert await store.trigger_index.store.count(ctx=context()) == 4
    hits = await recall(store, fs=filesystem({EVENT: BODY, moved: BODY}))
    assert {h["uri"] for h in hits} == {EVENT, moved}


@pytest.mark.asyncio
async def test_native_and_trigger_candidates_rrf_and_user_topk(store, monkeypatch):
    await add(store)
    pref_body = "Alice prefers quiet restaurants."
    await add(store, uri=PREFERENCE, body=pref_body, triggers=False)
    fs = filesystem()
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    embedder = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[1.0, 0.0, 0.0, 0.0])),
    )
    # Normal configured threshold 0.1 must not discard RRF scores (max 2/61).
    retriever = HierarchicalRetriever(store, embedder, rerank_config=RerankConfig())
    assert retriever._rerank_client is None
    query = TypedQuery(query="Where should we eat?", context_type=ContextType.MEMORY, intent="")
    result = await retriever.retrieve(query, context(), limit=2, mode=RetrieverMode.QUICK)
    assert {x.uri for x in result.matched_contexts} == {PREFERENCE, EVENT}
    assert result.matched_contexts[0].uri == EVENT  # Supported by both lanes.
    assert embedder.embed_async.call_count == 1
    assert {x.abstract for x in result.matched_contexts} == {BODY, pref_body}
    short = await retriever.retrieve(query, context(), limit=1, mode=RetrieverMode.QUICK)
    assert [x.uri for x in short.matched_contexts] == [EVENT]
    filtered = await retriever.retrieve(
        query, context(), mode=RetrieverMode.QUICK, score_threshold=0.05
    )
    assert filtered.matched_contexts == []
    store.trigger_index.settings.recall_enabled = False
    ordinary = await retriever.retrieve(query, context(), limit=2, mode=RetrieverMode.QUICK)
    # Both canonical vectors are orthogonal to the query; ordinary QUICK keeps
    # its original strict > 0 threshold and does not return the trigger hits.
    assert ordinary.matched_contexts == []


@pytest.mark.asyncio
async def test_trigger_fusion_never_calls_a_configured_reranker(store, monkeypatch):
    await add(store)
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", filesystem)
    embedder = SimpleNamespace(
        prepare_embedding_input=lambda text: text,
        embed_async=AsyncMock(return_value=EmbedResult(dense_vector=[1.0, 0.0, 0.0, 0.0])),
    )
    retriever = HierarchicalRetriever(store, embedder)
    rerank = Mock(side_effect=AssertionError("Unexpected rerank request"))
    retriever._rerank_client = SimpleNamespace(rerank_batch=rerank)
    query = TypedQuery(query="restaurant", context_type=ContextType.MEMORY, intent="")
    result = await retriever.retrieve(query, context(), mode=RetrieverMode.QUICK)
    assert [x.uri for x in result.matched_contexts] == [EVENT]
    rerank.assert_not_called()
