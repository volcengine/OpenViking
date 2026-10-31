"""Context summaries share a lease without bypassing the normal write path."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import body_for_preview
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier


class ContextAGFS:
    def __init__(self):
        self.events = []
        self.storage = {}
        self.lease = {"lease_ref": "context-lease"}
        self.failure = None

    async def mkdir(self, path, fs_ctx=None):
        self.events.append(("mkdir", path))

    async def pathlock_acquire_exact_batch(self, paths):
        self.events.append(("acquire", paths))
        return self.lease

    async def pathlock_release(self, lease):
        self.events.append(("release", lease))

    async def write(self, path, data, fs_ctx=None, auto_pathlock=True):
        self.events.append(("write", path, fs_ctx))
        if self.failure is not None and path.endswith(".overview.md"):
            raise self.failure
        self.storage[path] = data


def context_fs(monkeypatch):
    agfs = ContextAGFS()
    fs = VikingFS(agfs=object())
    fs._async_agfs = agfs
    access = AsyncMock()
    monkeypatch.setattr(fs, "_ensure_access", access)
    monkeypatch.setattr(fs, "_ensure_parent_dirs", AsyncMock())
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)
    return fs, agfs, access, ctx


@pytest.mark.asyncio
async def test_context_summary_pair_uses_one_lease_and_normal_file_writes(monkeypatch):
    fs, agfs, access, ctx = context_fs(monkeypatch)
    hook = AsyncMock()
    fs.set_post_write_hook(hook)

    await fs.write_context("viking://resources", abstract="L0", overview="L1", ctx=ctx)

    writes = [event for event in agfs.events if event[0] == "write"]
    acquisitions = [event for event in agfs.events if event[0] == "acquire"]
    assert len(acquisitions) == 1
    assert acquisitions[0][1] == [event[1] for event in writes]
    assert len(writes) == 2
    assert all(event[2]["lease_ref"] == "context-lease" for event in writes)
    assert agfs.events[-1] == ("release", agfs.lease)
    assert [body_for_preview(data.decode()) for data in agfs.storage.values()] == ["L0", "L1"]
    assert [call.args[0] for call in access.await_args_list] == [
        "viking://resources",
        "viking://resources/.abstract.md",
        "viking://resources/.overview.md",
    ]
    assert hook.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("write failed"), asyncio.CancelledError()])
async def test_context_failure_keeps_completed_write_and_releases_lease(monkeypatch, failure):
    fs, agfs, _, ctx = context_fs(monkeypatch)
    agfs.failure = failure

    with pytest.raises(
        asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else OSError
    ):
        await fs.write_context("viking://resources", abstract="L0", overview="L1", ctx=ctx)

    assert len(agfs.storage) == 1
    assert next(iter(agfs.storage)).endswith(".abstract.md")
    assert agfs.events[-1] == ("release", agfs.lease)


@pytest.mark.asyncio
async def test_context_outer_lease_stays_owned_by_caller(monkeypatch):
    fs, agfs, _, ctx = context_fs(monkeypatch)
    outer = {"lease_ref": "outer"}

    await fs.write_context(
        "viking://resources", abstract="L0", overview="L1", ctx=ctx, lease_ref=outer
    )

    assert not any(event[0] in {"acquire", "release"} for event in agfs.events)
    writes = [event for event in agfs.events if event[0] == "write"]
    assert len(writes) == 2
    assert all(event[2]["lease_ref"] == "outer" for event in writes)


@pytest.mark.asyncio
async def test_single_summary_retains_automatic_file_locking(monkeypatch):
    fs, agfs, _, ctx = context_fs(monkeypatch)

    await fs.write_context("viking://resources", abstract="L0", ctx=ctx)

    assert not any(event[0] in {"acquire", "release"} for event in agfs.events)
    assert len(agfs.storage) == 1


@pytest.mark.asyncio
async def test_context_batch_busy_does_not_write_or_release_unowned_lease(monkeypatch):
    fs, agfs, _, ctx = context_fs(monkeypatch)
    agfs.pathlock_acquire_exact_batch = AsyncMock(side_effect=RuntimeError("busy"))

    with pytest.raises(OSError, match="busy"):
        await fs.write_context("viking://resources", abstract="L0", overview="L1", ctx=ctx)

    assert not agfs.storage
    assert not any(event[0] == "release" for event in agfs.events)


@pytest.mark.asyncio
async def test_context_cancel_during_acquire_settles_and_releases(monkeypatch):
    fs, agfs, _, ctx = context_fs(monkeypatch)
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def acquire(paths):
        started.set()
        await proceed.wait()
        return agfs.lease

    agfs.pathlock_acquire_exact_batch = acquire
    task = asyncio.create_task(
        fs.write_context("viking://resources", abstract="L0", overview="L1", ctx=ctx)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert not agfs.storage
    assert agfs.events[-1] == ("release", agfs.lease)
