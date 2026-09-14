# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real-binding regressions for deletion snapshots (OpenViking #4966)."""

import asyncio
from dataclasses import replace

import pytest

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

ragfs_python = pytest.importorskip("ragfs_python")


@pytest.fixture
def snapshot_fs(tmp_path):
    storage = tmp_path / "fs"
    storage.mkdir()
    config = tmp_path / "ragfs.toml"
    config.write_text(
        f'[git]\nenabled = true\nbackend = "local"\n[git.local]\nbase_dir = "{tmp_path / "git"}"\n',
        encoding="utf-8",
    )
    client = ragfs_python.RAGFSBindingClient(git_config_path=str(config))
    client.mount("localfs", "/local", {"local_dir": str(storage)})
    try:
        yield VikingFS(agfs=client), storage
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.ROOT, Role.ADMIN, Role.USER])
@pytest.mark.parametrize("deleted_scope", ["file", "parent", "subtree"])
async def test_deletion_snapshot_does_not_recreate_paths(snapshot_fs, role, deleted_scope):
    vfs, storage = snapshot_fs
    ctx = RequestContext(user=UserIdentifier("snapshot_test", "alice"), role=role)
    parent = "viking://user/alice/memories/experiences/nested"
    uri = f"{parent}/example.md"
    sibling = "viking://user/alice/memories/unrelated.md"
    snapshot_path = parent if deleted_scope == "subtree" else uri
    await vfs.write_file(uri, b"original experience", ctx=ctx)
    await vfs.write_file(sibling, b"previous sibling", ctx=ctx)
    first = await vfs.commit(message="create", paths=[snapshot_path, sibling], ctx=ctx)

    deleted_uri = uri if deleted_scope == "file" else parent
    await vfs.rm(deleted_uri, recursive=deleted_scope != "file", ctx=ctx)
    physical = storage / ctx.account_id / vfs._uri_to_tree_path(deleted_uri, ctx=ctx)
    assert not physical.exists()

    # A broader lock must not broaden the paths recorded by the snapshot.
    await vfs.write_file(sibling, b"uncommitted sibling", ctx=ctx)
    second = await vfs.commit(message="record deletion", paths=[snapshot_path], ctx=ctx)

    assert not physical.exists(), "snapshot locking recreated an already-deleted path"
    assert second["result"] == "created"
    assert second["changed"] >= 1
    assert second["commit_oid"] != first["commit_oid"]
    assert await vfs.show(first["commit_oid"], path=uri, ctx=ctx) == b"original experience"
    with pytest.raises(AGFSNotFoundError):
        await vfs.show(second["commit_oid"], path=uri, ctx=ctx)
    assert await vfs.show(second["commit_oid"], path=sibling, ctx=ctx) == b"previous sibling"


@pytest.mark.asyncio
async def test_deletion_snapshot_retains_tree_lock_coverage(snapshot_fs, monkeypatch):
    vfs, storage = snapshot_fs
    ctx = RequestContext(user=UserIdentifier("snapshot_test", "alice"), role=Role.USER)
    uri = "viking://user/alice/memories/experiences/deleted.md"
    path = vfs._uri_to_path(uri, ctx=ctx)
    await vfs.write_file(uri, b"experience", ctx=ctx)
    await vfs.commit(message="create", paths=[uri], ctx=ctx)
    await vfs.rm(uri, ctx=ctx)
    entered = asyncio.Event()
    proceed = asyncio.Event()
    original_run = vfs._async_agfs.run

    async def gated_run(method, *args, **kwargs):
        if method == "git_commit":
            entered.set()
            await proceed.wait()
        return await original_run(method, *args, **kwargs)

    monkeypatch.setattr(vfs._async_agfs, "run", gated_run)
    task = asyncio.create_task(vfs.commit(message="delete", paths=[uri], ctx=ctx))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        for target in (path, f"{path}/child.md"):
            with pytest.raises(LockAcquisitionError):
                await vfs._async_agfs.pathlock_acquire_exact(target, timeout_secs=0)
        with pytest.raises(LockAcquisitionError):
            await vfs._async_agfs.pathlock_acquire_tree(path.rsplit("/", 1)[0], timeout_secs=0)
    finally:
        proceed.set()
        result = await asyncio.wait_for(task, timeout=5)

    assert result["result"] == "created"
    assert not (storage / "snapshot_test/user/alice/memories/experiences/deleted.md").exists()
    await vfs.write_file(uri, b"new experience after snapshot", ctx=ctx)
    assert await vfs.read_file(uri, ctx=ctx) == "new experience after snapshot"


@pytest.mark.asyncio
async def test_deletion_snapshot_still_checks_original_uri_access(snapshot_fs):
    vfs, storage = snapshot_fs
    alice = RequestContext(user=UserIdentifier("snapshot_test", "alice"), role=Role.USER)
    bob = replace(alice, user=UserIdentifier("snapshot_test", "bob"))
    uri = "viking://user/alice/memories/experiences/private.md"
    await vfs.write_file(uri, b"private experience", ctx=alice)
    first = await vfs.commit(message="create", paths=[uri], ctx=alice)
    await vfs.rm(uri, ctx=alice)

    with pytest.raises(PermissionDeniedError):
        await vfs.commit(message="unauthorized deletion snapshot", paths=[uri], ctx=bob)

    assert not (storage / "snapshot_test/user/alice/memories/experiences/private.md").exists()
    assert await vfs.show("main", path=uri, ctx=alice) == b"private experience"
    # A rejected request must also release its locks for the authorized caller.
    second = await vfs.commit(message="authorized deletion snapshot", paths=[uri], ctx=alice)
    assert second["result"] == "created"
    assert second["commit_oid"] != first["commit_oid"]
