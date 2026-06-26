"""Tests for LocalClient git version control methods.

Verifies that LocalClient.{git_commit, git_restore, git_show, git_log}
forward the right kwargs to FSService.{commit, restore, show, log}.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.client.local import LocalClient
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def mock_fs():
    m = MagicMock()
    m.commit = AsyncMock(return_value={"result": "created", "commit_oid": "a" * 40})
    m.restore = AsyncMock(return_value={"result": "applied", "commit_oid": "b" * 40})
    m.show = AsyncMock(return_value={"oid": "c" * 40, "message": "m", "parents": []})
    m.log = AsyncMock(return_value=[{"oid": "c" * 40, "message": "m"}])
    return m


@pytest.fixture
def local_client(mock_fs):
    """Build a LocalClient with a mocked FSService, bypassing __init__."""
    ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="u"),
        role=Role.ROOT,
    )
    client = object.__new__(LocalClient)
    client._service = MagicMock()
    client._service.fs = mock_fs
    client._ctx = ctx
    return client


@pytest.mark.asyncio
async def test_commit_forwards_kwargs(local_client, mock_fs):
    out = await local_client.git_commit(
        message="snapshot",
        paths=["viking://resources/a.md"],
        branch="main",
        author_name="me",
        author_email="me@x",
    )
    mock_fs.commit.assert_awaited_once_with(
        message="snapshot",
        paths=["viking://resources/a.md"],
        branch="main",
        author_name="me",
        author_email="me@x",
        ctx=local_client._ctx,
    )
    assert out["commit_oid"] == "a" * 40


@pytest.mark.asyncio
async def test_commit_defaults(local_client, mock_fs):
    await local_client.git_commit(message="m")
    kwargs = mock_fs.commit.await_args.kwargs
    assert kwargs["paths"] is None
    assert kwargs["branch"] == "main"
    assert kwargs["author_name"] is None
    assert kwargs["author_email"] is None


@pytest.mark.asyncio
async def test_restore_forwards_kwargs(local_client, mock_fs):
    out = await local_client.git_restore(
        project_dir="viking://resources/proj",
        source_commit="d" * 40,
        branch="main",
        dry_run=True,
        message="rollback",
        author_name="me",
        author_email="me@x",
    )
    mock_fs.restore.assert_awaited_once_with(
        project_dir="viking://resources/proj",
        source_commit="d" * 40,
        branch="main",
        dry_run=True,
        message="rollback",
        author_name="me",
        author_email="me@x",
        ctx=local_client._ctx,
    )
    assert out["result"] == "applied"


@pytest.mark.asyncio
async def test_restore_defaults_project_dir_none(local_client, mock_fs):
    await local_client.git_restore(source_commit="d" * 40)
    mock_fs.restore.assert_awaited_once_with(
        project_dir=None,
        source_commit="d" * 40,
        branch="main",
        dry_run=False,
        message=None,
        author_name=None,
        author_email=None,
        ctx=local_client._ctx,
    )


@pytest.mark.asyncio
async def test_show_metadata(local_client, mock_fs):
    out = await local_client.git_show("main")
    mock_fs.show.assert_awaited_once_with("main", path=None, ctx=local_client._ctx)
    assert out["oid"] == "c" * 40


@pytest.mark.asyncio
async def test_show_with_path(local_client, mock_fs):
    mock_fs.show = AsyncMock(return_value=b"blob data")
    out = await local_client.git_show("main", path="viking://resources/a.md")
    mock_fs.show.assert_awaited_once_with("main", path="viking://resources/a.md", ctx=local_client._ctx)
    assert out == b"blob data"


@pytest.mark.asyncio
async def test_log_defaults(local_client, mock_fs):
    out = await local_client.git_log()
    mock_fs.log.assert_awaited_once_with(branch="main", limit=20, ctx=local_client._ctx)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_log_overrides(local_client, mock_fs):
    await local_client.git_log(branch="dev", limit=5)
    mock_fs.log.assert_awaited_once_with(branch="dev", limit=5, ctx=local_client._ctx)
