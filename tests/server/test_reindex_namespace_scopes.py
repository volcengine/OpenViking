# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Fixed reindex scopes, complete discovery and reusable V inventories."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openviking.service.reindex_executor as module
from openviking.core.namespace import classify_uri, uri_parts
from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import (
    ReindexExecutor,
    _ReindexCounters,
    _ReindexRunContext,
)
from openviking.storage.errors import VikingDBException
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

USER = "viking://user/bob"


def record(uri, level=2):
    return {"uri": uri, "level": level, "abstract": "Existing summary", "md5": "old"}


class ScopeFS:
    def __init__(self, entries):
        self.entries = {uri: {"uri": uri, "isDir": is_dir} for uri, is_dir in entries.items()}
        self.dirs = {"viking://", "viking://user", USER}
        for uri, entry in list(self.entries.items()):
            if entry["isDir"]:
                self.dirs.add(uri)
            current = uri.rsplit("/", 1)[0]
            while current.startswith("viking://") and current != "viking:/":
                self.dirs.add(current)
                current = current.rsplit("/", 1)[0]
        for uri in self.dirs:
            self.entries.setdefault(uri, {"uri": uri, "isDir": True})
        self.calls = []
        self.fault = None

    async def stat(self, uri, **kwargs):
        self.calls.append(("stat", uri, kwargs))
        if self.fault == ("stat", uri):
            raise RuntimeError("S3 stat unavailable")
        if uri not in self.entries:
            raise NotFoundError(uri)
        return self.entries[uri]

    async def exists(self, uri, **kwargs):
        return uri in self.entries

    async def ls(self, uri, *, offset=0, node_limit=1000, **kwargs):
        self.calls.append(("ls", uri, {"offset": offset, "node_limit": node_limit, **kwargs}))
        if self.fault == ("ls", uri):
            raise RuntimeError("S3 listing unavailable")
        if uri not in self.entries:
            raise NotFoundError(uri)
        children = [
            value
            for key, value in sorted(self.entries.items())
            if key.startswith(uri.rstrip("/") + "/") and "/" not in key[len(uri.rstrip("/")) + 1 :]
            and (
                value.get("isDir")
                or kwargs.get("show_all_hidden", False)
                or not key.rsplit("/", 1)[-1].startswith(".")
            )
        ]
        return children[offset : offset + node_limit]

    async def tree(self, uri, **kwargs):
        self.calls.append(("tree", uri, kwargs))
        classification = classify_uri(uri)
        assert uri not in {"viking://", "viking://user", USER, USER + "/peers"}, (
            f"Recursively scanned namespace {uri}"
        )
        assert not classification.is_skill_namespace and not classification.is_memory_root, (
            f"Recursively scanned content container {uri}"
        )
        assert "/privacy" not in uri and "/sessions" not in uri
        return [value for key, value in self.entries.items() if key.startswith(uri + "/")]

    async def read_file(self, uri, **kwargs):
        return "A valid memory body."

    async def abstract(self, uri, **kwargs):
        return ""

    async def overview(self, uri, **kwargs):
        return ""


class ScopeDB:
    def __init__(self, records):
        self.records = {key: dict(value, id=key) for key, value in records.items()}
        self.calls, self.deleted = [], []
        self.failure = None

    async def get_reindex_inventory_under_uri(self, uri, **kwargs):
        self.calls.append((uri, kwargs))
        if self.failure == uri:
            raise RuntimeError("Inventory incomplete")
        root_parts = uri_parts(uri)
        recursive = kwargs.get("recursive", True)
        include_direct_children = kwargs.get("include_direct_children", False)

        def in_scope(record_uri):
            parts = uri_parts(record_uri)
            if parts[: len(root_parts)] != root_parts:
                return False
            depth = len(parts) - len(root_parts)
            return recursive or depth == 0 or (include_direct_children and depth == 1)

        return {
            key: value
            for key, value in self.records.items()
            if in_scope(value["uri"])
        }

    async def strict_delete(self, ids, **kwargs):
        self.deleted.extend(ids)
        for value in ids:
            self.records.pop(value)
        return len(ids)


