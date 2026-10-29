# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Failed handoff replacements must survive a real SQLite QueueFS restart."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.service import task_tracker as tracker_module
from openviking.service.reindex_executor import ReindexExecutor
from openviking.service.task_domain import TaskStatus, WorkState
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.service.task_work_index import bind_task_context, extract_task_metadata
from openviking.storage.errors import HandoffRecoveryBlocked
from openviking.storage.queuefs import queue_manager as manager_module
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking.storage.queuefs.content_write_msg import ContentWriteMsg
from openviking.storage.queuefs.content_write_processor import ContentWriteProcessor
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.storage.queuefs.reindex_msg import ReindexMsg
from openviking.storage.queuefs.reindex_processor import ReindexProcessor
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.queuefs.semantic_work import SemanticMessageWork, SkillSemanticMessageWork
from openviking.storage.viking_fs import VikingFS
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig

OWNER = {"account_id": "recovery", "user_id": "tester"}
OUTPUT = "/local/recovery/resources/result.md"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "consumer", ["AddResource", "Reindex", "ContentWrite", "Semantic", "SemanticSkill"]
)
async def test_handoff_replacement_enqueue_failure_survives_restart(
    tmp_path, monkeypatch, consumer
):
    """Use real retry methods, middleware, TaskStore, queue recovery and ACK.

    Reindex runs its full consumer with only lock contention and the expensive
    executor body substituted. Other cases drive their real retry methods from
    a small handler, including both real SemanticMessageWork implementations.
    """
    config = RagfsBindingConfig(
        agfs=AGFSConfig(
            path=str(tmp_path),
            backend="local",
            queuefs={"backend": "sqlite", "recover_stale_sec": 0},
        )
    )
    monkeypatch.setattr("openviking.storage.pathlock_recovery.sleep", AsyncMock())
    queue_name = "Semantic" if consumer.startswith("Semantic") else consumer
    message_type = {
        "AddResource": AddResourceMsg,
        "Reindex": ReindexMsg,
        "ContentWrite": ContentWriteMsg,
        "Semantic": SemanticMsg,
    }[queue_name]
    common = {"task_id": "task", **OWNER, "role": "root"}
    if queue_name == "AddResource":
        initial = AddResourceMsg(root_uri="viking://resources", path="/source/input.md", **common)
    elif queue_name == "Reindex":
        initial = ReindexMsg(
            uri="viking://resources/result.md", object_type="resource", mode="index", **common
        )
    elif queue_name == "ContentWrite":
        initial = ContentWriteMsg(
            operation="write",
            resource_id="viking://resources/result.md",
            result={},
            refresh={},
            **common,
        )
    else:
        initial = SemanticMsg(
            uri="viking://resources",
            context_type="skill" if consumer == "SemanticSkill" else "resource",
            **common,
        )

    blocked = True
    executed = []
    clients = []

    async def write_business_result(agfs, lease):
        await agfs.write(
            OUTPUT,
            b"business executed",
            fs_ctx={"account_id": OWNER["account_id"], "lease_ref": lease["lease_ref"]},
        )
        executed.append(OUTPUT)

    def open_runtime():
        client = create_agfs_client(config)
        clients.append(client)
        agfs = AsyncAGFSClient(client)
        tracker = TaskTracker(PersistentTaskStore(client))
        manager = QueueManager(client)
        monkeypatch.setattr(tracker_module, "_instance", tracker)
        monkeypatch.setattr(manager_module, "_instance", manager)
        queue = manager.get_queue(queue_name, allow_create=True)
        fs = VikingFS(client)
        processors = {
            "AddResource": lambda: AddResourceProcessor(None, queue_name, fs),
            "Reindex": lambda: ReindexProcessor(fs),
            "ContentWrite": lambda: ContentWriteProcessor(fs),
            "Semantic": lambda: SemanticProcessor(
                vlm_resolver=SimpleNamespace(get_vlm=AsyncMock())
            ),
        }
        processor = processors[queue_name]()

        if queue_name == "Reindex":

            async def acquire(msg, ctx):
                if blocked:
                    raise HandoffRecoveryBlocked("another owner holds coverage")
                return await agfs.pathlock_acquire_exact(OUTPUT)

            async def execute(_executor, *, lease_guard, **kwargs):
                await write_business_result(agfs, lease_guard.lease)
                return {"output": OUTPUT}

            monkeypatch.setattr(processor, "_adopt_or_reacquire", acquire)
            monkeypatch.setattr(ReindexExecutor, "_run", execute)
            queue.set_dequeue_handler(processor)
        else:

            class Handler(DequeueHandlerBase):
                async def on_dequeue(self, data):
                    msg = message_type.from_dict(json.loads(data["data"]))
                    if blocked:
                        error = HandoffRecoveryBlocked("another owner holds coverage")
                        if queue_name == "Semantic":
                            work_type = (
                                SkillSemanticMessageWork
                                if consumer == "SemanticSkill"
                                else SemanticMessageWork
                            )
                            work = work_type(processor, msg, caller_lock=None)
                            return await processor._requeue_semantic_msg_after_error(
                                msg, error, work=work
                            )
                        assert await processor._requeue_lock_handoff(msg, error)
                        return ProcessResult.requeued()
                    lease = await agfs.pathlock_acquire_exact(OUTPUT)
                    try:
                        await write_business_result(agfs, lease)
                    finally:
                        await agfs.pathlock_release(lease)
                    await tracker.complete("task", {"output": OUTPUT}, **OWNER)
                    return ProcessResult.success()

            queue.set_dequeue_handler(Handler())
        return agfs, tracker, manager, queue

    try:
        agfs, tracker, manager, queue = open_runtime()
        await agfs.ensure_parent_dirs(OUTPUT, fs_ctx={"account_id": OWNER["account_id"]})
        await tracker.create(
            "reindex" if queue_name == "Reindex" else "add_resource", task_id="task", **OWNER
        )
        await tracker.start("task", **OWNER)
        with bind_task_context("task", **OWNER):
            await queue.enqueue(initial if queue_name == "Semantic" else initial.to_dict())
        original = (await queue.snapshot())[0]
        metadata = extract_task_metadata(original)

        # Fault only the queue transport after registration, never the real
        # middleware or any retry method. No replacement reaches SQLite.
        original_write = queue._async_agfs.write
        attempted = []

        async def fail_enqueue(path, data, *args, **kwargs):
            if path.endswith("/enqueue"):
                attempted.append(json.loads(data))
                raise OSError("replacement enqueue failed")
            return await original_write(path, data, *args, **kwargs)

        monkeypatch.setattr(queue._async_agfs, "write", fail_enqueue)
        await queue.dequeue()
        assert len(attempted) == 1
        assert attempted[0]["_task_work_id"] == metadata.work_id
        assert attempted[0]["lock_handoff_retry"] == 1
        assert await tracker.get_work_state("task", metadata.work_id, **OWNER) is WorkState.RUNNING
        assert (await tracker.get("task", **OWNER)).status is TaskStatus.RUNNING
        assert await queue.snapshot() == [original]
        status = await queue.get_status()
        assert (status.pending, status.in_progress) == (0, 1)
        assert executed == []
        await agfs.run("close")
        clients.remove(manager._agfs)

        # Drop the original tracker, middleware, queue and native client.
        # RecoverStale and the production snapshot bootstrap resume the work.
        agfs, tracker, manager, queue = open_runtime()
        assert (await tracker.get("task", **OWNER)).status is TaskStatus.RUNNING
        status = await queue.get_status()
        assert (status.pending, status.in_progress) == (1, 0)
        await manager.prepare_task_tracking(tracker)
        assert await tracker.get_work_state("task", metadata.work_id, **OWNER) is WorkState.PENDING

        # Coverage is still blocked: the recovered original now successfully
        # publishes its replacement and is ACKed, keeping the same logical work.
        await queue.dequeue()
        replacement = (await queue.snapshot())[0]
        assert replacement["id"] != original["id"]
        assert extract_task_metadata(replacement).work_id == metadata.work_id
        assert json.loads(replacement["data"])["lock_handoff_retry"] == 1
        assert await tracker.get_work_state("task", metadata.work_id, **OWNER) is WorkState.PENDING

        blocked = False
        await queue.dequeue()
        assert executed == [OUTPUT]
        assert await queue.snapshot() == []
        assert await agfs.read(OUTPUT) == b"business executed"
        assert await tracker.get_work_state("task", metadata.work_id, **OWNER) is WorkState.DONE
        assert (await tracker.get("task", **OWNER)).status is TaskStatus.COMPLETED
        # A duplicate original cannot execute the business body twice.
        await queue.process_dequeued(original)
        assert executed == [OUTPUT]
        lease = await agfs.pathlock_acquire_exact(OUTPUT)
        await agfs.pathlock_release(lease)
        await agfs.run("close")
        clients.remove(manager._agfs)

        agfs, tracker, manager, queue = open_runtime()
        assert await queue.snapshot() == []
        assert (await tracker.get("task", **OWNER)).status is TaskStatus.COMPLETED
        assert await agfs.read(OUTPUT) == b"business executed"
    finally:
        for client in clients:
            await AsyncAGFSClient(client).run("close")
