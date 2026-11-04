# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deletion safety regressions for URI-scoped reindex."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openviking.service.reindex_executor as reindex_module
from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import (
    ReindexExecutor,
    _ReindexCounters,
    _ReindexRunContext,
    _SourceRead,
)
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def owner_context(*, role=Role.ADMIN, actor_peer_id=None):
    return RequestContext(
        user=UserIdentifier(account_id="orphan_safety", user_id="bob"),
        role=role,
        actor_peer_id=actor_peer_id,
    )


@pytest.mark.asyncio
async def test_peer_cleanup_preserves_live_roots_and_only_deletes_missing_memory_roots(monkeypatch):
    from openviking_cli.exceptions import NotFoundError

    root = "viking://user/bob/peers"
    memory, resource = root + "/live/memories", root + "/live/resources"
    gone_memory, gone_resource = root + "/gone/memories", root + "/gone/resources"
    inventory = {
        "live-l0": {"uri": memory, "level": 0},
        "live-l2": {"uri": memory + "/file.md", "level": 2},
        "live-chunk": {"uri": memory + "/file.md#chunk_0000", "level": 2},
        "gone-l0": {"uri": gone_memory, "level": 0},
        "gone-l2": {"uri": gone_memory + "/file.md", "level": 2},
        "gone-chunk": {"uri": gone_memory + "/file.md#chunk_0000", "level": 2},
        "live-resource": {"uri": resource + "/file.md", "level": 2},
        "gone-resource": {"uri": gone_resource + "/file.md", "level": 2},
    }
    inventory = {key: dict(value, id=key) for key, value in inventory.items()}
    children = {
        root: [{"uri": root + "/live", "isDir": True}],
        root + "/live": [{"uri": memory, "isDir": True}, {"uri": resource, "isDir": True}],
        memory: [{"uri": memory + "/file.md", "isDir": False}],
    }

    async def stat(uri, **kwargs):
        if uri not in children and uri != resource:
            raise NotFoundError(uri)
        return {"isDir": True}

    fs = SimpleNamespace(
        stat=AsyncMock(side_effect=stat),
        ls=AsyncMock(side_effect=lambda uri, **kw: children.get(uri, [])),
        exists=AsyncMock(return_value=True),
        read_file=AsyncMock(return_value="Live body"),
        abstract=AsyncMock(return_value=""),
        overview=AsyncMock(return_value=""),
    )
    db = SimpleNamespace(strict_delete=AsyncMock(side_effect=lambda ids, **kw: len(ids)))
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(reindex_module, "get_service", lambda: SimpleNamespace(vikingdb_manager=db))
    executor = ReindexExecutor()
    executor._reindex_rfv = AsyncMock()
    executor._upsert_context = AsyncMock()
    executor._effective_file_vectorization_concurrency = lambda: 2
    run = _ReindexRunContext(ctx=owner_context(), counters=_ReindexCounters())
    await executor._reindex_peer_scopes(uri=root, mode="vectors_only", run=run, inventory=inventory)
    deleted_ids = {
        record_id for call in db.strict_delete.await_args_list for record_id in call.args[0]
    }
    assert deleted_ids == {
        "gone-l0",
        "gone-l2",
        "gone-chunk",
        "live-chunk",
    }
    assert run.counters.deleted_records == 4
    by_uri = {call.kwargs["uri"]: call.kwargs for call in executor._reindex_rfv.await_args_list}
    assert by_uri[resource]["root_exists"] is True
    assert by_uri[gone_resource]["root_exists"] is False
    assert {"live-l0", "live-l2"}.isdisjoint(deleted_ids)


@pytest.mark.asyncio
async def test_peer_memory_root_probe_error_does_not_delete_vectors(monkeypatch):
    root = "viking://user/bob"
    fs = SimpleNamespace(stat=AsyncMock(side_effect=RuntimeError("S3 timeout")))
    db = SimpleNamespace(strict_delete=AsyncMock())
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(reindex_module, "get_service", lambda: SimpleNamespace(vikingdb_manager=db))
    executor = ReindexExecutor()
    executor._read_reindex_inventory = AsyncMock(
        return_value={
            "memory": {"id": "memory", "uri": root + "/peers/peer-a/memories/file.md", "level": 2}
        }
    )
    ctx = owner_context()
    with pytest.raises(RuntimeError, match="S3 timeout"):
        await executor._reindex_peer_scopes(
            uri=root + "/peers",
            mode="vectors_only",
            run=_ReindexRunContext(ctx=ctx, counters=_ReindexCounters()),
            inventory={
                "memory": {
                    "id": "memory",
                    "uri": root + "/peers/peer-a/memories/file.md",
                    "level": 2,
                }
            },
        )
    db.strict_delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.ADMIN, Role.ROOT])
