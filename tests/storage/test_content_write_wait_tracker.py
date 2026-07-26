# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content write wait-tracker lock ordering."""

import asyncio

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.errors import ResourceBusyError
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def _uri_to_path(self, uri, ctx=None):
        del ctx
        return f"/fake/{uri.replace('://', '/')}"


class _FakeHandle:
    id = "handle-1"
    locks = []


class _AssertingLockManager:
    def __init__(self, telemetry_id: str):
        self.telemetry_id = telemetry_id
        self.released = False

    def create_handle(self):
        return _FakeHandle()

    async def acquire_exact_path(self, handle, lock_path):
        del handle, lock_path
        assert self.telemetry_id in get_request_wait_tracker()._states
        return False

    async def release(self, handle):
        del handle
        self.released = True


@pytest.mark.asyncio
async def test_direct_write_registers_wait_tracker_before_lock_and_cleans_on_busy(monkeypatch):
    telemetry_id = "telemetry-before-lock"
    lock_manager = _AssertingLockManager(telemetry_id)
    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: lock_manager,
    )
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)
    coordinator = ContentWriteCoordinator(_FakeVikingFS())
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

    assert lock_manager.released is True
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
async def test_direct_write_cleans_wait_tracker_when_lock_acquisition_raises(monkeypatch):
    telemetry_id = "telemetry-acquire-error"

    class _FailingLockManager:
        def create_handle(self):
            return _FakeHandle()

        async def acquire_exact_path(self, handle, lock_path):
            del handle, lock_path
            raise RuntimeError("acquire failed")

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: _FailingLockManager(),
    )
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)
    coordinator = ContentWriteCoordinator(_FakeVikingFS())
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)

    with pytest.raises(RuntimeError, match="acquire failed"):
        await _run_direct_write(coordinator, ctx, telemetry_id)

    assert telemetry_id not in tracker._states


@pytest.mark.asyncio
@pytest.mark.parametrize("release_error", [RuntimeError("release failed"), asyncio.CancelledError()])
async def test_direct_write_busy_cleans_tracker_and_preserves_busy_error(
    monkeypatch, release_error
):
    telemetry_id = "telemetry-busy-release-error"

    class _FailingReleaseLockManager:
        def create_handle(self):
            return _FakeHandle()

        async def acquire_exact_path(self, handle, lock_path):
            del handle, lock_path
            return False

        async def release(self, handle):
            del handle
            raise release_error

    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager",
        lambda: _FailingReleaseLockManager(),
    )
    tracker = get_request_wait_tracker()
    tracker.cleanup(telemetry_id)
    coordinator = ContentWriteCoordinator(_FakeVikingFS())
    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)

    with pytest.raises(ResourceBusyError):
        await _run_direct_write(coordinator, ctx, telemetry_id)

    assert telemetry_id not in tracker._states

@pytest.mark.asyncio
async def test_direct_write_cancellation_after_acquire_releases_lock(monkeypatch):
    telemetry_id = "telemetry-cancel-after-acquire"

    class _LockManager:
        def __init__(self):
            self.release_count = 0

        def create_handle(self):
            return _FakeHandle()

        async def acquire_exact_path(self, handle, lock_path):
            del handle, lock_path
            return True

        async def release(self, handle):
            del handle
            self.release_count += 1

    lock_manager = _LockManager()
    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager", lambda: lock_manager
    )
    coordinator = ContentWriteCoordinator(_FakeVikingFS())

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

    assert lock_manager.release_count == 1
    assert telemetry_id not in tracker._states


@pytest.mark.asyncio
async def test_direct_write_cancellation_during_release_finishes_release(monkeypatch):
    telemetry_id = "telemetry-cancel-during-release"
    release_started = asyncio.Event()
    release_allowed = asyncio.Event()

    class _LockManager:
        def __init__(self):
            self.release_count = 0

        def create_handle(self):
            return _FakeHandle()

        async def acquire_exact_path(self, handle, lock_path):
            del handle, lock_path
            return True

        async def release(self, handle):
            del handle
            self.release_count += 1
            release_started.set()
            await release_allowed.wait()

    lock_manager = _LockManager()
    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager", lambda: lock_manager
    )
    coordinator = ContentWriteCoordinator(_FakeVikingFS())

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

    assert lock_manager.release_count == 1
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

    class _LockManager:
        def __init__(self):
            self.release_count = 0

        def create_handle(self):
            return _FakeHandle()

        async def acquire_exact_path(self, handle, lock_path):
            del handle, lock_path
            return True

        async def release(self, handle):
            del handle
            self.release_count += 1

    lock_manager = _LockManager()
    monkeypatch.setattr(
        "openviking.storage.content_write.get_lock_manager", lambda: lock_manager
    )
    coordinator = ContentWriteCoordinator(_FakeVikingFS())

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

    assert lock_manager.release_count == 1
    assert telemetry_id not in tracker._states
