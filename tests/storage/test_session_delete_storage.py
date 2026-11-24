# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Unindexed deletion avoids vector work while retaining storage safety checks."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.namespace import resolve_request_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig
from tests.storage.test_vector_transfer import _RealAclMemoryTransferBackend, _record

SESSION_URI = "viking://user/alice/sessions/session-1"
SESSION_PATH = "/local/account/user/alice/sessions/session-1"
RESOURCE_URI = "viking://resources/project"


@pytest.fixture
def storage():
    """Use real URI, authorization, lock-context, and vector cleanup behavior."""
    lease = {"lease_ref": "delete-lease"}
    agfs = SimpleNamespace(
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(side_effect=AssertionError("rm must not list descendant files")),
        pathlock_acquire_tree=AsyncMock(return_value=lease),
        pathlock_acquire_exact=AsyncMock(return_value=lease),
        pathlock_acquire_batch=AsyncMock(return_value=lease),
        pathlock_release=AsyncMock(),
        rm=AsyncMock(return_value={}),
    )
    vector = SimpleNamespace(count=AsyncMock(return_value=7), delete_uris=AsyncMock(return_value=7))
    acl = SimpleNamespace(is_enabled=AsyncMock(return_value=False), resolve_many=AsyncMock())
    fs = VikingFS(agfs=SimpleNamespace(), vector_store=vector, acl_manager=acl)
    fs._async_agfs = agfs
    ctx = RequestContext(user=UserIdentifier("account", "alice"), role=Role.USER)
    return SimpleNamespace(fs=fs, agfs=agfs, vector=vector, acl=acl, ctx=ctx, lease=lease)


@pytest.mark.parametrize("acl_enabled", [False, True])
@pytest.mark.parametrize(
    "request_uri",
    [
        SESSION_URI,
        "viking://~/sessions/session-1",
        "viking://temp/alice/raw-upload-1",
    ],
)
async def test_no_vector_rm_skips_subtree_enumeration_and_vector_store(
    storage, acl_enabled, request_uri
):
    storage.acl.is_enabled.return_value = acl_enabled
    uri = resolve_request_uri(request_uri, storage.ctx)
    path = storage.fs._uri_to_path(uri, ctx=storage.ctx)

    result = await storage.fs.rm(uri, recursive=True, ctx=storage.ctx, no_vector=True)

    assert result == {"estimated_deleted_count": 0}
    storage.agfs.ls.assert_not_awaited()
    storage.vector.count.assert_not_awaited()
    storage.vector.delete_uris.assert_not_awaited()
    storage.agfs.pathlock_acquire_tree.assert_awaited_once_with(path)
    storage.agfs.pathlock_acquire_exact.assert_not_awaited()
    storage.agfs.rm.assert_awaited_once_with(
        path,
        recursive=True,
        fs_ctx={"account_id": "account", "lease_ref": "delete-lease"},
        auto_pathlock=True,
    )
    storage.agfs.pathlock_release.assert_awaited_once_with(storage.lease)


async def test_session_file_rm_keeps_exact_lock_and_skips_vector_store(storage):
    storage.agfs.stat.return_value = {"isDir": False}
    uri = f"{SESSION_URI}/messages.jsonl"
    path = f"{SESSION_PATH}/messages.jsonl"

    result = await storage.fs.rm(uri, ctx=storage.ctx, no_vector=True)

    assert result == {"estimated_deleted_count": 0}
    storage.agfs.ls.assert_not_awaited()
    storage.vector.count.assert_not_awaited()
    storage.vector.delete_uris.assert_not_awaited()
    storage.agfs.pathlock_acquire_tree.assert_not_awaited()
    storage.agfs.pathlock_acquire_exact.assert_awaited_once_with(path)
    storage.agfs.rm.assert_awaited_once_with(
        path,
        recursive=False,
        fs_ctx={"account_id": "account", "lease_ref": "delete-lease"},
        auto_pathlock=True,
    )
    storage.agfs.pathlock_release.assert_awaited_once_with(storage.lease)