@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize(
    "uri",
    [
        "viking://",
        "viking://user",
        "viking://user/bob",
        "viking://user/bob/peers",
        "viking://user/bob/peers/peer-hidden/resources",
    ],
)
async def test_actor_unsafe_scope_is_rejected_before_task_creation(monkeypatch, role, wait, uri):
    monkeypatch.setattr(
        reindex_module, "get_task_tracker", lambda: pytest.fail("Created unsafe task")
    )
    ctx = owner_context(role=role, actor_peer_id="peer-current")
    with pytest.raises(PermissionDeniedError):
        await ReindexExecutor().execute(uri=uri, mode="vectors_only", wait=wait, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/alice",
        "viking://user/alice/peers",
        "viking://user/alice/peers/peer-hidden/memories",
    ],
)
async def test_actor_cross_owner_scope_is_rejected_before_task_creation(monkeypatch, wait, uri):
    monkeypatch.setattr(
        reindex_module, "get_task_tracker", lambda: pytest.fail("Created unsafe cross-owner task")
    )
    with pytest.raises(PermissionDeniedError):
        await ReindexExecutor().execute(
            uri=uri,
            mode="vectors_only",
            wait=wait,
            ctx=owner_context(actor_peer_id="peer-current"),
        )


@pytest.mark.asyncio
async def test_preaccepted_actor_namespace_job_is_rejected_before_service_access(monkeypatch):
    monkeypatch.setattr(reindex_module, "get_service", lambda: pytest.fail("Read unsafe scope"))
    with pytest.raises(PermissionDeniedError):
        await ReindexExecutor()._run(
            uri="viking://user/bob",
            object_type="user_namespace",
            mode="vectors_only",
            ctx=owner_context(actor_peer_id="peer-current"),
        )


@pytest.mark.asyncio
async def test_rejected_actor_job_releases_its_preaccepted_lease(monkeypatch):
    lease = {"lease_id": "preaccepted"}
    agfs = SimpleNamespace(pathlock_release=AsyncMock())
    fs = SimpleNamespace(_async_agfs=agfs, stat=AsyncMock())
    monkeypatch.setattr(reindex_module, "get_service", lambda: SimpleNamespace(viking_fs=fs))
    with pytest.raises(PermissionDeniedError):
        await ReindexExecutor()._run(
            uri="viking://user/bob",
            object_type="user_namespace",
            mode="vectors_only",
            ctx=owner_context(actor_peer_id="peer-current"),
            lease_guard=PathLockGuard(agfs, lease),
        )
    agfs.pathlock_release.assert_awaited_once_with(lease)
    fs.stat.assert_not_awaited()


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/bob/memories/events",
        "viking://user/bob/resources/project",
        "viking://user/bob/peers/peer-current/memories",
        "viking://resources/shared",
        "viking://user/alice/resources/project",
        "viking://user/alice/peers/peer-current/memories",
    ],
)
def test_actor_guard_allows_explicit_visible_content_scopes(uri):
    ReindexExecutor._ensure_reconciliation_scope_visible(
        uri, owner_context(actor_peer_id="peer-current")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["user", "global", "peer_cleanup"])
async def test_actor_guard_protects_internal_namespace_entry_points(monkeypatch, entry):
    monkeypatch.setattr(reindex_module, "get_service", lambda: pytest.fail("Read unsafe inventory"))
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: pytest.fail("Read unsafe tree"))
    executor = ReindexExecutor()
    ctx = owner_context(actor_peer_id="peer-current")
    run = _ReindexRunContext(ctx=ctx, counters=_ReindexCounters())
    with pytest.raises(PermissionDeniedError):
        if entry == "user":
            await executor._reindex_user_namespace(
                uri="viking://user/bob", mode="vectors_only", run=run
            )
        elif entry == "global":
            await executor._reindex_global_namespace(uri="viking://", mode="vectors_only", run=run)
        else:
            await executor._reindex_peer_scopes(
                uri="viking://user/bob/peers", mode="vectors_only", run=run, inventory={}
            )


@pytest.mark.asyncio
async def test_memory_read_error_protects_chunks_and_other_files_still_converge(monkeypatch):
    root = "viking://user/bob/memories/events"
    failed_file, healthy_file, gone_file = [
        root + "/" + name for name in ("failed.md", "healthy.md", "gone.md")
    ]
    fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        tree=AsyncMock(
            return_value=[{"uri": uri, "isDir": False} for uri in (failed_file, healthy_file)]
        ),
    )
    db = SimpleNamespace(strict_delete=AsyncMock(side_effect=lambda ids, **kw: len(ids)))
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(reindex_module, "get_service", lambda: SimpleNamespace(vikingdb_manager=db))
    executor = ReindexExecutor()
    executor._reindex_memory_directory_chain = AsyncMock(return_value={})
    executor._safe_read_text = AsyncMock(return_value="")
    executor._upsert_context = AsyncMock()
    executor._effective_file_vectorization_concurrency = lambda: 2

    async def body(uri, **kwargs):
        return (
            _SourceRead(exists=True, error=RuntimeError("read failure"))
            if uri == failed_file
            else _SourceRead(exists=True, text="healthy body")
        )

    executor._read_memory_body = AsyncMock(side_effect=body)
    uris = {
        "failed": failed_file,
        "failed-c0": failed_file + "#chunk_0000",
        "failed-c1": failed_file + "#chunk_0001",
        "healthy": healthy_file,
        "healthy-c0": healthy_file + "#chunk_0000",
        "stale": healthy_file + "#chunk_0001",
        "gone": gone_file,
        "gone-c0": gone_file + "#chunk_0000",
    }
    inventory = {key: {"id": key, "uri": uri, "level": 2} for key, uri in uris.items()}
    counters = _ReindexCounters()
    await executor._reindex_memory_vectors(
        uri=root, ctx=owner_context(), counters=counters, vector_inventory=inventory
    )
    assert set(db.strict_delete.await_args.args[0]) == {
        "failed-c0",
        "failed-c1",
        "healthy-c0",
        "stale",
        "gone",
        "gone-c0",
    }
    assert counters.failed_records == 1
    assert counters.rebuilt_records == 1
    executor._upsert_context.assert_awaited_once()
    assert executor._upsert_context.await_args.kwargs["uri"] == healthy_file


