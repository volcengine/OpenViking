# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for EmbeddingTaskTracker."""

import asyncio

import pytest

from openviking.storage.queuefs.embedding_tracker import EmbeddingTaskTracker


def reset_singleton():
    """Reset the singleton instance for testing."""
    EmbeddingTaskTracker._instance = None


@pytest.fixture(autouse=True)
def clean_singleton():
    """Reset singleton before and after each test."""
    reset_singleton()
    yield
    reset_singleton()


@pytest.fixture
def tracker() -> EmbeddingTaskTracker:
    """Create a fresh tracker instance for each test."""
    return EmbeddingTaskTracker()


# ── Singleton Pattern Tests ──


def test_singleton_returns_same_instance():
    """Test that get_instance() returns the same instance."""
    instance1 = EmbeddingTaskTracker.get_instance()
    instance2 = EmbeddingTaskTracker.get_instance()
    assert instance1 is instance2


def test_singleton_persists_across_calls():
    """Test that singleton persists across multiple get_instance calls."""
    instance1 = EmbeddingTaskTracker.get_instance()
    instance2 = EmbeddingTaskTracker.get_instance()
    instance3 = EmbeddingTaskTracker.get_instance()
    assert instance1 is instance2 is instance3


# ── Register Tests ──


@pytest.mark.asyncio
async def test_register_task(tracker: EmbeddingTaskTracker):
    """Test registering a task with valid count."""
    await tracker.register("msg-1", 5)
    status = await tracker.get_status("msg-1")
    assert status is not None
    assert status["remaining"] == 5
    assert status["total"] == 5


@pytest.mark.asyncio
async def test_register_with_metadata(tracker: EmbeddingTaskTracker):
    """Test registering a task with metadata."""
    metadata = {"key": "value", "count": 10}
    await tracker.register("msg-2", 3, metadata=metadata)
    status = await tracker.get_status("msg-2")
    assert status is not None
    assert status["metadata"] == metadata


@pytest.mark.asyncio
async def test_register_with_callback(tracker: EmbeddingTaskTracker):
    """Test registering a task with on_complete callback."""
    callback_called = []

    async def on_complete():
        callback_called.append(True)

    await tracker.register("msg-3", 1, on_complete=on_complete)
    await tracker.decrement("msg-3")
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_register_with_sync_callback(tracker: EmbeddingTaskTracker):
    """Test registering a task with synchronous callback."""
    callback_called = []

    def on_complete():
        callback_called.append(True)

    await tracker.register("msg-4", 1, on_complete=on_complete)
    await tracker.decrement("msg-4")
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_register_with_zero_count_does_nothing(tracker: EmbeddingTaskTracker):
    """Test that registering with total_count=0 does nothing."""
    await tracker.register("msg-5", 0)
    status = await tracker.get_status("msg-5")
    assert status is None


@pytest.mark.asyncio
async def test_register_with_negative_count_does_nothing(tracker: EmbeddingTaskTracker):
    """Test that registering with negative total_count does nothing."""
    await tracker.register("msg-6", -5)
    status = await tracker.get_status("msg-6")
    assert status is None


@pytest.mark.asyncio
async def test_register_overwrites_existing(tracker: EmbeddingTaskTracker):
    """Test that registering with same ID overwrites existing entry."""
    await tracker.register("msg-7", 5)
    await tracker.register("msg-7", 10)
    status = await tracker.get_status("msg-7")
    assert status is not None
    assert status["remaining"] == 10
    assert status["total"] == 10


# ── Increment Tests ──


@pytest.mark.asyncio
async def test_increment_existing_task(tracker: EmbeddingTaskTracker):
    """Test incrementing an existing task."""
    await tracker.register("msg-10", 5)
    result = await tracker.increment("msg-10")
    assert result == 6
    status = await tracker.get_status("msg-10")
    assert status["remaining"] == 6
    assert status["total"] == 6


@pytest.mark.asyncio
async def test_increment_multiple_times(tracker: EmbeddingTaskTracker):
    """Test incrementing a task multiple times."""
    await tracker.register("msg-11", 2)
    await tracker.increment("msg-11")
    await tracker.increment("msg-11")
    await tracker.increment("msg-11")
    status = await tracker.get_status("msg-11")
    assert status["remaining"] == 5
    assert status["total"] == 5


