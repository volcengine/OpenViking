from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.storage.queuefs.reindex_msg import ReindexMsg


def test_reindex_msg_round_trips_only_request_descriptor():
    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="semantic_and_vectors",
        force=True,
        recursive=False,
        tags=["team=search"],
        tag_mode="append",
        account_id="account-1",
        user_id="user-1",
        group_ids=["group-1"],
        role="admin",
        lock_handoff={"lease_ref": "lease-1"},
    )

    restored = ReindexMsg.from_dict(message.to_dict())

    assert restored == message
    assert "source_contents" not in restored.to_dict()
    assert "snapshot" not in restored.to_dict()


def test_reindex_msg_rejects_missing_descriptor_fields():
    with pytest.raises(ValueError, match="task_id"):
        ReindexMsg.from_dict({"uri": "viking://resources/demo"})


@pytest.mark.asyncio
async def test_reindex_processor_terminalizes_root_after_descendants(monkeypatch):
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    tracker = SimpleNamespace(
        start=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        wait_for_descendants=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", Mock(return_value=tracker)
    )
    monkeypatch.setattr(
        "openviking.server.dependencies.get_service",
        lambda: SimpleNamespace(_vlm_resolver=None, _vector_config_resolver=None),
    )

    result = {"status": "completed", "rebuilt_records": 3}
    monkeypatch.setattr(
        "openviking.service.reindex_executor.ReindexExecutor._run", AsyncMock(return_value=result)
    )
    viking_fs = SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock()))
    processor = ReindexProcessor(viking_fs)
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "root"})
    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="vectors_only",
        account_id="account-1",
        user_id="user-1",
        role="admin",
    )

    outcome = await processor.on_dequeue(
        {"id": "queue-1", "data": {**message.to_dict(), "_task_work_id": "root-work"}}
    )

    assert outcome.outcome.value == "success"
    tracker.wait_for_descendants.assert_awaited_once_with("task-1", "root-work")
    tracker.complete.assert_awaited_once_with(
        "task-1", result, account_id="account-1", user_id="user-1"
    )
