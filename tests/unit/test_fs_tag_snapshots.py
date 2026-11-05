"""Tag-driven snapshots and native file restore do not require compile configuration."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def snapshot_service():
    vfs = SimpleNamespace(
        _ensure_access=AsyncMock(),
        commit=AsyncMock(return_value={"commit_oid": "b" * 40, "result": "created"}),
        log=AsyncMock(return_value=[{"oid": "a" * 40}]),
        show=AsyncMock(return_value=b"restored content"),
        stat=AsyncMock(return_value={"isDir": True}),
        mkdir=AsyncMock(),
    )
    service = FSService(viking_fs=vfs)
    service._directory_has_compile_tag = AsyncMock(return_value=False)
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    return service, vfs, ctx


@pytest.mark.asyncio
async def test_tagged_ancestor_snapshot_uses_content_lease(snapshot_service):
    service, vfs, ctx = snapshot_service
    root = "viking://user/user/resources/output"
    uri = f"{root}/nested/file.md"
    service._directory_has_compile_tag.side_effect = lambda parent, ctx: parent == root
    write = AsyncMock(return_value=b"written")
    lease = {"owner": "content"}

    result = await service._snapshot_write([uri], ctx=ctx, lease_ref=lease, write=write)

    assert result == b"written"
    write.assert_awaited_once()
    assert [call.args[0] for call in service._directory_has_compile_tag.await_args_list] == [
        f"{root}/nested",
        root,
    ]
    vfs.commit.assert_awaited_once_with(
        message="content write", paths=[uri], ctx=ctx, lease_ref=lease
    )


@pytest.mark.asyncio
async def test_untagged_write_stops_at_namespace_boundary(snapshot_service):
    service, vfs, ctx = snapshot_service
    uri = "viking://user/user/peers/peer/resources/output/file.md"
    service._directory_has_compile_tag.side_effect = lambda parent, ctx: (
        parent == "viking://user/user"
    )
    write = AsyncMock()

    await service._snapshot_write([uri], ctx=ctx, lease_ref={}, write=write)

    write.assert_awaited_once()
    vfs.commit.assert_not_awaited()
    assert [call.args[0] for call in service._directory_has_compile_tag.await_args_list] == [
        "viking://user/user/peers/peer/resources/output",
        "viking://user/user/peers/peer/resources",
    ]


@pytest.mark.asyncio
async def test_snapshot_failure_is_returned_after_write(snapshot_service):
    service, vfs, ctx = snapshot_service
    service._directory_has_compile_tag.return_value = True
    vfs.commit.side_effect = OSError("snapshot failed")
    write = AsyncMock()

    with pytest.raises(OSError, match="snapshot failed"):
        await service._snapshot_write(
            ["viking://resources/output/file.md"], ctx=ctx, lease_ref={}, write=write
        )
    write.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_versions_page_without_global_metadata_access(snapshot_service):
    service, vfs, ctx = snapshot_service
    cursor = "a" * 40
    vfs.log.return_value = [{"oid": cursor}, {"oid": "b" * 40}, {"oid": "c" * 40}]

    result = await service.file_versions("viking://resources/file.md", ctx, limit=1, before=cursor)

    assert result["versions"] == [{"oid": "b" * 40}]
    assert result["next_before_snapshot"] == "b" * 40
    vfs.show.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_file_restore_rechecks_commit_under_content_lock(snapshot_service, stale):
    service, vfs, ctx = snapshot_service
    uri = "viking://resources/ordinary.md"
    vfs.log.side_effect = [[{"oid": "a" * 40}], [{"oid": ("c" if stale else "a") * 40}]]
    write = AsyncMock()

    async def locked_write(uri, content, ctx, *, mode, commit_content):
        assert mode == "replace" and content == "restored content"
        return await commit_content([uri], lease_ref={"owner": "restore"}, write=write)

    service.write = locked_write
    if stale:
        with pytest.raises(ConflictError):
            await service.restore_file_version(uri, "f" * 40, "a" * 40, ctx)
        write.assert_not_awaited()
        vfs.commit.assert_not_awaited()
    else:
        result = await service.restore_file_version(uri, "f" * 40, "a" * 40, ctx)
        assert result == {"uri": uri, "snapshot_id": "b" * 40, "result": "created"}
        write.assert_awaited_once()
        assert vfs.commit.await_args.kwargs["lease_ref"] == {"owner": "restore"}
    service._directory_has_compile_tag.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_restore_dry_run_does_not_write(snapshot_service):
    service, vfs, ctx = snapshot_service
    service.write = AsyncMock()
    result = await service.restore_file_version(
        "viking://resources/ordinary.md", "f" * 40, "a" * 40, ctx, dry_run=True
    )
    assert result["result"] == "dry_run"
    service.write.assert_not_awaited()
    vfs.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "directory", "file"])
async def test_ensure_compile_target_preserves_existing_directory(snapshot_service, kind):
    service, vfs, ctx = snapshot_service
    uri = "viking://resources/output"
    if kind == "missing":
        vfs.stat.side_effect = FileNotFoundError(uri)
    else:
        vfs.stat.return_value = {"isDir": kind == "directory"}
    service._existing_tree_lock_request = AsyncMock(return_value={"path": "parent"})
    service.finalize_compile_directories = AsyncMock(return_value={"directory_count": 1})

    @asynccontextmanager
    async def hold_scope(*args):
        yield {"owner": "directory"}

    service._hold_watch_scope = hold_scope
    if kind == "file":
        with pytest.raises(InvalidArgumentError):
            await service.ensure_compile_target(uri, ctx)
        service.finalize_compile_directories.assert_not_awaited()
    else:
        await service.ensure_compile_target(uri, ctx)
        service.finalize_compile_directories.assert_awaited_once_with(uri, ctx)
    assert vfs.mkdir.await_count == (kind == "missing")
    vfs.commit.assert_not_awaited()