def executor_for(monkeypatch, entries, records=None):
    fs, db = ScopeFS(entries), ScopeDB(records or {})
    monkeypatch.setattr(module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        module, "get_service", lambda: SimpleNamespace(viking_fs=fs, vikingdb_manager=db)
    )
    executor = ReindexExecutor()
    executor._reindex_rfv = AsyncMock()
    executor._upsert_context = AsyncMock()
    executor._effective_file_vectorization_concurrency = lambda: 2
    ctx = RequestContext(user=UserIdentifier("scopes", "bob"), role=Role.ADMIN)
    run = _ReindexRunContext(ctx=ctx, counters=_ReindexCounters())
    return executor, fs, db, run


@pytest.mark.asyncio
async def test_user_uses_partitioned_inventories_and_only_vectorized_scope_trees(monkeypatch):
    entries = {
        USER + "/resources/a.md": False,
        USER + "/skills/live/SKILL.md": False,
        USER + "/memories/profile.md": False,
        USER + "/memories/events/deep/event.md": False,
        USER + "/memories/preferences/pref.json": False,
        USER + "/sessions/archive/messages.jsonl": False,
        USER + "/privacy/secret.md": False,
        USER + "/unknown/data.md": False,
        USER + "/peers/live/resources/a.md": False,
        USER + "/peers/live/memories/events/live.md": False,
    }
    executor, fs, db, run = executor_for(
        monkeypatch,
        entries,
        {
            "live": record(USER + "/peers/live/memories/events/live.md"),
            "ghost": record(USER + "/peers/gone/memories/events/old.md"),
            "ghost-skill": record(USER + "/skills/gone/SKILL.md"),
        },
    )
    await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)
    assert [uri for uri, _ in db.calls] == [
        USER + "/" + name for name in ("resources", "skills", "memories", "peers")
    ] + [USER, USER + "/unknown"]
    assert Counter(uri for op, uri, _ in fs.calls if op == "tree") == Counter(
        {
            USER + "/memories/events": 1,
            USER + "/memories/preferences": 1,
            USER + "/peers/live/memories/events": 1,
        }
    )
    assert "live" in db.records and db.deleted == ["ghost"]
    by_uri = {call.kwargs["uri"]: call.kwargs for call in executor._reindex_rfv.await_args_list}
    assert USER + "/skills/gone" in by_uri
    assert by_uri[USER + "/skills/gone"]["root_exists"] is False
    assert set(by_uri[USER + "/skills/gone"]["vector_inventory"]) == {"ghost-skill"}
    assert by_uri[USER + "/unknown"]["root_exists"] is True
    assert by_uri[USER + "/unknown"]["root_is_dir"] is True
    assert not any(
        "/privacy" in uri or "/sessions" in uri for _, uri, _ in fs.calls
    )


@pytest.mark.asyncio
async def test_user_memory_root_preserves_direct_hidden_file(monkeypatch):
    memories = USER + "/memories"
    hidden_file = memories + "/.note.md"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {hidden_file: False},
        {"hidden": record(hidden_file)},
    )

    await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)

    assert "hidden" in db.records
    assert db.deleted == []
    assert any(
        op == "ls" and uri == memories and kwargs.get("show_all_hidden") is True
        for op, uri, kwargs in fs.calls
    )


@pytest.mark.asyncio
async def test_user_reindexes_custom_files_and_directories_without_scanning_managed_roots(
    monkeypatch,
):
    persona = USER + "/persona.md"
    notes = USER + "/notes"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            persona: False,
            notes + "/todo.md": False,
            USER + "/sessions/s1/messages.jsonl": False,
            USER + "/privacy/secret.md": False,
            USER + "/settings/user_config.json": False,
        },
        {
            "persona": record(persona),
            "notes-l0": record(notes, 0),
            "notes-l2": record(notes + "/todo.md"),
            "session": record(USER + "/sessions/s1/messages.jsonl"),
            "privacy": record(USER + "/privacy/secret.md"),
            "settings": record(USER + "/settings/user_config.json"),
        },
    )

    await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)

    by_uri = {call.kwargs["uri"]: call.kwargs for call in executor._reindex_rfv.await_args_list}
    assert by_uri[persona]["recursive"] is False
    assert by_uri[persona]["root_is_dir"] is False
    assert set(by_uri[persona]["vector_inventory"]) == {"persona"}
    assert by_uri[notes]["recursive"] is True
    assert by_uri[notes]["root_is_dir"] is True
    assert set(by_uri[notes]["vector_inventory"]) == {"notes-l0", "notes-l2"}
    inventory_roots = [uri for uri, _ in db.calls]
    assert USER in inventory_roots
    assert notes in inventory_roots
    assert not any(
        "/sessions" in uri or "/privacy" in uri or "/settings" in uri
        for uri in inventory_roots
    )
    assert not any(
        "/sessions" in uri or "/privacy" in uri or "/settings" in uri
        for op, uri, _ in fs.calls
        if op == "tree"
    )


