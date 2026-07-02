"""Unit tests for AsyncSnapshotNamespace and SyncSnapshotNamespace.

These tests verify the namespace classes forward to the underlying
client's git_* methods correctly. They don't exercise real git.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.snapshot_namespace import AsyncSnapshotNamespace, SyncSnapshotNamespace


@pytest.fixture
def fake_async_client():
    """A fake AsyncOpenViking with a mocked _client (BaseClient)."""
    parent = MagicMock()
    parent._ensure_initialized = AsyncMock(return_value=None)
    parent._client = MagicMock()
    parent._client.git_commit = AsyncMock(return_value={"result": "created", "commit_oid": "a" * 40})
    parent._client.git_restore = AsyncMock(return_value={"result": "applied", "commit_oid": "b" * 40})
    parent._client.git_show = AsyncMock(return_value={"oid": "c" * 40, "parents": []})
    parent._client.git_log = AsyncMock(return_value=[{"oid": "c" * 40}])
    return parent


@pytest.fixture
def async_ns(fake_async_client):
    return AsyncSnapshotNamespace(fake_async_client)


# -------- AsyncSnapshotNamespace --------


@pytest.mark.asyncio
async def test_async_commit_forwards(async_ns, fake_async_client):
    out = await async_ns.commit(message="m", paths=["viking://x/a"], branch="dev",
                                 author_name="me", author_email="me@x")
    fake_async_client._ensure_initialized.assert_awaited()
    fake_async_client._client.git_commit.assert_awaited_once_with(
        message="m", paths=["viking://x/a"], branch="dev",
        author_name="me", author_email="me@x",
    )
    assert out["commit_oid"] == "a" * 40


@pytest.mark.asyncio
async def test_async_commit_defaults(async_ns, fake_async_client):
    await async_ns.commit(message="m")
    kwargs = fake_async_client._client.git_commit.await_args.kwargs
    assert kwargs == {
        "message": "m", "paths": None, "branch": "main",
        "author_name": None, "author_email": None,
    }


@pytest.mark.asyncio
async def test_async_restore_forwards(async_ns, fake_async_client):
    out = await async_ns.restore(
        project_dir="viking://resources/proj",
        source_commit="d" * 40,
        dry_run=True,
        message="rollback",
    )
    fake_async_client._client.git_restore.assert_awaited_once_with(
        project_dir="viking://resources/proj",
        source_commit="d" * 40,
        branch="main",
        dry_run=True,
        message="rollback",
        author_name=None,
        author_email=None,
    )
    assert out["result"] == "applied"


@pytest.mark.asyncio
async def test_async_restore_defaults_project_dir_none(async_ns, fake_async_client):
    await async_ns.restore(source_commit="d" * 40)
    fake_async_client._client.git_restore.assert_awaited_once_with(
        project_dir=None,
        source_commit="d" * 40,
        branch="main",
        dry_run=False,
        message=None,
        author_name=None,
        author_email=None,
    )


@pytest.mark.asyncio
async def test_async_show_no_path(async_ns, fake_async_client):
    out = await async_ns.show("main")
    fake_async_client._client.git_show.assert_awaited_once_with("main", path=None)
    assert out["oid"] == "c" * 40


@pytest.mark.asyncio
async def test_async_show_with_path(async_ns, fake_async_client):
    fake_async_client._client.git_show = AsyncMock(return_value=b"data")
    out = await async_ns.show("main", path="viking://x/a")
    fake_async_client._client.git_show.assert_awaited_once_with("main", path="viking://x/a")
    assert out == b"data"


@pytest.mark.asyncio
async def test_async_log_defaults(async_ns, fake_async_client):
    out = await async_ns.log()
    fake_async_client._client.git_log.assert_awaited_once_with(branch="main", limit=20)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_async_log_overrides(async_ns, fake_async_client):
    await async_ns.log(branch="dev", limit=5)
    fake_async_client._client.git_log.assert_awaited_once_with(branch="dev", limit=5)


@pytest.mark.asyncio
async def test_async_ensures_initialized_before_every_call(async_ns, fake_async_client):
    await async_ns.commit(message="m")
    await async_ns.show("main")
    await async_ns.log()
    assert fake_async_client._ensure_initialized.await_count == 3


# -------- SyncSnapshotNamespace --------


def test_sync_namespace_delegates_through_async(monkeypatch):
    """SyncSnapshotNamespace.commit() runs the async equivalent via run_async."""
    # Build a fake SyncOpenViking exposing an async_client with a snapshot namespace.
    sync_parent = MagicMock()
    inner_async_ns = MagicMock()
    inner_async_ns.commit = AsyncMock(return_value={"commit_oid": "z" * 40})
    inner_async_ns.restore = AsyncMock(return_value={"result": "applied"})
    inner_async_ns.show = AsyncMock(return_value=b"blob")
    inner_async_ns.log = AsyncMock(return_value=[])
    sync_parent._async_client.snapshot = inner_async_ns

    sync_ns = SyncSnapshotNamespace(sync_parent)

    out = sync_ns.commit(message="m")
    assert out["commit_oid"] == "z" * 40
    inner_async_ns.commit.assert_awaited_once_with(
        message="m", paths=None, branch="main",
        author_name=None, author_email=None,
    )

    sync_ns.show("main", path="viking://x/a")
    inner_async_ns.show.assert_awaited_once_with("main", path="viking://x/a")

    sync_ns.log(branch="dev", limit=3)
    inner_async_ns.log.assert_awaited_once_with(branch="dev", limit=3)


def test_async_client_snapshot_property_is_lazy_and_cached():
    """Accessing .snapshot twice returns the same instance and doesn't construct early."""
    from openviking.async_client import AsyncOpenViking
    # Avoid real construction by faking the singleton.
    inst = object.__new__(AsyncOpenViking)
    # Patch the lazy attribute machinery
    assert not hasattr(inst, "_snapshot")
    ns1 = inst.snapshot
    assert isinstance(ns1, AsyncSnapshotNamespace)
    ns2 = inst.snapshot
    assert ns1 is ns2


def test_sync_client_snapshot_property_is_lazy_and_cached():
    from openviking.sync_client import SyncOpenViking
    inst = object.__new__(SyncOpenViking)
    assert not hasattr(inst, "_snapshot")
    ns1 = inst.snapshot
    assert isinstance(ns1, SyncSnapshotNamespace)
    ns2 = inst.snapshot
    assert ns1 is ns2
