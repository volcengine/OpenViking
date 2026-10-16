# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for add_resource cancellation checkpoints (issue #5283, Bug 2).

A cancelled add_resource task used to keep materializing: the queue consumer
checked cancellation only once before a delivery started, so a REST cancel
that landed while the job was running neither stopped the writes nor prevented
a deleted resource subtree from being re-created. Queue consumers run on their
own worker threads (often a different process from the API that records the
cancel), so the checkpoints must observe the durable store, not only the
process-local cache.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking.storage.queuefs.add_resource_msg import AddResourceMsg, AddResourcePhase
from openviking.storage.queuefs.add_resource_processor import AddResourceProcessor
from openviking_cli.session.user_id import UserIdentifier

ACCOUNT = "acme"
USER = "alice"
TASK_ID = "task-cancel-1"


class _FakeAgfs:
    """Minimal AGFS double backing PersistentTaskStore (mirrors tests/test_task_tracker.py)."""

    def __init__(self):
        self.files = {}
        self.dirs = {"/", "/local"}

    def mkdir(self, path: str, mode: str = "755"):
        self.dirs.add(path.rstrip("/") or "/")
        return {"message": "created", "mode": mode}

    def write(self, path: str, data, max_retries: int = 3, *, ctx=None):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.files[path] = data
        self.dirs.add(path.rsplit("/", 1)[0] or "/")
        return "OK"

    def read(self, path: str, offset: int = 0, size: int = -1, stream: bool = False):
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        if size >= 0:
            return data[offset : offset + size]
        return data[offset:]

    def ls(self, path: str = "/"):
        prefix = path.rstrip("/") or "/"
        if prefix not in self.dirs:
            return []
        children = {}
        for directory in self.dirs:
            if directory != prefix and directory.startswith(prefix + "/"):
                child = directory[len(prefix) :].strip("/").split("/", 1)[0]
                children[f"{prefix}/{child}".replace("//", "/")] = True
        return [{"name": k, "isDir": True} for k in children]

    def rm(self, path: str):
        self.files.pop(path, None)
        self.dirs.discard(path)
        return {"message": "deleted"}


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier(ACCOUNT, USER), role=Role.USER)


@pytest.fixture
def trackers():
    """Worker/API tracker pair over one shared store, like separate processes."""
    agfs = _FakeAgfs()
    worker = TaskTracker(store=PersistentTaskStore(agfs))
    api = TaskTracker(store=PersistentTaskStore(agfs))
    set_task_tracker(worker)
    yield worker, api
    set_task_tracker(None)


async def _create_running_task(worker: TaskTracker) -> None:
    await worker.create(
        "add_resource",
        resource_id="viking://resources/repo",
        account_id=ACCOUNT,
        user_id=USER,
        task_id=TASK_ID,
    )
    await worker.start(TASK_ID, account_id=ACCOUNT, user_id=USER, stage="queued")


async def _staged_msg() -> AddResourceMsg:
    return AddResourceMsg(
        task_id=TASK_ID,
        job_phase=AddResourcePhase.SOURCE,
        root_uri="viking://resources/repo",
        path="/tmp/repo",
        account_id=ACCOUNT,
        user_id=USER,
        role="user",
        staged_source={
            "temp_uri": "viking://temp/task-cancel-1",
            "source_uri": "viking://temp/task-cancel-1/source/repo",
            "source_type": "local",
            "original_source": "/tmp/repo",
            "meta": {},
        },
    )


def _materialize_viking_fs(tracker_api: TaskTracker, file_count: int = 40) -> SimpleNamespace:
    """viking_fs double whose staged tree serves `file_count` file entries."""
    entries = [
        {
            "uri": f"viking://temp/{TASK_ID}/source/repo/file_{index}.txt",
            "rel_path": f"repo/file_{index}.txt",
            "isDir": False,
        }
        for index in range(file_count)
    ]
    reads = {"count": 0}

    async def read_file_bytes(uri, ctx=None, **kwargs):
        reads["count"] += 1
        if reads["count"] == 5:
            # The REST cancel lands while the job is mid-materialization.
            await tracker_api.cancel(TASK_ID, account_id=ACCOUNT, user_id=USER)
        return b"payload"

    return SimpleNamespace(
        tree=AsyncMock(return_value=entries),
        read_file_bytes=AsyncMock(side_effect=read_file_bytes),
        reads=reads,
    )


