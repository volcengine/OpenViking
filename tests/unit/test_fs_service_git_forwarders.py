"""Tests for FSService git forwarder methods.

These tests verify FSService.{commit, restore, show, log} pass the right
args to VikingFS. They don't exercise real git — that's covered by
tests/agfs/test_viking_fs_git.py.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.exceptions import NotInitializedError
from openviking_cli.session.user_id import UserIdentifier


def _ctx():
    return RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user"),
        role=Role.ROOT,
    )


@pytest.fixture
def viking_fs_mock():
    m = MagicMock()
    m.commit = AsyncMock(return_value={"result": "created", "commit_oid": "a" * 40, "changed": 1})
    m.restore = AsyncMock(return_value={"result": "applied", "commit_oid": "b" * 40})
    m.show = AsyncMock(return_value={"oid": "c" * 40, "message": "m", "parents": []})
    m.log = AsyncMock(return_value=[{"oid": "c" * 40, "message": "m"}])
    return m


@pytest.fixture
def svc(viking_fs_mock):
    s = FSService(viking_fs=viking_fs_mock)
    return s


@pytest.mark.asyncio
async def test_commit_forwards_all_kwargs(svc, viking_fs_mock):
    ctx = _ctx()
    out = await svc.commit(
        message="snapshot",
        ctx=ctx,
        paths=["viking://resources/a.md"],
        branch="main",
        author_name="me",
        author_email="me@x",
    )
    viking_fs_mock.commit.assert_awaited_once_with(
        message="snapshot",
        paths=["viking://resources/a.md"],
        branch="main",
        author_name="me",
        author_email="me@x",
        ctx=ctx,
    )
    assert out["commit_oid"] == "a" * 40


@pytest.mark.asyncio
async def test_commit_defaults_paths_to_none(svc, viking_fs_mock):
    ctx = _ctx()
    await svc.commit(message="m", ctx=ctx)
    kwargs = viking_fs_mock.commit.await_args.kwargs
    assert kwargs["paths"] is None
    assert kwargs["branch"] == "main"
    assert kwargs["author_name"] is None
    assert kwargs["author_email"] is None


@pytest.mark.asyncio
async def test_restore_forwards_all_kwargs(svc, viking_fs_mock):
    ctx = _ctx()
    out = await svc.restore(
        project_dir="viking://resources/proj",
        source_commit="d" * 40,
        ctx=ctx,
        branch="main",
        dry_run=True,
        message="rolling back",
        author_name="me",
        author_email="me@x",
    )
    viking_fs_mock.restore.assert_awaited_once_with(
        project_dir="viking://resources/proj",
        source_commit="d" * 40,
        branch="main",
        dry_run=True,
        message="rolling back",
        author_name="me",
        author_email="me@x",
        ctx=ctx,
    )
    assert out["result"] == "applied"


@pytest.mark.asyncio
async def test_show_metadata_without_path(svc, viking_fs_mock):
    ctx = _ctx()
    out = await svc.show("main", ctx=ctx)
    viking_fs_mock.show.assert_awaited_once_with("main", path=None, ctx=ctx)
    assert out["oid"] == "c" * 40


@pytest.mark.asyncio
async def test_show_with_path_validated(svc, viking_fs_mock):
    ctx = _ctx()
    viking_fs_mock.show = AsyncMock(return_value=b"hello")
    out = await svc.show("main", ctx=ctx, path="viking://resources/a.md")
    viking_fs_mock.show.assert_awaited_once_with(
        "main", path="viking://resources/a.md", ctx=ctx
    )
    assert out == b"hello"


@pytest.mark.asyncio
async def test_log_defaults(svc, viking_fs_mock):
    ctx = _ctx()
    out = await svc.log(ctx=ctx)
    viking_fs_mock.log.assert_awaited_once_with(branch="main", limit=20, ctx=ctx)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_log_with_overrides(svc, viking_fs_mock):
    ctx = _ctx()
    await svc.log(ctx=ctx, branch="dev", limit=5)
    viking_fs_mock.log.assert_awaited_once_with(branch="dev", limit=5, ctx=ctx)


@pytest.mark.asyncio
async def test_methods_raise_when_not_initialized():
    svc = FSService()  # no viking_fs set
    ctx = _ctx()
    with pytest.raises(NotInitializedError):
        await svc.commit(message="m", ctx=ctx)
    with pytest.raises(NotInitializedError):
        await svc.restore(project_dir="viking://x", source_commit="a", ctx=ctx)
    with pytest.raises(NotInitializedError):
        await svc.show("main", ctx=ctx)
    with pytest.raises(NotInitializedError):
        await svc.log(ctx=ctx)
