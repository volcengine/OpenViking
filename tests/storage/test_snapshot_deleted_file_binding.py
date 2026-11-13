# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Committing a deleted path must not recreate it (#4966).

Non-ROOT commits took a Tree lock on every explicit path. For a deleted
file the lock resolver materialized ``example.md/`` as a directory to hold
the token, and the deletion was then committed as a noop. Missing targets
now get no lock on the filesystem provider (Tree on logical-path providers);
existing files get Exact, directories Tree.
"""

import os
import uuid

import pytest

from openviking.pyagfs import get_binding_client
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(params=["filesystem", "memory", "sqlite", "postgres"])
def snapshot_binding_config(tmp_path, request):
    provider = request.param
    if provider == "postgres" and not os.environ.get("OV_TEST_PG_DSN"):
        pytest.skip("set OV_TEST_PG_DSN to a disposable PostgreSQL database")
    fs_root = tmp_path / "fs"
    fs_root.mkdir()
    config = tmp_path / "ragfs.toml"
    config.write_text(
        f'[git]\nenabled=true\nbackend="local"\n[git.local]\nbase_dir="{tmp_path / "git"}"\n'
    )
    pathlock = {"provider": provider}
    if provider == "sqlite":
        pathlock["sqlite_path"] = str(tmp_path / "pathlock.db")
    elif provider == "postgres":
        pathlock["postgres_dsn_env"] = "OV_TEST_PG_DSN"
    return {
        "git_config_path": str(config),
        "config": {"service_name": f"snapshot-{uuid.uuid4().hex}", "pathlock": pathlock},
    }, fs_root


@pytest.fixture
def snapshot_env(snapshot_binding_config):
    client_type, _ = get_binding_client()
    kwargs, fs_root = snapshot_binding_config
    client = client_type(**kwargs)
    client.mount("localfs", "/local", {"local_dir": str(fs_root)})
    return VikingFS(agfs=client), fs_root


def _ctx(role):
    return RequestContext(user=UserIdentifier("test", "alice"), role=role)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.ROOT, Role.USER])
@pytest.mark.parametrize("filename", ["example.md", "extensionless"])
async def test_commit_deleted_file_stays_absent(snapshot_env, role, filename):
    vfs, fs_root = snapshot_env
    ctx = _ctx(role)
    uri = f"viking://user/alice/memories/experiences/{filename}"
    target = fs_root / f"test/user/alice/memories/experiences/{filename}"

    await vfs.write_file(uri, "original experience", ctx=ctx)
    created = await vfs.commit(message="create", paths=[uri], ctx=ctx)
    await vfs.rm(uri, ctx=ctx)
    assert not target.exists()
    entries_after_delete = sorted(p.name for p in target.parent.iterdir())

    deleted = await vfs.commit(message="record deletion", paths=[uri], ctx=ctx)
    assert not target.exists(), "snapshot locking recreated the deleted file as a directory"
    assert sorted(p.name for p in target.parent.iterdir()) == entries_after_delete
    assert deleted["result"] == "created"
    assert deleted["commit_oid"] != created["commit_oid"]
    assert await vfs.show(created["commit_oid"], path=uri, ctx=ctx) == b"original experience"
    with pytest.raises(AGFSNotFoundError):
        await vfs.show(deleted["commit_oid"], path=uri, ctx=ctx)
    repeated = await vfs.commit(message="repeat deletion", paths=[uri], ctx=ctx)
    assert repeated["result"] == "noop"
    assert not target.exists()

    await vfs.write_file(uri, "replacement", ctx=ctx)
    assert target.is_file()
    assert target.read_text() == "replacement"


@pytest.mark.asyncio
async def test_commit_deleted_directory_stays_absent(snapshot_env):
    vfs, fs_root = snapshot_env
    ctx = _ctx(Role.USER)
    directory = "viking://user/alice/memories/experiences/topic"
    child = f"{directory}/note.md"
    target = fs_root / "test/user/alice/memories/experiences/topic"

    await vfs.write_file(child, "content", ctx=ctx)
    first = await vfs.commit(message="dir", paths=[directory], ctx=ctx)
    await vfs.rm(directory, recursive=True, ctx=ctx)
    assert not target.exists()

    deleted = await vfs.commit(message="drop dir", paths=[directory], ctx=ctx)
    assert not target.exists(), "snapshot locking recreated the deleted directory"
    assert deleted["result"] == "created"
    assert await vfs.show(first["commit_oid"], path=child, ctx=ctx) == b"content"
    with pytest.raises(AGFSNotFoundError):
        await vfs.show(deleted["commit_oid"], path=child, ctx=ctx)


@pytest.mark.asyncio
async def test_commit_lock_kinds_follow_target_state(snapshot_env, monkeypatch):
    vfs, _fs_root = snapshot_env
    ctx = _ctx(Role.USER)
    base = "viking://user/alice/memories/experiences"
    await vfs.write_file(f"{base}/dir/a.md", "a", ctx=ctx)
    await vfs.write_file(f"{base}/file.md", "f", ctx=ctx)
    await vfs.commit(message="seed", paths=[base], ctx=ctx)

    seen = []
    acquire = vfs._async_agfs.pathlock_acquire_batch

    async def recording_acquire(requests, *args, **kwargs):
        seen.append(sorted((r["path"].rsplit("/", 1)[1], r["kind"]) for r in requests))
        return await acquire(requests, *args, **kwargs)

    monkeypatch.setattr(vfs._async_agfs, "pathlock_acquire_batch", recording_acquire)
    targets = [f"{base}/dir", f"{base}/file.md", f"{base}/missing.md"]

    await vfs.commit(message="lock by runtime capability", paths=targets, ctx=ctx)
    expected = [("dir", "tree"), ("file.md", "exact")]
    if vfs.agfs.pathlock_handle_mode() == "logical_path":
        expected.append(("missing.md", "tree"))
    assert seen[-1] == expected
    assert not (_fs_root / "test/user/alice/memories/experiences/missing.md").exists()


@pytest.mark.parametrize("owner", ["", "alice", "bob"])
@pytest.mark.parametrize(
    "directory",
    ["", "memories", "resources", "peers", "skills", "sessions", "privacy", "memories-extra"],
)
async def test_global_snapshot_scope(snapshot_env, owner, directory):
    vfs, fs_root = snapshot_env
    ctx = _ctx(Role.GLOBAL)
    scope = f"{owner}/{directory}".strip("/")
    uri = f"viking://user/{scope}".rstrip("/")
    allowed = owner == "alice" or (
        owner == "bob" and directory in {"memories", "resources", "peers", "skills"}
    )
    service = FSService(viking_fs=vfs)
    if not allowed:
        with pytest.raises(PermissionDeniedError):
            await vfs.commit(message="snapshot", paths=[uri], ctx=ctx)
        with pytest.raises(PermissionDeniedError):
            await service.diff_paths([uri], from_ref=None, to_ref="main", ctx=ctx)
        return
    target = fs_root / "test/user" / scope / "note.md"
    target.parent.mkdir(parents=True)
    target.write_text("content")
    snapshot = await vfs.commit(message="snapshot", paths=[uri], ctx=ctx)
    page = await service.diff_paths([uri], from_ref=None, to_ref=snapshot["commit_oid"], ctx=ctx)
    assert page["changes"] == [{"uri": f"{uri}/note.md", "change_type": "added"}]
    if scope.startswith("bob/"):
        with pytest.raises(PermissionDeniedError):
            await vfs.show(snapshot["commit_oid"], path=f"{uri}/note.md", ctx=ctx)

@pytest.mark.asyncio
async def test_missing_target_tree_lock_blocks_recreation(
    snapshot_env, snapshot_binding_config, monkeypatch
):
    vfs, fs_root = snapshot_env
    kwargs, _ = snapshot_binding_config
    provider = kwargs["config"]["pathlock"]["provider"]
    if provider == "filesystem":
        pytest.skip("filesystem cannot lock a missing target without recreating it")

    if provider == "memory":
        peer = vfs._async_agfs  # Memory coordinates only users of the same client.
    else:
        client_type, _ = get_binding_client()
        client = client_type(**kwargs)
        client.mount("localfs", "/local", {"local_dir": str(fs_root)})
        peer = VikingFS(agfs=client)._async_agfs

    ctx = _ctx(Role.USER)
    uri = "viking://user/alice/memories/experiences/deleted.md"
    path = "/local/test/user/alice/memories/experiences/deleted.md"
    target = fs_root / "test/user/alice/memories/experiences/deleted.md"
    await vfs.write_file(uri, "old content", ctx=ctx)
    await vfs.commit(message="seed", paths=[uri], ctx=ctx)
    await vfs.rm(uri, ctx=ctx)
    run = vfs._async_agfs.run
    checked = False

    async def check_coverage(method, *args, **kwargs):
        nonlocal checked
        if method == "git_commit":
            assert not target.exists()
            for conflicting_path in (path, f"{path}/child.md"):
                with pytest.raises(LockAcquisitionError):
                    await peer.pathlock_acquire_exact(conflicting_path)
            checked = True
        return await run(method, *args, **kwargs)

    monkeypatch.setattr(vfs._async_agfs, "run", check_coverage)
    result = await vfs.commit(message="record deletion", paths=[uri], ctx=ctx)
    assert checked and result["result"] == "created"
    assert not target.exists()
    lease = await peer.pathlock_acquire_exact(path)
    await peer.pathlock_release(lease)
