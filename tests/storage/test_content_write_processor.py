from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service import fs_service as fs_module
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.storage.queuefs import compile_target_tag_processor as tag_module
from openviking.storage.queuefs.content_write_msg import ContentWriteMsg
from openviking.storage.queuefs.content_write_processor import ContentWriteProcessor
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["content_write", "ensure_directory_index"])
async def test_content_write_processor_recovers_checkpoint(monkeypatch, operation):
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    ctx = _ctx()
    task = await tracker.create(
        "content_write",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        resource_id="viking://resources/output",
    )
    await tracker.start(task.task_id, account_id=ctx.account_id, user_id=ctx.user.user_id)
    await tracker.register_work(
        task.task_id,
        "root-work-1",
        "ContentWrite",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
    )
    msg = ContentWriteMsg(
        task_id=task.task_id,
        _task_work_id="root-work-1",
        operation=operation,
        resource_id="viking://resources/output",
        result={"overview_status": "queued", "vector_status": "queued"},
        refresh={"kind": "none", "indexed_levels": [0]},
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role="user",
    )
    fs = fs_module.FSService()
    records = AsyncMock(side_effect=[[], [{"level": 0}], [{"level": 0}]])
    monkeypatch.setattr(fs, "_directory_index_records", records)
    monkeypatch.setattr(
        "openviking.server.dependencies.get_service", lambda: SimpleNamespace(fs=fs)
    )
    monkeypatch.setattr(fs_module, "_DIRECTORY_INDEX_VISIBILITY_POLL_SECONDS", 0)
    processor = ContentWriteProcessor(
        SimpleNamespace(_async_agfs=SimpleNamespace(), stat=AsyncMock(return_value={"isDir": True}))
    )
    processor._execute_refresh = AsyncMock(
        return_value={"overview_status": "complete", "vector_status": "skipped"}
    )
    data = {"data": msg.to_dict()}

    try:
        await processor._on_dequeue_bound(data)
        await processor._on_dequeue_bound(data)

        restored = await tracker.get(
            task.task_id,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
        )
        assert restored is not None
        assert restored.result is not None
        assert restored.result["overview_status"] == "complete"
        assert restored.result["vector_status"] == (
            "complete" if operation == "ensure_directory_index" else "skipped"
        )
        assert processor._execute_refresh.await_count == 1
    finally:
        set_task_tracker(None)


@pytest.mark.parametrize("pending", [False, OSError("index unavailable")])
async def test_compile_tag_retry_preserves_work_and_progress(monkeypatch, pending):
    """A restarted processor completes the same Work after lag or a transient failure."""
    fs = SimpleNamespace(finalize_compile_directories=AsyncMock(side_effect=[pending, True]))
    manager = SimpleNamespace(enqueue=AsyncMock())
    monkeypatch.setattr(tag_module, "get_queue_manager", lambda: manager)
    monkeypatch.setattr(tag_module, "sleep", AsyncMock())
    payload = {
        "uri": "viking://resources/output",
        "account_id": "account",
        "user_id": "user",
        "task_id": "tag-task",
        "_task_work_id": "tag-work",
        "last_index_enqueue": 42,
    }
    result = await tag_module.CompileTargetTagProcessor(fs).on_dequeue(payload)
    assert result.outcome is ProcessOutcome.REQUEUED
    retry = manager.enqueue.await_args.args[1]
    assert retry == {**payload, "retry_count": 1}
    result = await tag_module.CompileTargetTagProcessor(fs).on_dequeue(retry)
    assert result.outcome is ProcessOutcome.SUCCESS
    manager.enqueue.assert_awaited_once()
