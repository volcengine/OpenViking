"""Verify the optional provider key contract through native QueueFS."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_domain import TaskRecord
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_none_provider_key_uses_plain_enqueue_for_create_and_resume(
    monkeypatch, tmp_path, backend
):
    import ragfs_python

    import openviking.resource.watch_manager  # noqa: F401
    from openviking.connector.delegate import ConnectorDelegate

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    transport = ragfs_python.RAGFSBindingClient()
    transport.mount(
        "queuefs", "/queue", {"backend": backend, "db_path": str(tmp_path / "queue.sqlite")}
    )
    manager = QueueManager(transport)
    queue = manager.get_queue(manager.EXTERNAL_TASK, allow_create=True)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    service = ExternalTaskService()
    service.register(
        SimpleNamespace(
            task_type="connector",
            task_id_prefix="con_",
            serialization_key=lambda payload: ConnectorDelegate.serialization_key(None, payload),
        )
    )
    queue._async_agfs.write = AsyncMock(wraps=queue._async_agfs.write)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    try:
        task = await service.create(
            "connector", resource_id=None, payload={"submit": {"to": "target"}}, ctx=ctx
        )
        assert service.queue_serial_key(task) is None
        await service._resume_submission(task, ctx)
        assert [call.args[0] for call in queue._async_agfs.write.await_args_list] == [
            f"{queue.path}/enqueue",
            f"{queue.path}/enqueue",
        ]
        first = await queue.dequeue_raw()
        second = await queue.dequeue_raw()
        assert first is not None and second is not None
        assert first["id"] != second["id"]
        assert json.loads(first["data"])["task_id"] == task.task_id
        assert json.loads(second["data"])["task_id"] == task.task_id
    finally:
        transport.close()


def test_explicit_provider_key_preserves_account_and_task_type_scope():
    service = ExternalTaskService()
    for task_type in ["compile", "other"]:
        service.register(
            SimpleNamespace(task_type=task_type, serialization_key=lambda payload: payload["to"])
        )
    task = TaskRecord(
        task_id="A",
        task_type="compile",
        account_id="租户",
        user_id="alice",
        meta={"request": {"to": "viking://resources/目标"}},
    )
    expected = '["租户","compile","target","viking://resources/目标"]'
    assert service.queue_serial_key(task) == expected
    task.user_id = "bob"
    task.task_id = "B"
    assert service.queue_serial_key(task) == expected
    task.account_id = "other"
    assert service.queue_serial_key(task) != expected
    task.account_id = "租户"
    task.task_type = "other"
    assert service.queue_serial_key(task) != expected