async def test_session_directory_rm_still_requires_recursive(storage):
    with pytest.raises(InvalidArgumentError, match="recursive"):
        await storage.fs.rm(SESSION_URI, ctx=storage.ctx, no_vector=True)

    storage.agfs.pathlock_acquire_tree.assert_not_awaited()
    storage.agfs.pathlock_acquire_exact.assert_not_awaited()
    storage.agfs.rm.assert_not_awaited()
    storage.vector.count.assert_not_awaited()
    storage.vector.delete_uris.assert_not_awaited()


@pytest.mark.parametrize("acl_enabled", [False, True])
@pytest.mark.parametrize("uri", [SESSION_URI, "viking://temp/alice/raw-upload-1"])
async def test_missing_no_vector_rm_does_not_scan_or_clean_vector_records(
    storage, acl_enabled, uri
):
    storage.acl.is_enabled.return_value = acl_enabled
    storage.agfs.stat.side_effect = FileNotFoundError(SESSION_PATH)

    result = await storage.fs.rm(uri, recursive=True, ctx=storage.ctx, no_vector=True)

    assert result == {"estimated_deleted_count": 0}
    storage.agfs.ls.assert_not_awaited()
    storage.vector.count.assert_not_awaited()
    storage.vector.delete_uris.assert_not_awaited()
    storage.agfs.pathlock_acquire_tree.assert_not_awaited()
    storage.agfs.rm.assert_not_awaited()


async def test_session_rm_releases_its_tree_lease_when_backend_delete_fails(storage):
    storage.agfs.rm.side_effect = OSError("storage delete failed")

    with pytest.raises(OSError, match="storage delete failed"):
        await storage.fs.rm(SESSION_URI, recursive=True, ctx=storage.ctx, no_vector=True)

    storage.agfs.pathlock_release.assert_awaited_once_with(storage.lease)
    assert storage.agfs.rm.await_args.kwargs["fs_ctx"]["lease_ref"] == "delete-lease"


async def test_session_rm_rechecks_existence_after_waiting_for_tree_lock(storage):
    storage.agfs.stat.side_effect = [
        {"isDir": True},
        FileNotFoundError(SESSION_PATH),
    ]

    result = await storage.fs.rm(SESSION_URI, recursive=True, ctx=storage.ctx, no_vector=True)

    assert result == {"estimated_deleted_count": 0}
    storage.agfs.pathlock_acquire_tree.assert_awaited_once_with(SESSION_PATH)
    storage.agfs.rm.assert_not_awaited()
    storage.agfs.pathlock_release.assert_awaited_once_with(storage.lease)


