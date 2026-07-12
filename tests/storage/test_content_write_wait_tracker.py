# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for content write wait-tracker lock ordering."""

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