@pytest.mark.asyncio
async def test_user_discovers_vector_only_custom_file_and_directory_roots(monkeypatch):
    persona = USER + "/persona.md"
    project = USER + "/old-project"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {},
        {
            "persona": record(persona),
            "project-l0": record(project, 0),
            "project-l2": record(project + "/docs/a.md"),
        },
    )

    await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)

    by_uri = {call.kwargs["uri"]: call.kwargs for call in executor._reindex_rfv.await_args_list}
    assert by_uri[persona]["root_exists"] is False
    assert by_uri[persona]["root_is_dir"] is False
    assert by_uri[persona]["formal_entries"] == []
    assert set(by_uri[persona]["vector_inventory"]) == {"persona"}
    assert by_uri[project]["root_exists"] is False
    assert by_uri[project]["root_is_dir"] is True
    assert by_uri[project]["formal_entries"] == []
    assert set(by_uri[project]["vector_inventory"]) == {"project-l0", "project-l2"}


@pytest.mark.asyncio
async def test_custom_inventory_failure_precedes_all_user_scope_mutations(monkeypatch):
    notes = USER + "/notes"
    executor, fs, db, run = executor_for(monkeypatch, {notes + "/todo.md": False})
    db.failure = notes

    with pytest.raises(RuntimeError, match="Inventory incomplete"):
        await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)

    executor._reindex_rfv.assert_not_awaited()
    executor._upsert_context.assert_not_awaited()
    assert not db.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vectors_only", "semantic_and_vectors"])
async def test_missing_self_memory_root_deletes_all_physical_ids(monkeypatch, mode):
    root = USER + "/memories"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {},
        {
            "root-l0": record(root, 0),
            "root-l1": record(root, 1),
            "file-a": record(root + "/events/a.md"),
            "file-b": record(root + "/events/a.md"),
            "chunk": record(root + "/events/a.md#chunk_0000"),
        },
    )
    executor._enqueue_reindex_semantic_message = AsyncMock()
    await executor._reindex_user_namespace(uri=USER, mode=mode, run=run)
    assert set(db.deleted) == {"root-l0", "root-l1", "file-a", "file-b", "chunk"}
    assert run.counters.deleted_records == 5
    executor._upsert_context.assert_not_awaited()
    executor._enqueue_reindex_semantic_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_root_files_and_literal_chunk_names_share_inventory(monkeypatch):
    root = USER + "/memories"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            root + "/profile.md": False,
            root + "/real#chunk_0000": False,
            root + "/events/live.md": False,
        },
        {
            "profile": record(root + "/profile.md"),
            "chunk": record(root + "/profile.md#chunk_0000"),
            "literal": record(root + "/real#chunk_0000"),
            "gone": record(root + "/gone.md"),
            "gone-shard": record(root + "/old/old.md"),
        },
    )
    await executor._reindex_memory(uri=root, mode="vectors_only", run=run)
    assert len(db.calls) == 1 and db.calls[0][0] == root
    assert set(db.deleted) == {"gone", "gone-shard", "chunk"}
    assert {"profile", "literal"} <= set(db.records)
    assert [uri for op, uri, _ in fs.calls if op == "tree"] == [root + "/events"]


@pytest.mark.asyncio
async def test_direct_memory_subtree_does_not_expand_to_user_or_siblings(monkeypatch):
    root = USER + "/memories/events"
    executor, fs, db, run = executor_for(monkeypatch, {root + "/live.md": False})
    await executor._reindex_memory(uri=root, mode="vectors_only", run=run)
    assert [uri for uri, _ in db.calls] == [root]
    assert [uri for op, uri, _ in fs.calls if op == "tree"] == [root]
    assert not [uri for op, uri, _ in fs.calls if op == "ls"]


