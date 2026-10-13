# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filesystem associations: persistence, concurrency, stale data and isolation."""

import asyncio
import json
from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest

from openviking.retrieve.memory_association import store as store_module
from openviking.retrieve.memory_association.store import FileAssociationStore, cue_directory
from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import ReindexExecutor
from openviking.storage.viking_fs import VikingFS, init_viking_fs
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.retrieval_config import MemoryAssociationConfig, RetrievalConfig

ROOT = "viking://user/alice/memories"
A = ROOT + "/events/a.md"
B = ROOT + "/events/b.md"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    ctx = RequestContext(user=UserIdentifier("tenant", "alice"), role=Role.ROOT)
    client = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="memory"))
    )
    fs = VikingFS(agfs=client)
    store = FileAssociationStore(fs, MemoryAssociationConfig(enabled=True))
    monkeypatch.setattr(
        store_module,
        "extract_cues_batch",
        lambda texts, **kwargs: [[("PROPER", text)] if text else [] for text in texts],
    )
    return fs, store, ctx


@pytest.mark.parametrize(
    "name", ["Caroline", "classic rock", "张三", "a/b", ".", "..", "%", "a#b?c", "a\x00b", "C:"]
)
def test_readable_safe_reversible_names(name):
    directory = cue_directory(name)
    assert unquote(directory) == name
    assert "/" not in directory and not directory.startswith(".")
    if name in {"Caroline", "classic rock", "张三"}:
        assert directory == name


async def test_parallel_same_cue_uses_one_meta_and_keeps_both_sources(fixture):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await fs.write_file(B, "caroline", ctx=ctx)
    await asyncio.gather(store.refresh(A, ctx), store.refresh(B, ctx))
    state = json.loads(await fs.read_file(ROOT + "/.association/.index.json", ctx=ctx))
    assert len(state["cues"]) == 1
    directory = state["cues"]["caroline"]
    meta = json.loads(await fs.read_file(ROOT + f"/.association/{directory}/meta.json", ctx=ctx))
    assert {ref["uri"] for ref in meta["memories"]} == {A, B}
    assert all(ref["source_version"] == 1 for ref in meta["memories"])
    files = await fs.tree(ROOT + "/.association", output="original", node_limit=None, ctx=ctx)
    assert [e["name"] for e in files if e["name"].endswith(".json")] == ["meta.json"]
    assert len(await store.search("CAROLINE", [ROOT], ctx)) == 2


async def test_update_delete_and_stale_reference_filtering(fixture):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await store.refresh(A, ctx)
    await fs.write_file(A, "Melanie", ctx=ctx)
    assert await store.search("Caroline", [ROOT], ctx) == []
    await store.refresh(A, ctx)
    assert not await fs.exists(ROOT + "/.association/Caroline", ctx=ctx)
    assert (await store.search("Melanie", [ROOT], ctx))[0]["memory_uri"] == A
    await fs.remove_files(A, ctx=ctx)
    assert await store.search("Melanie", [ROOT], ctx) == []
    await store.refresh(A, ctx)
    assert not await fs.exists(ROOT + "/.association/Melanie", ctx=ctx)


@pytest.mark.parametrize("name", ["张三", "classic rock", "a/b", "..", "C:"])
async def test_names_are_persisted_readably_without_traversal(fixture, name):
    fs, store, ctx = fixture
    await fs.write_file(A, name, ctx=ctx)
    await store.refresh(A, ctx)
    meta = json.loads(
        await fs.read_file(ROOT + "/.association/" + cue_directory(name) + "/meta.json", ctx=ctx)
    )
    assert meta["cue"] == name
    assert meta["memories"][0]["uri"] == A
    assert len(await store.search(name, [ROOT], ctx)) == 1


async def test_self_peer_and_other_user_have_separate_associations(fixture):
    fs, store, ctx = fixture
    peer = "viking://user/alice/peers/friend/memories"
    other = "viking://user/bob/memories"
    for root in (ROOT, peer, other):
        uri = root + "/events/a.md"
        await fs.write_file(uri, "Caroline", ctx=ctx)
        await store.refresh(uri, ctx)
    for root in (ROOT, peer, other):
        hits = await store.search("Caroline", [root], ctx)
        assert {hit["memory_uri"] for hit in hits} == {root + "/events/a.md"}


async def test_tree_sync_preserves_associations(fixture):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await store.refresh(A, ctx)
    source = "viking://temp/import"
    await fs.write_file(source + "/events/a.md", "Caroline", ctx=ctx)
    # A staged association subtree is also excluded at a memory destination.
    await fs.write_file(source + "/.association/injected/meta.json", "{}", ctx=ctx)
    await fs.sync_tree(source, ROOT, ctx=ctx)
    assert len(await store.search("Caroline", [ROOT], ctx)) == 1
    assert not await fs.exists(ROOT + "/.association/injected", ctx=ctx)


async def test_native_reindex_only_vectorizes_primary_memories(fixture, monkeypatch):
    from openviking.service.reindex_executor import _ReindexCounters

    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await store.refresh(A, ctx)
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: fs)
    executor = ReindexExecutor()
    upsert = AsyncMock()
    monkeypatch.setattr(executor, "_upsert_context", upsert)
    monkeypatch.setattr(executor, "_fetch_existing_record", AsyncMock(return_value=None))
    monkeypatch.setattr(executor, "_best_file_summary", AsyncMock(return_value=""))
    await executor._reindex_memory_vectors(uri=ROOT, ctx=ctx, counters=_ReindexCounters())
    uris = [call.kwargs["uri"] for call in upsert.await_args_list]
    assert A in uris
    assert not any("/.association" in uri for uri in uris)


