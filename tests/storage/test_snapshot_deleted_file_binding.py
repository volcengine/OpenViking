# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Committing a deleted path must not recreate it (#4966).

Non-ROOT commits took a Tree lock on every explicit path. For a deleted
file the lock resolver materialized ``example.md/`` as a directory to hold
the token, and the deletion was then committed as a noop. Missing targets
now get no lock on the filesystem provider (Tree on the Redis provider);
existing files get Exact, directories Tree.
"""

import pytest

from openviking.pyagfs import get_binding_client
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def snapshot_env(tmp_path):
    client_type, _ = get_binding_client()
    fs_root = tmp_path / "fs"
    fs_root.mkdir()
    config = tmp_path / "ragfs.toml"
    config.write_text(
        f'[git]\nenabled=true\nbackend="local"\n[git.local]\nbase_dir="{tmp_path / "git"}"\n'
    )
    client = client_type(git_config_path=str(config))
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

    await vfs.commit(message="filesystem provider", paths=targets, ctx=ctx)
    assert seen[-1] == [("dir", "tree"), ("file.md", "exact")]

    monkeypatch.setattr(
        type(vfs), "_snapshot_missing_target_lock_kind", staticmethod(lambda: "tree")
    )
    await vfs.commit(message="cache provider", paths=targets, ctx=ctx)
    assert seen[-1] == [("dir", "tree"), ("file.md", "exact"), ("missing.md", "tree")]
