# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for resource service background task tracking."""

import asyncio
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_tracker import TaskTracker, reset_task_tracker, set_task_tracker
from openviking_cli.session.user_id import UserIdentifier


class _MemoryTaskStore:
    async def create(self, task):
        return None

    async def update(self, task):
        return None

    async def get(self, task_id, *, account_id=None, user_id=None):
        del task_id, account_id, user_id
        return None

    async def list(self, account_id, *, user_id=None):
        del account_id, user_id
        return []

    async def delete(self, task_id, *, account_id, user_id=None):
        del task_id, account_id, user_id
        return None


class _FakeSkillProcessor:
    async def process_skill(self, **kwargs):
        del kwargs
        return {"uri": "viking://user/alice/skills/demo"}


@pytest.mark.asyncio
async def test_add_skill_tracks_queue_monitor_task(monkeypatch):
    reset_task_tracker()
    set_task_tracker(TaskTracker(_MemoryTaskStore()))
    monitor_started = asyncio.Event()
    release_monitor = asyncio.Event()
    service = ResourceService(
        vikingdb=object(),
        viking_fs=object(),
        resource_processor=object(),
        skill_processor=_FakeSkillProcessor(),
    )

    async def fake_monitor(*args):
        del args
        monitor_started.set()
        await release_monitor.wait()

    monkeypatch.setattr(service, "_monitor_queue_processing", fake_monitor)
    monkeypatch.setattr(
        "openviking.service.resource_service.get_current_telemetry",
        lambda: SimpleNamespace(telemetry_id="telemetry-resource-monitor"),
    )

    ctx = RequestContext(user=UserIdentifier("acc", "alice"), role=Role.USER)
    result = await service.add_skill("skill body", ctx, wait=False, target_uri="viking://user/skills")
    await asyncio.wait_for(monitor_started.wait(), timeout=1.0)

    assert result["task_id"]
    assert len(service._background_tasks) == 1

    await service.close_background_tasks()

    assert not service._background_tasks
    reset_task_tracker()