async def test_enqueue_error_preserves_primary_write(fixture, monkeypatch):
    from openviking.retrieve.memory_association import runtime
    from openviking.storage.queuefs.queue_manager import init_queue_manager

    fs, _, ctx = fixture
    queues = init_queue_manager(fs.agfs)
    monkeypatch.setattr(runtime, "get_nlp_full", lambda name=None: object())
    enabled = init_viking_fs(
        fs.agfs, retrieval_config=RetrievalConfig(memory_association={"enabled": True})
    )
    enqueue = AsyncMock(side_effect=OSError("queue unavailable"))
    monkeypatch.setattr(queues, "enqueue", enqueue)
    await enabled.write_file(A, "Caroline", ctx=ctx)
    assert await enabled.read_file(A, ctx=ctx) == "Caroline"
    assert enqueue.await_count == 1
    assert not await enabled.exists(ROOT + "/.association", ctx=ctx)


async def test_retry_only_requeues_derived_work_and_retains_exhausted_errors(monkeypatch):
    from types import SimpleNamespace

    from openviking.retrieve.memory_association.runtime import QUEUE, AssociationProcessor
    from openviking.storage.queuefs.process_result import ProcessOutcome

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    store = SimpleNamespace(refresh_tree=AsyncMock(side_effect=OSError("transient")))
    queues = SimpleNamespace(enqueue=AsyncMock())
    processor = AssociationProcessor(store, queues)
    payload = {"uri": A, "account_id": "tenant", "user_id": "alice"}
    result = await processor.on_dequeue({"data": json.dumps(payload)})
    assert result.outcome is ProcessOutcome.REQUEUED
    queues.enqueue.assert_awaited_once_with(QUEUE, {**payload, "attempt": 1})
    queues.enqueue.reset_mock()
    with pytest.raises(OSError):
        await processor.on_dequeue({"data": {**payload, "attempt": 7}})
    queues.enqueue.assert_not_awaited()


async def test_journal_replays_interrupted_multi_file_update(fixture, monkeypatch):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await store.refresh(A, ctx)
    await fs.write_file(A, "Melanie", ctx=ctx)
    write = store._write_json

    async def fail_manifest(uri, *args):
        if uri.endswith("/.index.json"):
            raise OSError("injected manifest write failure")
        await write(uri, *args)

    monkeypatch.setattr(store, "_write_json", fail_manifest)
    with pytest.raises(OSError):
        await store.refresh(A, ctx)
    assert await fs.exists(ROOT + "/.association/.pending.json", ctx=ctx)
    monkeypatch.setattr(store, "_write_json", write)
    assert (await store.search("Melanie", [ROOT], ctx))[0]["memory_uri"] == A
    assert not await fs.exists(ROOT + "/.association/.pending.json", ctx=ctx)


async def test_bad_json_is_not_silently_replaced(fixture):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await store.refresh(A, ctx)
    uri = ROOT + "/.association/.index.json"
    await fs.write_file(uri, "{broken", ctx=ctx)
    with pytest.raises(json.JSONDecodeError):
        await store.refresh(A, ctx)
    assert await fs.read_file(uri, ctx=ctx) == "{broken"


async def test_scopes_and_live_acl_are_checked(fixture, monkeypatch):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await store.refresh(A, ctx)
    assert await store.search("Caroline", [ROOT + "/preferences"], ctx) == []
    other = RequestContext(user=UserIdentifier("other", "alice"), role=Role.ROOT)
    assert await store.search("Caroline", [ROOT], other) == []
    monkeypatch.setattr(
        fs, "_ensure_retrieval_scope", AsyncMock(side_effect=PermissionDeniedError("denied"))
    )
    assert await store.search("Caroline", [ROOT], ctx) == []


async def test_delayed_extract_cannot_overwrite_a_new_source(fixture, monkeypatch):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    original = store._locked

    def lock_after_new_write(*args, **kwargs):
        fs.agfs.write(fs._uri_to_path(A, ctx=ctx), b"Melanie", ctx={"account_id": ctx.account_id})
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_locked", lock_after_new_write)
    with pytest.raises(RuntimeError, match="source changed"):
        await store.refresh(A, ctx)
    monkeypatch.setattr(store, "_locked", original)
    await store.refresh(A, ctx)
    assert await store.search("Caroline", [ROOT], ctx) == []
    assert len(await store.search("Melanie", [ROOT], ctx)) == 1


async def test_disabled_uses_original_class_and_does_not_load_optional_runtime(
    fixture, monkeypatch
):
    fs, _, ctx = fixture
    from openviking.retrieve.memory_association import runtime

    monkeypatch.setattr(runtime, "get_nlp_full", lambda: pytest.fail("NLP loaded while disabled"))
    result = init_viking_fs(fs.agfs, retrieval_config=RetrievalConfig())
    assert type(result) is VikingFS
    assert not hasattr(result, "memory_association")
    await result.write_file(A, "Caroline", ctx=ctx)
    assert not await result.exists(ROOT + "/.association", ctx=ctx)


async def test_reindex_explicitly_rejects_and_filters_associations(fixture):
    fs, store, ctx = fixture
    await fs.write_file(A, "Caroline", ctx=ctx)
    await store.refresh(A, ctx)
    executor = ReindexExecutor()
    with pytest.raises(InvalidArgumentError, match="cannot be reindexed"):
        executor._infer_target_type(ROOT + "/.association/Caroline/meta.json")
    entries = await executor._tree_all(fs, ROOT, show_all_hidden=True, ctx=ctx)
    assert any(entry["uri"] == A for entry in entries)
    assert not any("/.association" in entry["uri"] for entry in entries)