@pytest.mark.asyncio
async def test_memory_inventory_failure_precedes_semantic_mutation(monkeypatch):
    root = USER + "/memories"
    executor, fs, db, run = executor_for(monkeypatch, {root + "/events/live.md": False})
    db.failure = root
    executor._enqueue_reindex_semantic_message = AsyncMock()
    with pytest.raises(RuntimeError, match="Inventory incomplete"):
        await executor._reindex_memory(uri=root, mode="semantic_and_vectors", run=run)
    executor._enqueue_reindex_semantic_message.assert_not_awaited()
    executor._upsert_context.assert_not_awaited()
    assert not db.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["resources", "skills", "memories", "peers"])
async def test_user_inventory_failure_precedes_all_scope_mutations(monkeypatch, prefix):
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            USER + "/resources/live.md": False,
            USER + "/memories/events/live.md": False,
        },
    )
    db.failure = USER + "/" + prefix
    with pytest.raises(RuntimeError, match="Inventory incomplete"):
        await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)
    executor._reindex_rfv.assert_not_awaited()
    executor._upsert_context.assert_not_awaited()
    assert not db.deleted


@pytest.mark.asyncio
async def test_user_shallow_discovery_failure_does_not_delete(monkeypatch):
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            USER + "/resources/live.md": False,
            USER + "/skills/live/SKILL.md": False,
        },
        {"ghost": record(USER + "/memories/gone.md")},
    )
    fs.fault = ("ls", USER + "/skills")
    with pytest.raises(RuntimeError, match="listing unavailable"):
        await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)
    executor._reindex_rfv.assert_not_awaited()
    assert not db.deleted


@pytest.mark.asyncio
async def test_shallow_skill_discovery_consumes_all_pages_and_reuses_one_inventory(monkeypatch):
    root = USER + "/skills"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            root + "/" + name + "/SKILL.md": False
            for name in ("one", "two", "three", "four", "five")
        },
    )
    monkeypatch.setattr(ReindexExecutor, "_NAMESPACE_LS_PAGE_SIZE", 2, raising=False)
    await executor._reindex_skill_namespace(uri=root, mode="vectors_only", run=run)
    assert len(db.calls) == 1
    assert executor._reindex_rfv.await_count == 5
    assert [kwargs["offset"] for op, uri, kwargs in fs.calls if op == "ls" and uri == root] == [
        0,
        2,
        4,
    ]


@pytest.mark.asyncio
async def test_global_dispatch_uses_shared_roots_and_shallow_users(monkeypatch):
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            "viking://resources/live.md": False,
            "viking://agent/skills/shared/SKILL.md": False,
            "viking://user/carol/memories/events/live.md": False,
        },
    )
    executor._reindex_user_namespace = AsyncMock()
    await executor._reindex_global_namespace(uri="viking://", mode="vectors_only", run=run)
    assert {call.kwargs["uri"] for call in executor._reindex_user_namespace.await_args_list} == {
        USER,
        "viking://user/carol",
    }
    assert {uri for uri, _ in db.calls} == {"viking://resources", "viking://agent/skills"}
    assert {call.kwargs["uri"] for call in executor._reindex_rfv.await_args_list} == {
        "viking://resources",
        "viking://agent/skills/shared",
    }


@pytest.mark.asyncio
async def test_resource_direct_read_retries_full_inventory_and_injects_snapshot(monkeypatch):
    from openviking.storage import resource_rfv

    root = USER + "/resources/project"
    executor, fs, db, run = executor_for(monkeypatch, {root + "/live.md": False})
    executor._reindex_rfv = ReindexExecutor._reindex_rfv.__get__(executor)
    db.get_reindex_inventory_under_uri = AsyncMock(
        side_effect=[
            VikingDBException("429", status_code=429, retryable=True),
            {"old": dict(record(root + "/old.md"), id="old")},
        ]
    )
    capture = AsyncMock(side_effect=RuntimeError("snapshot captured"))
    monkeypatch.setattr(resource_rfv, "build_rfv_snapshot", capture)
    with pytest.raises(RuntimeError, match="snapshot captured"):
        await executor._reindex_rfv(
            uri=root,
            mode="vectors_only",
            context_type="resource",
            recursive=True,
            run=run,
            root_is_dir=True,
        )
    assert db.get_reindex_inventory_under_uri.await_count == 2
    assert all(call.args[0] == root for call in db.get_reindex_inventory_under_uri.await_args_list)
    assert set(capture.await_args.kwargs["vector_inventory"]) == {"old"}
    assert {"abstract", "search_tags", "name", "description", "tags"} <= set(
        db.get_reindex_inventory_under_uri.await_args.kwargs["output_fields"]
    )


