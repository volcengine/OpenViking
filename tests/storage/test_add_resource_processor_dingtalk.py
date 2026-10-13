from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.task_tracker import TaskStatus
from openviking.service.task_work_index import TASK_WORK_ID_FIELD
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.queue_manager import QueueManager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("warnings", "unreadable_nodes", "records_sync"),
    [
        ([], [], True),
        (
            [
                "DingTalk sync skipped 1 unreadable source file(s); "
                "they will be retried on the next sync."
            ],
            [{"node_id": "file-1"}],
            True,
        ),
        (["DingTalk sync retained 1 missing source node."], [], False),
    ],
)
async def test_dingtalk_task_completes_after_descendants_and_sync_record(
    monkeypatch, warnings, unreadable_nodes, records_sync
):
    events = []

    async def record_descendants(*_args, **_kwargs):
        events.append("descendants")

    async def record_sync(*_args, **_kwargs):
        events.append("sync_record")

    async def record_complete(*_args, **_kwargs):
        events.append("complete")

    tracker = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
        start=AsyncMock(),
        update_stage=AsyncMock(),
        complete=AsyncMock(side_effect=record_complete),
        fail=AsyncMock(),
        get_task_auth=AsyncMock(return_value={}),
        wait_for_descendants=AsyncMock(side_effect=record_descendants),
    )
    mark_complete = AsyncMock(side_effect=record_sync)
    monkeypatch.setattr(
        "openviking.storage.queuefs.add_resource_processor.get_task_tracker", lambda: tracker
    )
    monkeypatch.setattr("openviking.resource.dingtalk_incremental.mark_complete", mark_complete)
    result = {
        "status": "success",
        "root_uri": "viking://resources/dingtalk_Source123",
        "meta": {
            "dingtalk_run_id": "run-2",
            "dingtalk_report": {"unreadable_nodes": unreadable_nodes},
        },
        "warnings": warnings,
    }
    service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(return_value=result),
        _link_resource_reason_memory=AsyncMock(),
    )
    processor = AddResourceProcessor(
        service,
        QueueManager.ADD_RESOURCE,
        SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock())),
    )
    msg = AddResourceMsg(
        task_id="task-1",
        path="https://alidocs.dingtalk.com/i/nodes/Source123",
        root_uri=result["root_uri"],
        account_id="account-1",
        user_id="user-1",
        role="user",
    )
    data = msg.to_dict()
    data[TASK_WORK_ID_FIELD] = "work-1"

    await processor._process(msg, data)

    expected = ["descendants"]
    if records_sync:
        expected.append("sync_record")
    expected.append("complete")
    assert events == expected
    assert tracker.complete.await_count == 1


@pytest.mark.asyncio
async def test_descendant_failure_cannot_be_marked_as_a_reusable_import():
    from openviking.service.task_tracker import TaskTracker

    tracker = TaskTracker(store=SimpleNamespace())
    tracker._work_index.record_failure("dingtalk-task", "embedding failed")
    with pytest.raises(RuntimeError, match="embedding failed"):
        await tracker.wait_for_descendants("dingtalk-task", "import-work", raise_on_failure=True)
    # Existing callers keep their current completion behavior.
    await tracker.wait_for_descendants("dingtalk-task", "import-work")