async def test_ttl_recycles_under_native_tree_lease_and_retries_failed_deletion(
    tmp_path, monkeypatch
):
    from openviking.scheduling.expiry.api import CallbackRequest, ItemIdentity
    from openviking.ttl.adapter import TTLBackfillRequest, TTLRecycleRequest
    from openviking.ttl.runtime import DirectoryTTL
    from openviking.ttl.tests.test_support import FakeredisCacheRuntime
    from openviking.ttl.wiring import install_ttl_hook
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
    from openviking_cli.exceptions import NotFoundError
    from openviking_cli.utils.config.agfs_config import AGFSConfig
    from openviking_cli.utils.config.ttl_config import TTLConfig

    agfs = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    )
    vectors = SimpleNamespace(count=AsyncMock(return_value=1), delete_uris=AsyncMock())
    fs = VikingFS(agfs=agfs, vector_store=vectors)
    cache_runtime = FakeredisCacheRuntime()
    engine = DirectoryTTL(
        resource_id="recycle-contract",
        cache_runtime=cache_runtime,
        batch_interval=0,
        delete_interval=0,
    )
    engine.fs = fs
    engine.policies = AsyncMock(return_value=(TTLConfig(sessions=7, user_events=7), None))
    ctx = engine.context("account")
    session = "viking://user/alice/sessions/fresh"
    event = "viking://user/alice/memories/events/2020/01/01"
    request = TTLRecycleRequest(engine.resource_id, ctx.account_id)
    callback = CallbackRequest(ItemIdentity("ttl", ctx.account_id, "_system", "maintenance"))
    try:
        # A historical Session missed incremental registration; backfill must recover it.
        await fs.write_file(session + "/messages.jsonl", "recent message", ctx=ctx)
        install_ttl_hook(fs, engine)
        await engine.start()
        # Soft TTL permits writing old Events until the background deletion happens.
        await fs.write_file(event + "/event.md", "historical event", ctx=ctx)
        await asyncio.wait_for(engine._registration_task, 5)
        prefix = engine.prefix(ctx.account_id)
        event_score = await cache_runtime.redis.zscore(prefix + "user_events", event)
        assert event_score is not None
        await engine.register_initial_backfill(
            TTLBackfillRequest(
                engine.resource_id,
                ctx.account_id,
                ("viking://user/alice/sessions", "viking://user/alice/memories/events"),
            )
        )
        assert (await engine.recycle_due(request)).deleted == 0
        assert await fs.read_file(event + "/event.md", ctx=ctx) == "historical event"
        monkeypatch.setattr(
            "openviking.ttl.runtime.time.time_ns", lambda: engine._recycle_after_ms * 1_000_000
        )
        await engine.handle(callback)
        await asyncio.wait_for(engine._task, 5)
        assert (await engine.read_initial(ctx.account_id))["done"]
        assert await cache_runtime.redis.zscore(prefix + "sessions", session) is not None

        # The file's fresh ModTime wins over a stale Session index entry.
        await cache_runtime.redis.zadd(prefix + "sessions", {session: 1})
        vectors.delete_uris.side_effect = OSError("vector deletion unavailable")
        failed = await engine.recycle_due(request)
        assert failed.failed == 1 and failed.skipped >= 1
        assert await cache_runtime.redis.zscore(prefix + "sessions", session) > 1
        assert await cache_runtime.redis.zscore(prefix + "user_events", event) == event_score
        assert await fs.read_file(event + "/event.md", ctx=ctx) == "historical event"
        assert (await fs._async_agfs.pathlock_observe())["active_locks"] == 0

        vectors.delete_uris.side_effect = None
        recycled = await engine.recycle_due(request)
        assert recycled.deleted == 1 and recycled.failed == 0
        with pytest.raises(NotFoundError):
            await fs.stat(event, ctx=ctx)
        assert await cache_runtime.redis.zscore(prefix + "user_events", event) is None
        assert await fs.read_file(session + "/messages.jsonl", ctx=ctx) == "recent message"
        assert (await fs._async_agfs.pathlock_observe())["active_locks"] == 0
    finally:
        await engine.aclose()
        agfs.close()


async def test_session_rm_preserves_retryable_busy_error(storage):
    storage.agfs.pathlock_acquire_tree.side_effect = LockAcquisitionError("path is locked")

    with pytest.raises(ResourceBusyError) as error:
        await storage.fs.rm(SESSION_URI, recursive=True, ctx=storage.ctx, no_vector=True)

    assert error.value.uri == SESSION_URI
    assert error.value.conflict_type == "path_busy"
    assert error.value.retryable is True
    storage.agfs.rm.assert_not_awaited()
    storage.agfs.pathlock_release.assert_not_awaited()


@pytest.mark.parametrize("acl_enabled", [False, True])
@pytest.mark.parametrize(
    "uri",
    ["viking://user/bob/sessions/session-1", f"{SESSION_URI}/../another-session"],
)
async def test_session_rm_preserves_namespace_and_path_validation(storage, acl_enabled, uri):
    storage.acl.is_enabled.return_value = acl_enabled

    with pytest.raises(PermissionDeniedError):
        await storage.fs.rm(uri, recursive=True, ctx=storage.ctx, no_vector=True)

    storage.agfs.stat.assert_not_awaited()
    storage.agfs.pathlock_acquire_tree.assert_not_awaited()
    storage.agfs.rm.assert_not_awaited()
    storage.vector.delete_uris.assert_not_awaited()