@pytest.mark.asyncio
async def test_is_cancelled_observes_store_across_trackers(trackers):
    worker, api = trackers
    await _create_running_task(worker)

    assert await worker.is_cancelled(TASK_ID, account_id=ACCOUNT, user_id=USER) is False

    # The API-process cancel must be visible to the worker's checkpoint even
    # though the worker cache still says running.
    await api.cancel(TASK_ID, account_id=ACCOUNT, user_id=USER)
    assert await worker.is_cancelled(TASK_ID, account_id=ACCOUNT, user_id=USER) is True
    assert await worker.is_cancelled("task-unknown", account_id=ACCOUNT, user_id=USER) is False


@pytest.mark.asyncio
async def test_is_cancelled_fast_path_on_local_cancellation(trackers):
    worker, _api = trackers
    await _create_running_task(worker)
    await worker.cancel(TASK_ID, account_id=ACCOUNT, user_id=USER)
    assert await worker.is_cancelled(TASK_ID, account_id=ACCOUNT, user_id=USER) is True


@pytest.mark.asyncio
async def test_cancelled_mid_materialization_stops_writes(trackers):
    worker, api = trackers
    await _create_running_task(worker)
    viking_fs = _materialize_viking_fs(api)
    service = ResourceService(
        vikingdb=object(),
        viking_fs=viking_fs,
        resource_processor=SimpleNamespace(),
        skill_processor=object(),
    )
    service._execute_resource_ingestion = AsyncMock()
    msg = await _staged_msg()

    with pytest.raises(asyncio.CancelledError):
        await service.execute_add_resource_job(
            msg,
            ctx=_ctx(),
            resource_lock=None,
            stage_callback=AsyncMock(),
        )

    # Ingestion must never start writing the resource tree, and the staged
    # entries after the observed cancellation must not be materialized.
    service._execute_resource_ingestion.assert_not_awaited()
    assert viking_fs.reads["count"] < 40


@pytest.mark.asyncio
async def test_cancelled_at_stage_boundary_settles_handler_cleanup(trackers):
    worker, api = trackers
    await _create_running_task(worker)
    lock = {"lease_ref": "lock-1"}

    async def job(msg, *, ctx, resource_lock, stage_callback, task_auth, **kwargs):
        await stage_callback("fetching")
        await api.cancel(TASK_ID, account_id=ACCOUNT, user_id=USER)
        await stage_callback("target_resolve")
        return {"status": "success"}

    resource_service = SimpleNamespace(
        execute_add_resource_job=AsyncMock(side_effect=job),
        record_watch_execution=AsyncMock(),
    )
    viking_fs = SimpleNamespace(
        delete_temp=AsyncMock(),
        _async_agfs=SimpleNamespace(
            pathlock_adopt=AsyncMock(return_value=lock),
            pathlock_release=AsyncMock(),
        ),
    )
    processor = AddResourceProcessor(resource_service, "add_resource", viking_fs)
    msg = AddResourceMsg(
        task_id=TASK_ID,
        job_phase=AddResourcePhase.SOURCE,
        root_uri="viking://resources/repo",
        path="/tmp/repo",
        account_id=ACCOUNT,
        user_id=USER,
        role="user",
        watch_task_id="watch-1",
        lock_handoff={"lease_ref": "handoff-1"},
        staged_source={
            "temp_uri": "viking://temp/task-cancel-1",
            "source_uri": "viking://temp/task-cancel-1/source/repo",
            "source_type": "local",
            "original_source": "/tmp/repo",
            "meta": {},
        },
    )
    data = {"id": "message-1", "data": msg.to_dict()}

    with pytest.raises(asyncio.CancelledError):
        await processor._process_bound(msg, data)

    resource_service.record_watch_execution.assert_awaited_once()
    assert resource_service.record_watch_execution.await_args.kwargs["status"] == "cancelled"
    viking_fs._async_agfs.pathlock_release.assert_awaited_once_with(lock)
    viking_fs.delete_temp.assert_awaited_once()
    settled = await worker.get(TASK_ID, account_id=ACCOUNT, user_id=USER)
    assert settled is not None
    assert settled.status in (TaskStatus.CANCELLING, TaskStatus.CANCELLED)
