from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.storage.queuefs.content_write_msg import ContentWriteMsg
from openviking.storage.queuefs.content_write_processor import ContentWriteProcessor
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)


@pytest.mark.asyncio
async def test_content_write_processor_recovers_dispatch_result_from_checkpoint():
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    set_task_tracker(tracker)
    ctx = _ctx()
    task = await tracker.create(
        "content_write",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        resource_id="viking://user/default/memories/note.md",
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
        operation="content_write",
        resource_id="viking://user/default/memories/note.md",
        result={"overview_status": "queued", "vector_status": "queued"},
        refresh={"kind": "memory"},
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        role="user",
    )
    processor = ContentWriteProcessor(SimpleNamespace(_async_agfs=SimpleNamespace()))
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
        assert restored.result["vector_status"] == "skipped"
        assert processor._execute_refresh.await_count == 1
    finally:
        set_task_tracker(None)
