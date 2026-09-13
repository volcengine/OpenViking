# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for reserved-target rollback on failed add-resource jobs (#4501)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pytest

from openviking.service.task_tracker import TaskStatus
from openviking.service.task_work_index import TASK_WORK_ID_FIELD
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.queue_manager import QueueManager


def _task_tracker() -> SimpleNamespace:
    return SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_descendants=AsyncMock(),
    )


def _processor(monkeypatch, execute_add_resource_job, cleanup) -> tuple:
    task_tracker = _task_tracker()
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker",
        Mock(return_value=task_tracker),
    )
    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(side_effect=execute_add_resource_job),
        _cleanup_reserved_target_if_empty=AsyncMock(side_effect=cleanup),
        _link_resource_reason_memory=AsyncMock(),
    )
    lock = {"lease_ref": "lock-1"}
    viking_fs = SimpleNamespace(
        _async_agfs=SimpleNamespace(
            pathlock_adopt=AsyncMock(return_value=lock),
            pathlock_release=AsyncMock(),
        ),
        delete_temp=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        asyncio.get_running_loop(),
        QueueManager.ADD_RESOURCE,
        viking_fs,
    )
    return processor, task_tracker, service, viking_fs, lock


def _msg() -> AddResourceMsg:
    return AddResourceMsg(
        task_id="task-1",
        path="broken.pdf",
        root_uri="viking://resources/repro/broken-pdf",
        account_id="account-1",
        user_id="user-1",
        role="user",
        lock_handoff={"lease_ref": "handoff-1"},
        cleanup_empty_target_on_failure=True,
    )


def _data(msg: AddResourceMsg) -> dict:
    data = msg.to_dict()
    data[TASK_WORK_ID_FIELD] = "work-1"
    return data


@pytest.mark.asyncio
async def test_business_error_rolls_back_reserved_target(monkeypatch):
    processor, task_tracker, service, _, lock = _processor(
        monkeypatch,
        execute_add_resource_job=lambda *_a, **_k: {
            "status": "error",
            "errors": ["No /Root object! - Is this really a PDF?"],
        },
        cleanup=lambda **_k: True,
    )
    msg = _msg()

    await processor._process(msg, _data(msg))

    task_tracker.fail.assert_awaited_once()
    service._cleanup_reserved_target_if_empty.assert_awaited_once_with(
        root_uri="viking://resources/repro/broken-pdf",
        ctx=ANY,
        resource_lock=lock,
    )


@pytest.mark.asyncio
async def test_exception_rolls_back_reserved_target(monkeypatch):
    async def _raise(*_args, **_kwargs):
        raise RuntimeError("No /Root object! - Is this really a PDF?")

    processor, task_tracker, service, _, lock = _processor(
        monkeypatch,
        execute_add_resource_job=_raise,
        cleanup=lambda **_k: True,
    )
    msg = _msg()

    await processor._process(msg, _data(msg))

    task_tracker.fail.assert_awaited_once()
    service._cleanup_reserved_target_if_empty.assert_awaited_once_with(
        root_uri="viking://resources/repro/broken-pdf",
        ctx=ANY,
        resource_lock=lock,
    )


@pytest.mark.asyncio
async def test_successful_import_keeps_reserved_target(monkeypatch):
    processor, task_tracker, service, _, _ = _processor(
        monkeypatch,
        execute_add_resource_job=lambda *_a, **_k: {
            "status": "success",
            "root_uri": "viking://resources/repro/broken-pdf",
        },
        cleanup=lambda **_k: True,
    )
    msg = _msg()

    await processor._process(msg, _data(msg))

    task_tracker.complete.assert_awaited()
    service._cleanup_reserved_target_if_empty.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_without_ownership_keeps_reserved_target(monkeypatch):
    processor, task_tracker, service, _, _ = _processor(
        monkeypatch,
        execute_add_resource_job=lambda *_a, **_k: {
            "status": "error",
            "errors": ["parse failed"],
        },
        cleanup=lambda **_k: True,
    )
    msg = _msg()
    msg.cleanup_empty_target_on_failure = False

    await processor._process(msg, _data(msg))

    task_tracker.fail.assert_awaited_once()
    service._cleanup_reserved_target_if_empty.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_runs_before_lock_release(monkeypatch):
    events = []

    async def _execute(*_args, **_kwargs):
        return {"status": "error", "errors": ["parse failed"]}

    async def _cleanup(**_kwargs):
        events.append("cleanup")
        return True

    processor, _, service, viking_fs, _ = _processor(
        monkeypatch,
        execute_add_resource_job=_execute,
        cleanup=_cleanup,
    )
    viking_fs._async_agfs.pathlock_release = AsyncMock(
        side_effect=lambda *_args: events.append("release")
    )
    msg = _msg()

    await processor._process(msg, _data(msg))

    assert events == ["cleanup", "release"]
