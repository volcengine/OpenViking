"""Repeated activity checks should restore an owner's file records once."""

import asyncio
from unittest.mock import create_autospec

import pytest

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from tests.test_task_tracker import _FakeAgfs

OWNER = {"account_id": "acme", "user_id": "alice"}


@pytest.mark.asyncio
async def test_activity_checks_restore_once_and_follow_live_lifecycle():
    agfs = _FakeAgfs()
    writer = TaskTracker(PersistentTaskStore(agfs))
    task = await writer.create("session_commit", resource_id="s", **OWNER)
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    agfs.read = create_autospec(agfs.read, side_effect=agfs.read)
    assert all(
        await asyncio.gather(
            *[tracker.has_running("session_commit", "s", **OWNER) for _ in range(12)]
        )
    )
    assert agfs.read.call_count == 1
    assert await tracker.create_if_no_running("session_commit", "s", **OWNER) is None
    await tracker.complete(task.task_id, {}, **OWNER)
    assert not await tracker.has_running("session_commit", "s", **OWNER)
    assert agfs.read.call_count == 1
    assert await tracker.create_if_no_running("session_commit", "s", **OWNER) is not None
    reads = agfs.read.call_count
    assert await tracker.has_running("session_commit", "s", **OWNER)
    assert agfs.read.call_count == reads
    assert not await tracker.has_running("session_commit", "s", account_id="acme", user_id="bob")


@pytest.mark.asyncio
async def test_failed_restore_retries_and_new_files_are_discovered():
    agfs = _FakeAgfs()
    writer = TaskTracker(PersistentTaskStore(agfs))
    await writer.create("session_commit", resource_id="s", **OWNER)
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    original = agfs.read
    agfs.read = create_autospec(original, side_effect=OSError("unavailable"))
    with pytest.raises(OSError):
        await tracker.has_running("session_commit", "s", **OWNER)
    agfs.read = create_autospec(original, side_effect=original)
    assert await tracker.has_running("session_commit", "s", **OWNER)
    assert await tracker.has_running("session_commit", "s", **OWNER)
    assert agfs.read.call_count == 1
    await writer.create("session_commit", resource_id="new", **OWNER)
    reads = agfs.read.call_count
    assert await tracker.has_running("session_commit", "new", **OWNER)
    assert agfs.read.call_count == reads + 1
