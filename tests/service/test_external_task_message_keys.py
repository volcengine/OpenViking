"""Verify the optional provider key contract through native QueueFS."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.crypto.encryptor import build_credential_encryptor
from openviking.server.identity import RequestContext, Role
from openviking.service.compile_service import CompileService
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.fs_service import FSService
from openviking.service.task_domain import TaskRecord
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.encryption_config import EncryptionConfig
from openviking_cli.utils.config.open_viking_config import CompileApiConfig, ConnectorConfig
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_connector_monitors_for_same_target_can_be_dequeued_independently(
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
    service = ExternalTaskService(
        credential_encryptor=build_credential_encryptor(
            EncryptionConfig(local={"root_key": "42" * 32})
        )
    )
    provider = ConnectorDelegate(viking_fs=None)
    service.register(provider)
    config = ConnectorConfig(
        enable=True,
        connector="https://connector.example/doc/add",
        tracker="https://connector.example/task/info",
    )
    monkeypatch.setattr(
        "openviking.connector.delegate.get_openviking_config",
        lambda: SimpleNamespace(connector=config),
    )
    monkeypatch.setattr("openviking.connector.delegate.get_external_task_service", lambda: service)
    submit = AsyncMock(side_effect=[{"task_key": "remote-first"}, {"task_key": "remote-second"}])
    monkeypatch.setattr(provider, "_client", lambda _: SimpleNamespace(submit_doc_add=submit))
    ctx = RequestContext(
        user=UserIdentifier("acme", "alice"), role=Role.USER, api_key="test-api-key"
    )
    try:
        target = "viking://resources/target"
        first_task = await provider.create(
            "tos://doc-bucket/first.md", ctx, target, create_parent=True
        )
        second_task = await provider.create(
            "tos://doc-bucket/second.md", ctx, target, create_parent=True
        )
        assert first_task["connector_task_key"] == "remote-first"
        assert second_task["connector_task_key"] == "remote-second"
        first = await queue.dequeue_raw()
        second = await queue.dequeue_raw()
        assert first is not None and second is not None
        assert first["id"] != second["id"]
        assert json.loads(first["data"])["task_id"] == first_task["task_id"]
        assert json.loads(second["data"])["task_id"] == second_task["task_id"]
    finally:
        transport.close()


def test_explicit_provider_key_preserves_account_and_task_type_scope():
    service = ExternalTaskService()
    service.register(CompileService(CompileApiConfig(), tasks=service, fs=FSService()))
    service.register(
        SimpleNamespace(task_type="other", serialization_key=lambda payload: payload["to"])
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
