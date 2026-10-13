# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content write wait-tracker lock ordering.

The direct-write path takes its exact-path lock through the VikingFS path-lock
lease API (``_async_agfs.pathlock_acquire_exact`` / ``pathlock_release``);
``_FakeAGFS`` stands in for that client.
"""

import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking_cli.session.user_id import UserIdentifier


class _FakeAGFS:
    """Minimal stand-in for the AGFS path-lock lease client."""

    def __init__(self, *, acquire_error=None, release_error=None, on_acquire=None, on_release=None):
        self.acquire_error = acquire_error
        self.release_error = release_error
        self.on_acquire = on_acquire
        self.on_release = on_release
        self.acquired_paths = []
        self.released_leases = []
        self.release_count = 0

    async def pathlock_acquire_exact(self, path):
        self.acquired_paths.append(path)
        if self.on_acquire is not None:
            await self.on_acquire()
        if self.acquire_error is not None:
            raise self.acquire_error
        return {"path": path, "token": "lease-1"}

    async def pathlock_release(self, lease):
        self.released_leases.append(lease)
        self.release_count += 1
        if self.on_release is not None:
            await self.on_release()
        if self.release_error is not None:
            raise self.release_error


class _FakeVikingFS:
    def __init__(self, agfs):
        self._async_agfs = agfs

    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/')}"


@pytest.mark.asyncio
async def test_direct_write_registers_wait_tracker_before_lock_and_cleans_on_busy():
    telemetry_id = "telemetry-before-lock"
    registered_during_acquire = []

    async def on_acquire():
        registered_during_acquire.append(telemetry_id in get_request_wait_tracker()._states)

    agfs = _FakeAGFS(acquire_error=LockAcquisitionError("busy"), on_acquire=on_acquire)
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)
    coordinator = ContentWriteCoordinator(_FakeVikingFS(agfs))
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)

    with pytest.raises(ResourceBusyError):
        await coordinator._write_direct_with_refresh(
            uri="viking://resources/doc.md",
            root_uri="viking://resources/doc.md",
            content="updated",
            mode="replace",
            context_type="resource",
            wait=True,
            timeout=0.1,
            ctx=ctx,
            written_bytes=7,
            telemetry_id=telemetry_id,
        )

    assert registered_during_acquire == [True]
    assert agfs.acquired_paths == ["/fake/viking/resources/doc.md"]
    # A failed acquisition holds no lease, so nothing may be released.
    assert agfs.release_count == 0
    assert telemetry_id not in tracker._states


async def _run_direct_write(coordinator, ctx, telemetry_id):
    return await coordinator._write_direct_with_refresh(
        uri="viking://resources/doc.md",
        root_uri="viking://resources/doc.md",
        content="updated",
        mode="replace",
        context_type="resource",
        wait=True,
        timeout=0.1,
        ctx=ctx,
        written_bytes=7,
        telemetry_id=telemetry_id,
    )


@pytest.mark.asyncio
async def test_direct_write_cleans_wait_tracker_when_lock_acquisition_raises():
    telemetry_id = "telemetry-acquire-error"
    agfs = _FakeAGFS(acquire_error=RuntimeError("acquire failed"))
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)
    coordinator = ContentWriteCoordinator(_FakeVikingFS(agfs))
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)

    with pytest.raises(RuntimeError, match="acquire failed"):
        await _run_direct_write(coordinator, ctx, telemetry_id)

    assert agfs.release_count == 0
    assert telemetry_id not in tracker._states


@pytest.mark.asyncio
@pytest.mark.parametrize("release_error", [RuntimeError("release failed"), asyncio.CancelledError()])
async def test_direct_write_busy_cleans_tracker_and_preserves_busy_error(release_error):
    telemetry_id = "telemetry-busy-release-error"
    agfs = _FakeAGFS(
        acquire_error=LockAcquisitionError("busy"),
        release_error=release_error,
    )
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)
    coordinator = ContentWriteCoordinator(_FakeVikingFS(agfs))
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)

    with pytest.raises(ResourceBusyError):
        await _run_direct_write(coordinator, ctx, telemetry_id)

    assert agfs.release_count == 0
    assert telemetry_id not in tracker._states


@pytest.mark.asyncio
async def test_direct_write_cancellation_after_acquire_releases_lock(monkeypatch):
    telemetry_id = "telemetry-cancel-after-acquire"
    agfs = _FakeAGFS()
    coordinator = ContentWriteCoordinator(_FakeVikingFS(agfs))

    async def cancel_write(*args, **kwargs):
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(coordinator, "_write_in_place", cancel_write)
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)

    with pytest.raises(asyncio.CancelledError):
        await coordinator._write_direct_with_refresh(
            uri="viking://resources/doc.md",
            root_uri="viking://resources/doc.md",
            content="updated",
            mode="create",
            context_type="resource",
            wait=True,
            timeout=0.1,
            ctx=ctx,
            written_bytes=7,
            telemetry_id=telemetry_id,
        )

    assert agfs.release_count == 1
    assert telemetry_id not in tracker._states


@pytest.mark.asyncio
async def test_direct_write_cancellation_during_release_finishes_release(monkeypatch):
    telemetry_id = "telemetry-cancel-during-release"
    release_started = asyncio.Event()
    release_allowed = asyncio.Event()

    async def on_release():
        release_started.set()
        await release_allowed.wait()

    agfs = _FakeAGFS(on_release=on_release)
    coordinator = ContentWriteCoordinator(_FakeVikingFS(agfs))

    async def no_op(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(coordinator, "_write_in_place", no_op)
    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", no_op)
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)

    task = asyncio.create_task(
        coordinator._write_direct_with_refresh(
            uri="viking://resources/doc.md",
            root_uri="viking://resources/doc.md",
            content="updated",
            mode="create",
            context_type="resource",
            wait=True,
            timeout=0.1,
            ctx=ctx,
            written_bytes=7,
            telemetry_id=telemetry_id,
        )
    )
    await release_started.wait()
    task.cancel()
    release_allowed.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert agfs.release_count == 1
    assert telemetry_id not in tracker._states


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary_error", "rollback_error"),
    [
        (RuntimeError("enqueue failed"), RuntimeError("rollback failed")),
        (asyncio.CancelledError(), asyncio.CancelledError()),
    ],
)
async def test_direct_write_rollback_failure_preserves_primary_and_releases_lock(
    monkeypatch, primary_error, rollback_error
):
    telemetry_id = "telemetry-rollback-cleanup-failure"
    agfs = _FakeAGFS()
    coordinator = ContentWriteCoordinator(_FakeVikingFS(agfs))

    async def no_op(*args, **kwargs):
        del args, kwargs

    async def fail_enqueue(*args, **kwargs):
        del args, kwargs
        raise primary_error

    async def fail_rollback(*args, **kwargs):
        del args, kwargs
        raise rollback_error

    monkeypatch.setattr(coordinator, "_write_in_place", no_op)
    monkeypatch.setattr(coordinator, "_enqueue_semantic_refresh", fail_enqueue)
    monkeypatch.setattr(coordinator, "_rollback_direct_write", fail_rollback)
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)

    with pytest.raises(type(primary_error), match=str(primary_error) or None):
        await coordinator._write_direct_with_refresh(
            uri="viking://resources/doc.md",
            root_uri="viking://resources/doc.md",
            content="updated",
            mode="create",
            context_type="resource",
            wait=True,
            timeout=0.1,
            ctx=ctx,
            written_bytes=7,
            telemetry_id=telemetry_id,
        )

    assert agfs.release_count == 1
    assert telemetry_id not in tracker._states