@pytest.mark.asyncio
async def test_memory_reindex_preserves_hidden_source_files_without_vectorizing_sidecars(
    monkeypatch,
):
    root = "viking://user/bob/memories/events"
    hidden_file = root + "/.note.md"
    sidecars = [root + "/.abstract.md", root + "/.overview.md"]

    async def tree(_uri, *, show_all_hidden=False, **_kwargs):
        if not show_all_hidden:
            return []
        return [
            {"uri": item, "isDir": False} for item in [hidden_file, *sidecars]
        ]

    fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        tree=AsyncMock(side_effect=tree),
    )
    db = SimpleNamespace(strict_delete=AsyncMock())
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        reindex_module, "get_service", lambda: SimpleNamespace(vikingdb_manager=db)
    )
    executor = ReindexExecutor()
    executor._reindex_memory_directory_chain = AsyncMock(return_value={})
    executor._read_memory_body = AsyncMock(
        return_value=_SourceRead(exists=True, text="hidden memory")
    )
    executor._upsert_context = AsyncMock()
    executor._effective_file_vectorization_concurrency = lambda: 2

    counters = _ReindexCounters()
    await executor._reindex_memory_vectors(
        uri=root,
        ctx=owner_context(),
        counters=counters,
        vector_inventory={
            "hidden": {"id": "hidden", "uri": hidden_file, "level": 2}
        },
    )

    db.strict_delete.assert_not_awaited()
    executor._upsert_context.assert_awaited_once()
    assert executor._upsert_context.await_args.kwargs["uri"] == hidden_file
    assert fs.tree.await_args.kwargs["show_all_hidden"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("is_directory", [False, True])
async def test_literal_chunk_filename_is_never_deleted(monkeypatch, is_directory):
    parent = "viking://user/bob/memories/events"
    file_uri = parent + "/real#chunk_0000"
    fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": is_directory}),
        tree=AsyncMock(return_value=[{"uri": file_uri, "isDir": False}]),
    )
    db = SimpleNamespace(strict_delete=AsyncMock())
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(reindex_module, "get_service", lambda: SimpleNamespace(vikingdb_manager=db))
    executor = ReindexExecutor()
    executor._reindex_memory_directory_chain = AsyncMock(return_value={})
    executor._read_memory_body = AsyncMock(return_value=_SourceRead(exists=True, text="real file"))
    executor._safe_read_text = AsyncMock(return_value="")
    executor._upsert_context = AsyncMock()
    executor._effective_file_vectorization_concurrency = lambda: 1
    counters = _ReindexCounters()
    await executor._reindex_memory_vectors(
        uri=parent if is_directory else file_uri,
        ctx=owner_context(),
        counters=counters,
        vector_inventory={"real": {"id": "real", "uri": file_uri, "level": 2}},
    )
    db.strict_delete.assert_not_awaited()
    assert counters.rebuilt_records == 1
    assert counters.deleted_records == 0


@pytest.mark.asyncio
async def test_denied_memory_tree_fails_before_inventory_or_mutation(monkeypatch):
    root = "viking://user/bob/memories/events"
    fs = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        stat=AsyncMock(return_value={"isDir": True}),
        tree=AsyncMock(
            return_value=[{"uri": root + "/hidden.md", "isDir": False, "access": "denied"}]
        ),
    )
    db = SimpleNamespace(
        get_reindex_inventory_under_uri=AsyncMock(return_value={}), strict_delete=AsyncMock()
    )
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(reindex_module, "get_service", lambda: SimpleNamespace(vikingdb_manager=db))
    executor = ReindexExecutor()
    executor._reindex_memory_directory_chain = AsyncMock(return_value={})
    executor._upsert_context = AsyncMock()
    executor._read_memory_body = AsyncMock(
        return_value=_SourceRead(exists=True, text="hidden body")
    )
    executor._safe_read_text = AsyncMock(return_value="")
    executor._effective_file_vectorization_concurrency = lambda: 1
    with pytest.raises(ValueError, match="incomplete"):
        await executor._reindex_memory_vectors(
            uri=root, ctx=owner_context(), counters=_ReindexCounters()
        )
    db.get_reindex_inventory_under_uri.assert_not_awaited()
    db.strict_delete.assert_not_awaited()
    executor._reindex_memory_directory_chain.assert_not_awaited()
    executor._upsert_context.assert_not_awaited()