@pytest.mark.asyncio
async def test_increment_nonexistent_task_returns_none(tracker: EmbeddingTaskTracker):
    """Test incrementing a non-existent task returns None."""
    result = await tracker.increment("nonexistent")
    assert result is None


# ── Decrement Tests ──


@pytest.mark.asyncio
async def test_decrement_existing_task(tracker: EmbeddingTaskTracker):
    """Test decrementing an existing task."""
    await tracker.register("msg-20", 5)
    result = await tracker.decrement("msg-20")
    assert result == 4
    status = await tracker.get_status("msg-20")
    assert status["remaining"] == 4


@pytest.mark.asyncio
async def test_decrement_multiple_times(tracker: EmbeddingTaskTracker):
    """Test decrementing a task multiple times."""
    await tracker.register("msg-21", 3)
    await tracker.decrement("msg-21")
    await tracker.decrement("msg-21")
    status = await tracker.get_status("msg-21")
    assert status["remaining"] == 1


@pytest.mark.asyncio
async def test_decrement_nonexistent_task_returns_none(tracker: EmbeddingTaskTracker):
    """Test decrementing a non-existent task returns None."""
    result = await tracker.decrement("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_decrement_to_zero_removes_task(tracker: EmbeddingTaskTracker):
    """Test that decrementing to zero removes the task."""
    await tracker.register("msg-22", 1)
    result = await tracker.decrement("msg-22")
    assert result == 0
    status = await tracker.get_status("msg-22")
    assert status is None


@pytest.mark.asyncio
async def test_decrement_triggers_callback_on_completion(tracker: EmbeddingTaskTracker):
    """Test that callback is triggered when count reaches zero."""
    callback_called = []

    async def on_complete():
        callback_called.append("async")

    await tracker.register("msg-23", 2, on_complete=on_complete)
    await tracker.decrement("msg-23")
    assert len(callback_called) == 0
    await tracker.decrement("msg-23")
    assert len(callback_called) == 1
    assert callback_called[0] == "async"


@pytest.mark.asyncio
async def test_decrement_sync_callback_on_completion(tracker: EmbeddingTaskTracker):
    """Test that sync callback is triggered when count reaches zero."""
    callback_called = []

    def on_complete():
        callback_called.append("sync")

    await tracker.register("msg-24", 1, on_complete=on_complete)
    await tracker.decrement("msg-24")
    assert len(callback_called) == 1
    assert callback_called[0] == "sync"


@pytest.mark.asyncio
async def test_decrement_callback_error_is_handled(tracker: EmbeddingTaskTracker):
    """Test that callback errors are handled gracefully."""

    async def on_complete():
        raise ValueError("Callback error")

    await tracker.register("msg-25", 1, on_complete=on_complete)
    result = await tracker.decrement("msg-25")
    assert result == 0


@pytest.mark.asyncio
async def test_decrement_below_zero_removes_task(tracker: EmbeddingTaskTracker):
    """Test that decrementing below zero still removes the task."""
    await tracker.register("msg-26", 1)
    await tracker.decrement("msg-26")
    status = await tracker.get_status("msg-26")
    assert status is None


# ── Get Status Tests ──


@pytest.mark.asyncio
async def test_get_status_existing_task(tracker: EmbeddingTaskTracker):
    """Test getting status of an existing task."""
    await tracker.register("msg-30", 5, metadata={"key": "value"})
    status = await tracker.get_status("msg-30")
    assert status is not None
    assert status["remaining"] == 5
    assert status["total"] == 5
    assert status["metadata"] == {"key": "value"}


@pytest.mark.asyncio
async def test_get_status_nonexistent_task(tracker: EmbeddingTaskTracker):
    """Test getting status of a non-existent task."""
    status = await tracker.get_status("nonexistent")
    assert status is None


@pytest.mark.asyncio
async def test_get_status_reflects_changes(tracker: EmbeddingTaskTracker):
    """Test that get_status reflects increment/decrement changes."""
    await tracker.register("msg-31", 5)
    await tracker.increment("msg-31")
    status = await tracker.get_status("msg-31")
    assert status["remaining"] == 6
    assert status["total"] == 6
    await tracker.decrement("msg-31")
    status = await tracker.get_status("msg-31")
    assert status["remaining"] == 5


# ── Remove Tests ──


@pytest.mark.asyncio
async def test_remove_existing_task(tracker: EmbeddingTaskTracker):
    """Test removing an existing task."""
    await tracker.register("msg-40", 5)
    result = await tracker.remove("msg-40")
    assert result is True
    status = await tracker.get_status("msg-40")
    assert status is None


@pytest.mark.asyncio
async def test_remove_nonexistent_task(tracker: EmbeddingTaskTracker):
    """Test removing a non-existent task."""
    result = await tracker.remove("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_remove_does_not_trigger_callback(tracker: EmbeddingTaskTracker):
    """Test that remove does not trigger the on_complete callback."""
    callback_called = []

    async def on_complete():
        callback_called.append(True)

    await tracker.register("msg-41", 5, on_complete=on_complete)
    await tracker.remove("msg-41")
    assert len(callback_called) == 0


# ── Get All Tracked Tests ──


@pytest.mark.asyncio
async def test_get_all_tracked_empty(tracker: EmbeddingTaskTracker):
    """Test get_all_tracked when no tasks are registered."""
    all_tasks = await tracker.get_all_tracked()
    assert all_tasks == {}


@pytest.mark.asyncio
async def test_get_all_tracked_single_task(tracker: EmbeddingTaskTracker):
    """Test get_all_tracked with a single task."""
    await tracker.register("msg-50", 5, metadata={"key": "value"})
    all_tasks = await tracker.get_all_tracked()
    assert len(all_tasks) == 1
    assert "msg-50" in all_tasks
    assert all_tasks["msg-50"]["remaining"] == 5
    assert all_tasks["msg-50"]["total"] == 5
    assert all_tasks["msg-50"]["metadata"] == {"key": "value"}


@pytest.mark.asyncio
async def test_get_all_tracked_multiple_tasks(tracker: EmbeddingTaskTracker):
    """Test get_all_tracked with multiple tasks."""
    await tracker.register("msg-51", 3)
    await tracker.register("msg-52", 5)
    await tracker.register("msg-53", 7)
    all_tasks = await tracker.get_all_tracked()
    assert len(all_tasks) == 3
    assert "msg-51" in all_tasks
    assert "msg-52" in all_tasks
    assert "msg-53" in all_tasks


@pytest.mark.asyncio
async def test_get_all_tracked_excludes_on_complete(tracker: EmbeddingTaskTracker):
    """Test that get_all_tracked does not include on_complete callback."""
    await tracker.register("msg-54", 5, on_complete=lambda: None)
    all_tasks = await tracker.get_all_tracked()
    assert "on_complete" not in all_tasks["msg-54"]


@pytest.mark.asyncio
async def test_get_all_tracked_returns_copy(tracker: EmbeddingTaskTracker):
    """Test that get_all_tracked returns a copy, not internal state."""
    await tracker.register("msg-55", 5)
    all_tasks = await tracker.get_all_tracked()
    all_tasks["msg-55"]["remaining"] = 999
    status = await tracker.get_status("msg-55")
    assert status["remaining"] == 5


# ── Concurrency Tests ──


@pytest.mark.asyncio
async def test_concurrent_register(tracker: EmbeddingTaskTracker):
    """Test concurrent register operations."""

    async def register_task(msg_id: str):
        await tracker.register(msg_id, 5)

    await asyncio.gather(
        register_task("msg-60"),
        register_task("msg-61"),
        register_task("msg-62"),
    )
    all_tasks = await tracker.get_all_tracked()
    assert len(all_tasks) == 3


@pytest.mark.asyncio
async def test_concurrent_increment(tracker: EmbeddingTaskTracker):
    """Test concurrent increment operations."""
    await tracker.register("msg-70", 1)

    async def increment_task():
        await tracker.increment("msg-70")

    await asyncio.gather(
        increment_task(),
        increment_task(),
        increment_task(),
    )
    status = await tracker.get_status("msg-70")
    assert status["remaining"] == 4
    assert status["total"] == 4


@pytest.mark.asyncio
async def test_concurrent_decrement(tracker: EmbeddingTaskTracker):
    """Test concurrent decrement operations."""
    await tracker.register("msg-71", 3)

    async def decrement_task():
        await tracker.decrement("msg-71")

    await asyncio.gather(
        decrement_task(),
        decrement_task(),
        decrement_task(),
    )
    status = await tracker.get_status("msg-71")
    assert status is None


@pytest.mark.asyncio
async def test_concurrent_mixed_operations(tracker: EmbeddingTaskTracker):
    """Test concurrent mixed operations (increment and decrement)."""
    await tracker.register("msg-72", 5)

    async def increment():
        await tracker.increment("msg-72")

    async def decrement():
        await tracker.decrement("msg-72")

    await asyncio.gather(
        increment(),
        increment(),
        decrement(),
        decrement(),
        decrement(),
    )
    status = await tracker.get_status("msg-72")
    assert status is not None


@pytest.mark.asyncio
async def test_concurrent_register_and_decrement(tracker: EmbeddingTaskTracker):
    """Test concurrent register and decrement operations."""
    callback_called = []

    async def on_complete():
        callback_called.append(True)

    await tracker.register("msg-73", 1, on_complete=on_complete)
    await tracker.decrement("msg-73")
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_concurrent_callback_execution(tracker: EmbeddingTaskTracker):
    """Test that callbacks are executed correctly under concurrency."""
    callback_count = []

    async def make_callback(msg_id: str):
        async def on_complete():
            callback_count.append(msg_id)

        return on_complete

    async def register_and_complete(msg_id: str):
        callback = await make_callback(msg_id)
        await tracker.register(msg_id, 1, on_complete=callback)
        await tracker.decrement(msg_id)

    await asyncio.gather(
        register_and_complete("msg-80"),
        register_and_complete("msg-81"),
        register_and_complete("msg-82"),
    )
    assert len(callback_count) == 3


# ── Edge Cases Tests ──


@pytest.mark.asyncio
async def test_multiple_decrements_to_zero(tracker: EmbeddingTaskTracker):
    """Test multiple decrements that bring count to exactly zero."""
    callback_called = []

    async def on_complete():
        callback_called.append(True)

    await tracker.register("msg-90", 3, on_complete=on_complete)
    await tracker.decrement("msg-90")
    await tracker.decrement("msg-90")
    assert len(callback_called) == 0
    await tracker.decrement("msg-90")
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_decrement_after_increment(tracker: EmbeddingTaskTracker):
    """Test decrement after increment maintains correct count."""
    await tracker.register("msg-91", 2)
    await tracker.increment("msg-91")
    await tracker.decrement("msg-91")
    status = await tracker.get_status("msg-91")
    assert status["remaining"] == 2
    assert status["total"] == 3


@pytest.mark.asyncio
async def test_empty_metadata(tracker: EmbeddingTaskTracker):
    """Test that empty metadata is handled correctly."""
    await tracker.register("msg-92", 5, metadata={})
    status = await tracker.get_status("msg-92")
    assert status["metadata"] == {}


@pytest.mark.asyncio
async def test_none_metadata(tracker: EmbeddingTaskTracker):
    """Test that None metadata defaults to empty dict."""
    await tracker.register("msg-93", 5, metadata=None)
    status = await tracker.get_status("msg-93")
    assert status["metadata"] == {}


@pytest.mark.asyncio
async def test_none_callback(tracker: EmbeddingTaskTracker):
    """Test that None callback is handled correctly."""
    await tracker.register("msg-94", 1, on_complete=None)
    result = await tracker.decrement("msg-94")
    assert result == 0


@pytest.mark.asyncio
async def test_large_count(tracker: EmbeddingTaskTracker):
    """Test with large task count."""
    large_count = 10000
    await tracker.register("msg-95", large_count)
    status = await tracker.get_status("msg-95")
    assert status["remaining"] == large_count
    assert status["total"] == large_count


@pytest.mark.asyncio
async def test_special_characters_in_id(tracker: EmbeddingTaskTracker):
    """Test with special characters in semantic_msg_id."""
    special_id = "msg-with-special_chars.123!@#$%"
    await tracker.register(special_id, 5)
    status = await tracker.get_status(special_id)
    assert status is not None
    assert status["remaining"] == 5
