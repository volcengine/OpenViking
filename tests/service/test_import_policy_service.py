# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.resource.watch_manager import WatchManager
from openviking.service.resource_service import ResourceService
from openviking.utils.resource_processor import ResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError


@pytest.mark.asyncio
async def test_conflict_rejected_before_source_work():
    service = ResourceService(
        vikingdb=object(), viking_fs=object(), resource_processor=object(), skill_processor=object()
    )
    service._prepare_standard_source_plan = AsyncMock()
    with pytest.raises(InvalidArgumentError, match="cannot be combined"):
        await service.add_resource(
            "https://example.com/docs", object(), strict=True, import_policy="best_effort"
        )
    service._prepare_standard_source_plan.assert_not_awaited()
    with pytest.raises(InvalidArgumentError, match="top-level"):
        await service.add_resource(
            "https://example.com/docs", object(), args={"import_policy": "best_effort"}
        )


def test_watch_partial_round_does_not_checkpoint_and_keeps_policy():
    result = WatchManager._result(
        "completed",
        {
            "import_complete": False,
            "source_checkpoint": {"head_sha": "same"},
            "meta": {"resolved_head_sha": "same"},
        },
        None,
        connector=False,
        instruction="",
    )
    assert result.status == "partial" and result.source_checkpoint is None
    service = ResourceService(
        vikingdb=object(), viking_fs=object(), resource_processor=object(), skill_processor=object()
    )
    assert (
        service._watch_processor_kwargs(
            {"strict": False, "import_policy": "best_effort"}, None, "replace"
        )["import_policy"]
        == "best_effort"
    )


@pytest.mark.asyncio
async def test_quarantine_revoked_snapshot_removes_ancestor_views_and_vectors(monkeypatch):
    fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        exists=AsyncMock(return_value=True),
        create_temp_uri=MagicMock(return_value="viking://user/alice/temp/quarantine"),
        mkdir=AsyncMock(),
        mv=AsyncMock(),
        _uri_to_path=lambda uri, **kw: uri,
        _delete_from_vector_store=AsyncMock(),
        remove_files=AsyncMock(),
        _async_agfs=SimpleNamespace(
            pathlock_release=AsyncMock(),
            pathlock_acquire_exact_batch=AsyncMock(return_value={"id": "parent"}),
        ),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fs)
    rp = ResourceProcessor(vikingdb=object(), media_storage=None)
    rp.acquire_resource_lock = AsyncMock(return_value={"id": "parent"})
    await rp._quarantine_revoked_snapshot("viking://resources/team/docs", object(), {"id": "root"})
    assert fs.mv.await_args.kwargs["vector_mode"] == "delete_source"
    assert fs.mv.await_args.kwargs["copy_semantic_sidecars"] is False
    assert [c.args[0] for c in fs._delete_from_vector_store.await_args_list] == [
        ["viking://resources/team"],
        ["viking://resources"],
    ]
    assert len(fs.remove_files.await_args_list) == 4
    for call in fs._async_agfs.pathlock_acquire_exact_batch.await_args_list:
        parent, abstract, overview = call.args[0]
        assert abstract == parent + "/.abstract.md"
        assert overview == parent + "/.overview.md"
        assert call.kwargs["owner_lease_ref"] == {"id": "root"}


@pytest.mark.asyncio
async def test_quarantine_failure_is_not_silenced(monkeypatch):
    fs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        exists=AsyncMock(return_value=True),
        create_temp_uri=MagicMock(return_value="viking://user/alice/temp/quarantine"),
        mkdir=AsyncMock(),
        mv=AsyncMock(side_effect=OpenVikingError("storage unavailable")),
    )
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fs)
    rp = ResourceProcessor(vikingdb=object(), media_storage=None)
    with pytest.raises(OpenVikingError, match="storage unavailable"):
        await rp._quarantine_revoked_snapshot("viking://resources", object(), {"id": "root"})


@pytest.mark.asyncio
@pytest.mark.parametrize("preacquired", [False, True])
async def test_quarantine_snapshot_with_real_storage_and_locks(monkeypatch, watch_vfs, preacquired):
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    fs = watch_vfs
    ctx = RequestContext(user=UserIdentifier("test", "alice"), role=Role.ADMIN)
    root = "viking://resources/team/docs"
    destination = fs.create_temp_uri(ctx=ctx)
    monkeypatch.setattr(fs, "create_temp_uri", lambda **kwargs: destination)
    monkeypatch.setattr(fs, "_delete_from_vector_store", AsyncMock())
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fs)
    for uri in ("viking://resources", "viking://resources/team", root):
        await fs.mkdir(uri, exist_ok=True, ctx=ctx)
        await fs.write_file(uri + "/.abstract.md", "revoked summary", ctx=ctx)
        await fs.write_file(uri + "/.overview.md", "revoked overview", ctx=ctx)
    await fs.write_file(root + "/old.md", "revoked body", ctx=ctx)

    rp = ResourceProcessor(vikingdb=object(), media_storage=None)
    lease = (
        await rp.acquire_resource_lock(fs._uri_to_path(root, ctx=ctx), uri=root)
        if preacquired
        else None
    )
    try:
        await rp._quarantine_revoked_snapshot(root, ctx, lease)
    finally:
        if lease is not None:
            await fs._async_agfs.pathlock_release(lease)

    assert not await fs.exists(root, ctx=ctx)
    assert await fs.read_file(destination + "/old.md", ctx=ctx) == "revoked body"
    for uri in ("viking://resources", "viking://resources/team", destination):
        assert not await fs.exists(uri + "/.abstract.md", ctx=ctx)
        assert not await fs.exists(uri + "/.overview.md", ctx=ctx)
    assert any(root in call.args[0] for call in fs._delete_from_vector_store.await_args_list)
