from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.service.task_tracker import TaskStatus
from openviking.storage.errors import LockAcquisitionError
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
        actor_peer_id="peer-1",
        bypass_acl=True,
        lock_handoff={"lease_ref": "lease-1"},
    )

    restored = ReindexMsg.from_dict(message.to_dict())

    assert restored == message
    assert "source_contents" not in restored.to_dict()
    assert "snapshot" not in restored.to_dict()


def test_reindex_processor_restores_actor_peer_and_acl_bypass_context():
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="vectors_only",
        account_id="account-1",
        user_id="user-1",
        role="admin",
        actor_peer_id="peer-1",
        bypass_acl=True,
    )

    ctx = ReindexProcessor._ctx(message)

    assert ctx.actor_peer_id == "peer-1"
    assert ctx.bypass_acl is True


def test_reindex_msg_rejects_missing_descriptor_fields():
    with pytest.raises(ValueError, match="task_id"):
        ReindexMsg.from_dict({"uri": "viking://resources/demo"})


@pytest.mark.asyncio
async def test_reindex_processor_terminalizes_root_after_descendants(monkeypatch):
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    tracker = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.RUNNING)),
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


@pytest.mark.asyncio
async def test_reindex_processor_restores_root_telemetry_for_executor(monkeypatch):
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor
    from openviking.telemetry import (
        OperationTelemetry,
        get_current_telemetry,
        register_telemetry,
        unregister_telemetry,
    )

    tracker = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.RUNNING)),
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
    telemetry = OperationTelemetry(operation="content_reindex", enabled=True)
    telemetry_id = telemetry.telemetry_id
    register_telemetry(telemetry)
    seen = {}

    async def capture_telemetry(_self, **_kwargs):
        seen["telemetry_id"] = get_current_telemetry().telemetry_id
        return {"status": "completed"}

    monkeypatch.setattr(
        "openviking.service.reindex_executor.ReindexExecutor._run",
        capture_telemetry,
    )
    processor = ReindexProcessor(
        SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock()))
    )
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "root"})
    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="semantic_and_vectors",
        account_id="account-1",
        user_id="user-1",
        role="admin",
        telemetry_id=telemetry_id,
    )

    try:
        outcome = await processor.on_dequeue({"id": "queue-1", "data": message.to_dict()})
    finally:
        unregister_telemetry(telemetry_id)

    assert outcome.outcome.value == "success"
    assert seen["telemetry_id"] == telemetry_id


@pytest.mark.asyncio
async def test_reindex_processor_replay_of_completed_task_skips_force_rebuild(monkeypatch):
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    tracker = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.COMPLETED)),
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
    monkeypatch.setattr(
        "openviking.service.reindex_executor.ReindexExecutor._run",
        AsyncMock(return_value={"status": "completed"}),
    )
    viking_fs = SimpleNamespace(_async_agfs=SimpleNamespace(pathlock_release=AsyncMock()))
    processor = ReindexProcessor(viking_fs)
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "must-not-adopt"})
    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="semantic_and_vectors",
        force=True,
        account_id="account-1",
        user_id="user-1",
        role="admin",
        lock_handoff={"lease_ref": "old"},
    )

    outcome = await processor.on_dequeue(
        {"id": "queue-1", "data": {**message.to_dict(), "_task_work_id": "root-work"}}
    )

    assert outcome.outcome.value == "success"
    processor._adopt_or_reacquire.assert_not_awaited()
    tracker.start.assert_not_awaited()
    tracker.complete.assert_not_awaited()
    tracker.fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_processor_requeues_lock_handoff_failure_before_ack(monkeypatch):
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    tracker = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.RUNNING)),
        start=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        wait_for_descendants=AsyncMock(),
    )
    queue_manager = SimpleNamespace(REINDEX="Reindex", enqueue=AsyncMock(return_value="retry-1"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", Mock(return_value=tracker)
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue_manager)
    processor = ReindexProcessor(SimpleNamespace(_async_agfs=SimpleNamespace()))
    processor._adopt_or_reacquire = AsyncMock(side_effect=LockAcquisitionError("busy"))
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

    assert outcome.outcome.value == "requeued"
    queue_manager.enqueue.assert_awaited_once_with(
        "Reindex", {**message.to_dict(), "lock_handoff_retry": 1}
    )
    tracker.fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_processor_fails_after_lock_handoff_retry_limit(monkeypatch):
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    tracker = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.RUNNING)),
        start=AsyncMock(),
        complete=AsyncMock(),
        fail=AsyncMock(),
        wait_for_descendants=AsyncMock(),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.reindex_processor.get_task_tracker", Mock(return_value=tracker)
    )
    processor = ReindexProcessor(SimpleNamespace(_async_agfs=SimpleNamespace()))
    processor._adopt_or_reacquire = AsyncMock(side_effect=LockAcquisitionError("busy"))
    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="vectors_only",
        account_id="account-1",
        user_id="user-1",
        role="admin",
        lock_handoff_retry=2,
    )

    outcome = await processor.on_dequeue({"id": "queue-1", "data": message.to_dict()})

    assert outcome.outcome.value == "failed"
    tracker.fail.assert_awaited_once()


@pytest.mark.asyncio
async def test_reindex_processor_cancellation_releases_root_lock_handoff():
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    agfs = SimpleNamespace(
        pathlock_adopt=AsyncMock(return_value={"lease": "root"}),
        pathlock_release=AsyncMock(),
    )
    processor = ReindexProcessor(SimpleNamespace(_async_agfs=agfs))
    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="vectors_only",
        account_id="account-1",
        user_id="user-1",
        role="admin",
        lock_handoff={"lease_ref": "root-handoff"},
    )

    outcome = await processor.on_cancelled({"data": message.to_dict()})

    assert outcome.outcome.value == "cancelled"
    agfs.pathlock_adopt.assert_awaited_once_with({"lease_ref": "root-handoff"})
    agfs.pathlock_release.assert_awaited_once_with({"lease": "root"})


@pytest.mark.asyncio
async def test_reindex_processor_does_not_release_lease_owned_by_failed_executor(monkeypatch):
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor

    tracker = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.RUNNING)),
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
    agfs = SimpleNamespace(pathlock_release=AsyncMock())

    async def fail_after_releasing(_self, *, existing_lease, **_kwargs):
        await agfs.pathlock_release(existing_lease)
        raise RuntimeError("semantic plan enqueue failed")

    monkeypatch.setattr(
        "openviking.service.reindex_executor.ReindexExecutor._run",
        fail_after_releasing,
    )
    processor = ReindexProcessor(SimpleNamespace(_async_agfs=agfs))
    processor._adopt_or_reacquire = AsyncMock(return_value={"lease": "root"})
    message = ReindexMsg(
        task_id="task-1",
        uri="viking://resources/demo",
        object_type="resource",
        mode="semantic_and_vectors",
        account_id="account-1",
        user_id="user-1",
        role="admin",
    )

    outcome = await processor.on_dequeue({"id": "queue-1", "data": message.to_dict()})

    assert outcome.outcome.value == "failed"
    agfs.pathlock_release.assert_awaited_once_with({"lease": "root"})
    tracker.fail.assert_awaited_once()
