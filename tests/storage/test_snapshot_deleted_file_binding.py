# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deletion snapshots must not materialize file paths while locking them (#4966)."""

import pytest

from openviking.pyagfs import get_binding_client
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.ROOT, Role.USER])
@pytest.mark.parametrize("filename", ["example.md", "extensionless"])
async def test_snapshot_deleted_file_stays_absent(tmp_path, role, filename):
    client_type, _ = get_binding_client()
    fs_root = tmp_path / "fs"
    fs_root.mkdir()
    config = tmp_path / "ragfs.toml"
    config.write_text(
        f'[git]\nenabled=true\nbackend="local"\n[git.local]\nbase_dir="{tmp_path / "git"}"\n'
    )
    client = client_type(git_config_path=str(config))
    client.mount("localfs", "/local", {"local_dir": str(fs_root)})
    vfs = VikingFS(agfs=client)
    ctx = RequestContext(user=UserIdentifier("test", "alice"), role=role)
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
    # Reading the deletion commit must report absence, not an empty blob/directory.
    with pytest.raises(AGFSNotFoundError):
        await vfs.show(deleted["commit_oid"], path=uri, ctx=ctx)
    repeated = await vfs.commit(message="repeat deletion", paths=[uri], ctx=ctx)
    assert repeated["result"] == "noop"
    assert not target.exists()

    await vfs.write_file(uri, "replacement", ctx=ctx)
    assert target.is_file()
    assert target.read_text() == "replacement"
