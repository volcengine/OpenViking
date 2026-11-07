# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""rm/mv/cp/append path-lock acquires must wait for busy locks instead of failing at 0ms.

Regresssion guard for the CI flake seen on #4373: ``filesystem/test_fs_rm``
creates a directory and immediately removes it; the zero-wait lock acquire
returned CONFLICT ``path_busy`` while the mkdir's background semantic refresh
still held the tree lock. Mirrors the ingest-side wait added for #4337.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_fs._ops import FS_OP_LOCK_ACQUIRE_WAIT_SECS
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError


def _make_fs(monkeypatch, *, acquire_side_effect=None):
    fs = VikingFS(agfs=SimpleNamespace())
    acquire_tree = AsyncMock(return_value={"lease_ref": "tree"})
    if acquire_side_effect is not None:
        acquire_tree.side_effect = acquire_side_effect
    acquire_batch = AsyncMock(return_value={"lease_ref": "batch"})
    async def _stat(path, *a, **k):
        if path.endswith("/resources/b"):
            raise FileNotFoundError(path)
        return {"isDir": True}

    agfs = SimpleNamespace(
        stat=AsyncMock(side_effect=_stat),
        pathlock_acquire_tree=acquire_tree,
        pathlock_acquire_exact=AsyncMock(return_value={"lease_ref": "exact"}),
        pathlock_acquire_batch=acquire_batch,
        pathlock_release=AsyncMock(),
        rm=AsyncMock(return_value={}),
        ls=AsyncMock(return_value=[]),
        read=AsyncMock(side_effect=FileNotFoundError("no such file")),
        write=AsyncMock(return_value="ok"),
    )
    monkeypatch.setattr(fs, "_async_agfs", agfs)
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock(return_value=None))
    monkeypatch.setattr(
        fs, "_uri_to_path", lambda uri, **_k: f"/agfs/{uri.split(':///', 1)[-1]}"
    )
    monkeypatch.setattr(fs, "_path_to_uri", lambda path, **_k: f"viking://{path}")
    monkeypatch.setattr(fs, "_ls_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(fs, "_get_vector_store", lambda: None)
    monkeypatch.setattr(fs, "_copy_for_mv", AsyncMock())
    monkeypatch.setattr(fs, "_update_vector_store_uris", AsyncMock())
    monkeypatch.setattr(fs, "_pathlock_fs_ctx", lambda _ctx, lease: {"lease": lease})
    return fs, agfs


@pytest.mark.asyncio
async def test_rm_waits_for_busy_tree_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)

    await fs.rm("viking:///resources/docs", recursive=True, ctx=None)

    agfs.pathlock_acquire_tree.assert_awaited_once_with(
        "/agfs/resources/docs", timeout_secs=FS_OP_LOCK_ACQUIRE_WAIT_SECS
    )
    agfs.pathlock_release.assert_awaited_once()


@pytest.mark.asyncio
async def test_rm_file_waits_for_busy_exact_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)
    agfs.stat = AsyncMock(return_value={"isDir": False})

    await fs.rm("viking:///resources/docs/file.md", ctx=None)

    agfs.pathlock_acquire_exact.assert_awaited_once_with(
        "/agfs/resources/docs/file.md", timeout_secs=FS_OP_LOCK_ACQUIRE_WAIT_SECS
    )


@pytest.mark.asyncio
async def test_rm_still_maps_persistent_busy_to_resource_busy(monkeypatch):
    fs, _agfs = _make_fs(
        monkeypatch, acquire_side_effect=LockAcquisitionError("still busy")
    )

    with pytest.raises(ResourceBusyError):
        await fs.rm("viking:///resources/docs", recursive=True, ctx=None)


@pytest.mark.asyncio
async def test_mv_waits_for_busy_batch_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)

    await fs.mv("viking:///resources/a", "viking:///resources/b", ctx=None)

    assert agfs.pathlock_acquire_batch.await_args.kwargs.get(
        "timeout_secs"
    ) == FS_OP_LOCK_ACQUIRE_WAIT_SECS
    # Shape of the lock requests follows the current mv implementation
    # (parent transfer locks today); the regression guarantee is that every
    # batch acquire on the mv path forwards the wait timeout.
    assert agfs.pathlock_acquire_batch.await_count >= 1
    for call in agfs.pathlock_acquire_batch.await_args_list:
        assert call.kwargs.get("timeout_secs") == FS_OP_LOCK_ACQUIRE_WAIT_SECS


@pytest.mark.asyncio
async def test_cp_waits_for_busy_batch_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)
    agfs.stat = AsyncMock(return_value={"isDir": False})
    monkeypatch.setattr(fs, "_ensure_copy_source_access", AsyncMock())
    monkeypatch.setattr(fs, "_ensure_transfer_parent_directory", AsyncMock())
    monkeypatch.setattr(fs, "_ensure_transfer_target_missing", AsyncMock())
    monkeypatch.setattr(fs, "_copy_agfs_entry", AsyncMock(return_value={"files": 1}))

    await fs.cp("viking:///resources/a.md", "viking:///resources/b-copy.md", ctx=None)

    assert agfs.pathlock_acquire_batch.await_count >= 1
    for call in agfs.pathlock_acquire_batch.await_args_list:
        assert call.kwargs.get("timeout_secs") == FS_OP_LOCK_ACQUIRE_WAIT_SECS


@pytest.mark.asyncio
async def test_append_waits_for_busy_exact_lock(monkeypatch):
    fs, agfs = _make_fs(monkeypatch)
    monkeypatch.setattr(fs, "_ensure_parent_dirs", AsyncMock())

    await fs.append_file("viking:///resources/docs/file.md", "more", ctx=None)

    agfs.pathlock_acquire_exact.assert_awaited_once_with(
        "/agfs/resources/docs/file.md", timeout_secs=FS_OP_LOCK_ACQUIRE_WAIT_SECS
    )
    agfs.pathlock_release.assert_awaited_once()
    agfs.write.assert_awaited_once()
