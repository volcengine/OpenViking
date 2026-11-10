# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real HTTP Connector transport with data callbacks on local RAGFS."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import ragfs_python

from openviking.connector import delegate as connector_delegate_module
from openviking.resource.watch_manager import WatchManager
from openviking.resource.watch_runtime import WatchRuntime
from openviking.resource.watch_store_file import FileWatchStore
from openviking.server.identity import RequestContext, Role
from openviking.service import external_task_service as external_task_module
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.resource_service import ResourceService
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.storage.queuefs import QueueManager
from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.encryption_config import EncryptionConfig

TARGET = "viking://resources/connector"
OWNER = {"account_id": "default", "user_id": "default"}


@pytest.mark.asyncio
async def test_first_import_and_trigger_allow_http_connector_data_callback(monkeypatch, watch_vfs):
    tracker = TaskTracker(PersistentTaskStore(watch_vfs._async_agfs))
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(connector_delegate_module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(external_task_module, "get_task_tracker", lambda: tracker)
    ctx = RequestContext(
        user=UserIdentifier("default", "default"), role=Role.ROOT, api_key="test-key"
    )
    await watch_vfs.mkdir(TARGET, ctx=ctx, exist_ok=True)
    resources = ResourceService(
        vikingdb=object(),
        viking_fs=watch_vfs,
        resource_processor=SimpleNamespace(),
        skill_processor=object(),
    )
    service = WatchManager(
        FileWatchStore(watch_vfs),
        viking_fs=watch_vfs,
        tracker=tracker,
        resource_service=resources,
    )
    resources._watch_manager = service
    await service.initialize()
    from openviking.crypto.encryptor import build_credential_encryptor

    encryption = EncryptionConfig(local={"root_key": "42" * 32})
    external_tasks = ExternalTaskService(
        credential_encryptor=build_credential_encryptor(encryption)
    )
    external_tasks.register(resources._connector)
    monkeypatch.setattr(external_task_module, "_instance", external_tasks)
    transport = ragfs_python.RAGFSBindingClient()
    transport.mount("queuefs", "/queue", {"backend": "memory"})
    queues = QueueManager(transport)
    queue = queues.get_queue(
        queues.EXTERNAL_TASK,
        dequeue_handler=ExternalTaskProcessor(external_tasks),
        allow_create=True,
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queues)
    published = asyncio.Event()
    enqueue = queues.enqueue

    async def publish(*args, **kwargs):
        receipt = await enqueue(*args, **kwargs)
        published.set()
        return receipt

    monkeypatch.setattr(queues, "enqueue", publish)

    async def execute_round(submit):
        submission = asyncio.create_task(submit)
        try:
            await asyncio.wait_for(published.wait(), 5)
            published.clear()
            # The public receipt must not wait for a monitoring worker to dequeue.
            receipt = await asyncio.wait_for(submission, 5)
            task_id = receipt["task_id"] if isinstance(receipt, dict) else receipt.task_id
            record = await tracker.get(task_id, **OWNER)
            stored_auth = await tracker.get_task_auth(record.task_id, **OWNER)
            assert "test-key" not in json.dumps(stored_auth)
            assert record.meta["connector_task_key"] == f"external-{len(submitted)}"
            # A fresh monitor can recover credentials using the configured key.
            recovered = ExternalTaskService(
                credential_encryptor=build_credential_encryptor(encryption)
            )
            recovered.register(resources._connector)
            queue.set_dequeue_handler(ExternalTaskProcessor(recovered))
            await asyncio.wait_for(queue.dequeue(), 5)
            return receipt
        finally:
            if not submission.done():
                submission.cancel()
            await asyncio.gather(submission, return_exceptions=True)

    runtime = WatchRuntime(service, leader_enabled=False)
    submitted, errors, stream_states = [], [], []

    async def handle(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode()
            length = next(
                int(line.split(":", 1)[1])
                for line in headers.split("\r\n")
                if line.lower().startswith("content-length:")
            )
            request = json.loads(await reader.readexactly(length))
            if headers.startswith("POST /doc/add "):
                watches = await service.get_all_tasks("default", "default", "root")
                assert len(watches) == 1
                state = await service.store.get_state(watches[0].task_id)
                record = await tracker.get(state.current_run_id, **OWNER)
                assert record and record.task_type == "connector_import"
                assert not record.is_terminal()
                assert request["to"] == TARGET
                async with service.locks.tree("default", TARGET) as lease:
                    # The external importer can write data before replying.
                    await watch_vfs.write_file(
                        TARGET + "/downloaded.txt",
                        f"round {len(submitted) + 1}",
                        ctx=ctx,
                        lease_ref=lease,
                    )
                submitted.append(record.task_id)
                stream_states.append(request.get("stream_states"))
                data = {"task_key": f"external-{len(submitted)}"}
            else:
                data = {"status": "succeeded", "stream_states": {"cursor": len(submitted)}}
            body = json.dumps({"code": 0, "data": data}).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except Exception as exc:
            errors.append(exc)
            body = str(exc).encode()
            writer.write(
                b"HTTP/1.1 500 Internal Server Error\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    config = SimpleNamespace(
        enable=True,
        connector=f"http://127.0.0.1:{port}/doc/add",
        tracker=f"http://127.0.0.1:{port}/task/info",
        allowed_add_types=["tos"],
        poll_interval_ms=1,
        timeout_seconds=5,
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.open_viking_config.get_openviking_config",
        lambda: SimpleNamespace(connector=config, encryption=encryption),
    )
    monkeypatch.setattr(
        connector_delegate_module,
        "get_openviking_config",
        lambda: SimpleNamespace(connector=config, encryption=encryption),
    )
    await runtime.start()
    try:
        first = await execute_round(
            resources.add_resource(path="tos://bucket/docs/", ctx=ctx, to=TARGET, watch_interval=1)
        )
        assert first["watch_id"] and first["task_id"] == submitted[0]
        await runtime.sweep()
        receipt = await execute_round(service.trigger(first["watch_id"]))
        await runtime.sweep()
        state = await service.store.get_state(first["watch_id"])
        assert not errors
        assert submitted == [first["task_id"], receipt.task_id]
        assert submitted[0] != submitted[1]
        assert stream_states == [None, {"cursor": 1}]
        assert state.task.connector_states == {"cursor": 2}
        assert state.current_run_id is None and state.task.last_status == "completed"
        assert len(await tracker.list_tasks(**OWNER)) == 2
        assert await watch_vfs.read_file(TARGET + "/downloaded.txt", ctx=ctx) == "round 2"
    finally:
        server.close()
        await server.wait_closed()
        await resources.close_background_tasks()
        await runtime.stop()
        await service.close()
        await tracker.close()
        transport.close()
        assert not errors, errors