@pytest.mark.parametrize(
    ("uri", "missing"),
    [(RESOURCE_URI, False), (RESOURCE_URI, True), (f"{SESSION_URI}/messages.jsonl", False)],
)
async def test_default_rm_still_cleans_vector_records(
    storage, vector_backend_factory, tmp_path, monkeypatch, uri, missing
):
    is_dir = uri == RESOURCE_URI
    storage.agfs.stat.return_value = {"isDir": is_dir}
    if missing:
        storage.agfs.stat.side_effect = FileNotFoundError(uri)

    target_uris = [uri]
    if is_dir:
        target_uris.extend(f"{uri}/nested/{i}.md" for i in range(205))
    records = [
        {
            "id": f"target-{i}",
            "uri": target_uri,
            "account_id": storage.ctx.account_id,
            "level": i % 3,
            "vector": [0.1] * 4,
        }
        for i, target_uri in enumerate(target_uris)
    ]
    sibling = {**records[0], "id": "sibling", "uri": f"{uri}.bak"}
    foreign = {**records[0], "id": "foreign", "account_id": "other"}
    foreign_ctx = RequestContext(user=UserIdentifier("other", "alice"), role=Role.ROOT)
    backend = vector_backend_factory(
        VectorDBBackendConfig(backend="local", name="context", dimension=4, path=str(tmp_path))
    )
    try:
        assert await backend.create_collection(
            "context", CollectionSchemas.context_collection("context", 4)
        )
        await backend.upsert_many([*records, sibling], ctx=storage.ctx)
        await backend.upsert_many([foreign], ctx=foreign_ctx)
        monkeypatch.setattr(backend, "count", AsyncMock())
        storage.fs.vector_store = backend

        result = await storage.fs.rm(uri, recursive=True, ctx=storage.ctx)

        assert result == {"estimated_deleted_count": len(records)}
        remaining = await backend.get_strict(
            [record["id"] for record in records] + ["sibling"], ctx=storage.ctx
        )
        assert [record["id"] for record in remaining] == ["sibling"]
        assert [r["id"] for r in await backend.get_strict(["foreign"], ctx=foreign_ctx)] == [
            "foreign"
        ]
        backend.count.assert_not_awaited()
        storage.agfs.ls.assert_not_awaited()
        if missing:
            storage.agfs.rm.assert_not_awaited()
        else:
            storage.agfs.rm.assert_awaited_once()
            assert storage.agfs.rm.await_args.kwargs["recursive"] is is_dir
            storage.agfs.pathlock_release.assert_awaited_once_with(storage.lease)
    finally:
        await backend.close()


@pytest.mark.parametrize("allowed", [False, True])
async def test_resource_rm_authorizes_the_whole_subtree_at_its_root(storage, allowed):
    backend = _RealAclMemoryTransferBackend(
        [
            _record(
                "root",
                RESOURCE_URI,
                account_id=storage.ctx.account_id,
                level=0,
                acl_mode="restricted",
                acl_direct_grants=["7:user:alice"] if allowed else [],
            ),
            _record(
                "child",
                f"{RESOURCE_URI}/nested/restricted.md",
                account_id=storage.ctx.account_id,
                acl_mode="restricted",
                acl_direct_grants=[],
            ),
        ]
    )
    storage.fs.vector_store = backend
    storage.fs.acl_manager = backend.acl_manager
    service = FSService(viking_fs=storage.fs)
    service._enqueue_delete_refresh = AsyncMock(return_value="queued")

    if allowed:
        # Direct deletion still checks the child; FSService authorizes the subtree at its root.
        with pytest.raises(PermissionDeniedError, match="manage permission required"):
            await storage.fs.rm(RESOURCE_URI, ctx=storage.ctx, recursive=True)
        assert set(backend.records) == {"root", "child"}
        storage.agfs.rm.assert_not_awaited()

        await service._rm(RESOURCE_URI, ctx=storage.ctx, recursive=True)
        assert not backend.records
        storage.agfs.rm.assert_awaited_once()
    else:
        with pytest.raises(PermissionDeniedError):
            await service._rm(RESOURCE_URI, ctx=storage.ctx, recursive=True)
        assert set(backend.records) == {"root", "child"}
        storage.agfs.rm.assert_not_awaited()