@pytest.mark.asyncio
async def test_memory_tree_outside_scope_is_rejected_before_reads_or_mutations(monkeypatch):
    root = USER + "/memories/events"
    foreign = "viking://user/carol/memories/events/foreign.md"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            root + "/live.md": False,
            foreign: False,
        },
    )
    fs.tree = AsyncMock(return_value=[{"uri": foreign, "isDir": False}])
    fs.read_file = AsyncMock(return_value="Foreign data")
    with pytest.raises(ValueError, match="formal tree"):
        await executor._reindex_memory(uri=root, mode="vectors_only", run=run)
    fs.read_file.assert_not_awaited()
    executor._upsert_context.assert_not_awaited()
    assert not db.deleted


@pytest.mark.asyncio
async def test_denied_shallow_listing_blocks_all_user_scope_mutations(monkeypatch):
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            USER + "/resources/live.md": False,
            USER + "/skills/hidden/SKILL.md": False,
        },
        {"ghost": record(USER + "/memories/gone.md")},
    )
    original_ls = fs.ls

    async def denied(uri, **kwargs):
        if uri == USER + "/skills":
            return [{"uri": uri + "/hidden", "isDir": True, "access": "denied"}]
        return await original_ls(uri, **kwargs)

    fs.ls = denied
    with pytest.raises(ValueError, match="incomplete listing"):
        await executor._reindex_user_namespace(uri=USER, mode="vectors_only", run=run)
    executor._reindex_rfv.assert_not_awaited()
    executor._upsert_context.assert_not_awaited()
    assert not db.deleted


@pytest.mark.asyncio
async def test_direct_memory_root_listing_failure_precedes_semantic_work(monkeypatch):
    root = USER + "/memories"
    executor, fs, db, run = executor_for(monkeypatch, {root + "/events/live.md": False})
    executor._enqueue_reindex_semantic_message = AsyncMock()
    fs.fault = ("ls", root)
    with pytest.raises(RuntimeError, match="listing unavailable"):
        await executor._reindex_memory(uri=root, mode="semantic_and_vectors", run=run)
    executor._enqueue_reindex_semantic_message.assert_not_awaited()
    executor._upsert_context.assert_not_awaited()
    assert not db.deleted


@pytest.mark.asyncio
async def test_legacy_chunk_is_retired_without_deleting_same_named_real_directory(monkeypatch):
    root = USER + "/memories"
    base, chunk = root + "/profile.md", root + "/profile.md#chunk_0000"
    executor, fs, db, run = executor_for(
        monkeypatch,
        {
            base: False,
            chunk + "/child.md": False,
        },
        {
            "base": record(base),
            "valid-chunk": record(chunk),
            "directory-marker": record(chunk, 0),
            "child": record(chunk + "/child.md"),
        },
    )
    executor._read_memory_directory_source = AsyncMock(
        return_value=module._SourceRead(exists=True, text="Real directory summary")
    )
    await executor._reindex_memory(uri=root, mode="vectors_only", run=run)
    assert db.deleted == ["valid-chunk"]
    assert {"base", "directory-marker", "child"} <= set(db.records)


@pytest.mark.asyncio
async def test_full_inventory_count_drift_retries_before_snapshot(monkeypatch):
    executor, fs, db, run = executor_for(monkeypatch, {})
    db.get_reindex_inventory_under_uri = AsyncMock(
        side_effect=[
            RuntimeError("Reindex inventory returned 1 records but count was 2"),
            {},
        ]
    )
    inventory = await executor._read_reindex_inventory(
        db,
        uri=USER + "/resources",
        ctx=run.ctx,
        recursive=True,
    )
    assert inventory == {}
    assert db.get_reindex_inventory_under_uri.await_count == 2


@pytest.mark.asyncio
async def test_global_missing_user_container_still_reindexes_shared_resources(monkeypatch):
    executor, fs, db, run = executor_for(monkeypatch, {"viking://resources/live.md": False})
    fs.entries.pop("viking://user")
    fs.entries.pop(USER)
    await executor._reindex_global_namespace(uri="viking://", mode="vectors_only", run=run)
    assert [call.kwargs["uri"] for call in executor._reindex_rfv.await_args_list] == [
        "viking://resources"
    ]
