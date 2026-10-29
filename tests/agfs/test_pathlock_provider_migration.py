# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Single-instance restart with persisted filesystem handoffs and real Redis."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.service import task_tracker as tracker_module
from openviking.service.reindex_executor import ReindexExecutor
from openviking.service.task_domain import TaskStatus, WorkState
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking.service.task_work_index import extract_task_metadata, get_task_context
from openviking.storage.errors import (
    HandoffRecoveryBlocked,
    HandoffRecoveryRequired,
    LockAcquisitionError,
)
from openviking.storage.pathlock_recovery import adopt_or_reacquire
from openviking.storage.queuefs import queue_manager as manager_module
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.storage.queuefs.reindex_processor import ReindexProcessor
from openviking.storage.viking_fs import VikingFS
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.cache_config import CacheConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("historic", [False, True])
async def test_filesystem_process_exit_then_immediate_redis_recovery(tmp_path, historic):
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL required for filesystem → Redis migration integration")
    started = time.monotonic()
    # The producer is a separate process; no old heartbeat or writer survives.
    source = """
import json, sys
from pathlib import Path
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig
client = create_agfs_client(RagfsBindingConfig(agfs=AGFSConfig(path=sys.argv[1], backend="local")))
ctx = {"account_id": "migration"}
requests = [
    {"path": "/local/migration/resources/tree", "kind": "tree"},
    {"path": "/local/migration/resources/信贷.md", "kind": "exact"},
]
client.ensure_parent_dirs("/local/migration/resources/tree/a.md", ctx=ctx)
client.write("/local/migration/resources/信贷.md", b"before", ctx=ctx)
lease = client.pathlock_acquire_batch(ctx, requests, 0.0, None)
handoff = client.pathlock_to_handoff(ctx, lease)
client.pathlock_handoff(ctx, lease)
Path(sys.argv[2]).write_text(json.dumps(handoff))
"""
    handoff_file = tmp_path / "handoff.json"
    completed = subprocess.run(
        [sys.executable, "-c", source, str(tmp_path), str(handoff_file)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    handoff = json.loads(handoff_file.read_text())
    assert handoff["handle_mode"] == "lock_path"
    coverage = handoff["covered_paths"]
    old_files = {p: p.read_bytes() for p in (tmp_path / "viking").rglob(".exact.ovlock.*")}
    assert len(old_files) == 2
    if historic:
        handoff.pop("handle_mode")
        handoff.pop("covered_paths")
        handoff["handle_id"] = handoff.pop("owner_id")
        handoff.pop("lease_ref")
    config = RagfsBindingConfig(
        agfs=AGFSConfig(path=str(tmp_path), backend="local", pathlock={"provider": "cache"}),
        cache=CacheConfig(
            provider="redis",
            params={"endpoints": [redis_url], "command_timeout_ms": 1000},
        ),
        service_name=f"migration-{uuid.uuid4().hex}",
    )
    agfs = AsyncAGFSClient(create_agfs_client(config))
    ctx = SimpleNamespace(account_id="migration")
    with pytest.raises(HandoffRecoveryRequired) as raised:
        await agfs.pathlock_adopt(handoff, fs_ctx={"account_id": "migration"})
    assert raised.value.reason == "provider_changed"

    async def operation_requests():
        return coverage

    lease = await adopt_or_reacquire(agfs, handoff, ctx=ctx, fallback_requests=operation_requests)
    try:
        assert lease["owner_id"] != handoff.get("owner_id", handoff.get("handle_id"))
        recovered = await agfs.pathlock_to_handoff(lease)
        assert recovered["covered_paths"] == coverage
        assert recovered["handle_mode"] == "logical_path"
        assert all(p.read_bytes() == data for p, data in old_files.items())
        with pytest.raises(LockAcquisitionError):
            await agfs.pathlock_acquire_exact("/local/migration/resources/tree/child.md")
        with pytest.raises(LockAcquisitionError):
            await agfs.pathlock_acquire_exact("/local/migration/resources/信贷.md")
        await agfs.write(
            "/local/migration/resources/tree/child.md",
            b"replayed",
            fs_ctx={"account_id": "migration", "lease_ref": lease["lease_ref"]},
        )
    finally:
        await agfs.pathlock_release(lease)
    next_lease = await agfs.pathlock_acquire_exact("/local/migration/resources/tree/child.md")
    await agfs.pathlock_release(next_lease)
    # Demonstrate recovery before the old 30-second lease expires.
    assert time.monotonic() - started < 30


_BACKLOG_PRODUCER = """
import asyncio, json, sys, time
from pathlib import Path
from openviking.pyagfs.async_client import AsyncAGFSClient
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker, set_task_tracker
from openviking.service.task_work_index import bind_task_context
from openviking.storage.pathlock_transfer import PathLockGuard
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking.storage.queuefs.reindex_msg import ReindexMsg
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig

async def main():
    client = create_agfs_client(RagfsBindingConfig(agfs=AGFSConfig(
        path=sys.argv[1], backend="local",
        queuefs={"backend": "sqlite", "recover_stale_sec": 0},
    )))
    agfs = AsyncAGFSClient(client)
    tracker = TaskTracker(PersistentTaskStore(client))
    set_task_tracker(tracker)
    manager = QueueManager(client)
    queue = manager.get_queue(manager.REINDEX, allow_create=True)
    ctx = {"account_id": "migration"}
    await agfs.ensure_parent_dirs("/local/migration/resources/tree/input.md", fs_ctx=ctx)
    await agfs.write("/local/migration/resources/tree/input.md", b"input", fs_ctx=ctx)
    await agfs.write("/local/migration/resources/信贷.md", b"before", fs_ctx=ctx)
    expected = []
    contended = len(sys.argv) > 5 and sys.argv[5] == "contended"
    if contended:
        await agfs.write("/local/migration/resources/tree/contended.md", b"", fs_ctx=ctx)
    targets = [("tree", "tree"), ("tree", "tree")] if contended else [
        ("tree", "tree"), ("信贷.md", "exact")]
    for suffix, kind in targets:
        path = "/local/migration/resources/" + suffix
        uri = "viking://resources/" + suffix
        task = await tracker.create("reindex", resource_id=uri,
                                    account_id="migration", user_id="tester")
        if not contended or not expected:
            guard = PathLockGuard(agfs)
            await guard.acquire(lambda: agfs.pathlock_acquire_batch(
                [{"path": path, "kind": kind}], fs_ctx=ctx))
            acquired_at = time.monotonic()
            handoff = await guard.to_handoff()
            old_owner = handoff["owner_id"]
            old_lease_ref = handoff["lease_ref"]
            if sys.argv[3] == "historic":
                handoff.pop("handle_mode")
                handoff.pop("covered_paths")
                handoff["handle_id"] = handoff.pop("owner_id")
                handoff.pop("lease_ref")
        msg = ReindexMsg(task_id=task.task_id, uri=uri, object_type="resources",
                         mode="semantic", account_id="migration",
                         user_id="tester", role="root", lock_handoff=handoff,
                         lock_handoff_retry=int(sys.argv[6]) if len(sys.argv) > 6 else 0)
        with bind_task_context(task.task_id, "migration", "tester"):
            if contended and expected:
                receipt = await queue.enqueue(msg.to_dict())
            else:
                receipt = await guard.handoff_and_dispatch(
                    handoff, lambda: queue.enqueue(msg.to_dict()))
        assert guard.transferred and guard.lease is None
        expected.append({"task_id": task.task_id, "work_id": receipt.work_id,
                         "uri": uri, "path": path,
                         "kind": kind, "old_owner": old_owner,
                         "old_lease_ref": old_lease_ref, "acquired_at": acquired_at})
    # Binding write returns a byte-count description, so obtain backend delivery
    # IDs from the durable snapshot rather than treating the receipt as an ID.
    deliveries = await queue.snapshot()
    for delivery in deliveries:
        payload = delivery["data"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        item = next(item for item in expected if item["task_id"] == payload["task_id"])
        item["message_id"] = delivery["id"]
    if sys.argv[4] == "interrupted":
        delivery = await queue.dequeue_raw()
        assert delivery["id"] == expected[0]["message_id"]
    status = await queue.get_status()
    assert status.pending == (1 if sys.argv[4] == "interrupted" else 2)
    assert status.in_progress == (1 if sys.argv[4] == "interrupted" else 0)
    assert len(await queue.snapshot()) == 2
    Path(sys.argv[2]).write_text(json.dumps(expected))
    # Exit without ACK, lock release, queue clearing, or waiting for lease expiry.

asyncio.run(main())
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("historic", [False, True], ids=["current", "historic"])
@pytest.mark.parametrize("interrupted", [False, True], ids=["pending", "unacked"])
async def test_sqlite_backlog_migrates_to_redis_and_is_consumed(
    tmp_path, monkeypatch, historic, interrupted
):
    """Real QueueFS restart, worker, Task/Work recovery, ACK and durable completion.

    Only the model-dependent executor body is replaced by a protected AGFS write.
    Queue middleware, ReindexProcessor, lock recovery and worker dispatch are real.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL required for filesystem → Redis migration integration")
    manifest = tmp_path / "expected.json"
    producer = subprocess.run(
        [
            sys.executable,
            "-c",
            _BACKLOG_PRODUCER,
            str(tmp_path),
            str(manifest),
            "historic" if historic else "current",
            "interrupted" if interrupted else "pending",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert producer.returncode == 0, producer.stderr
    expected = json.loads(manifest.read_text())
    by_uri = {item["uri"]: item for item in expected}
    old_files = {p: p.read_bytes() for p in (tmp_path / "viking").rglob(".exact.ovlock.*")}
    assert len(old_files) == 2
    # The previous writer has exited. Bootstrap against the same SQLite database,
    # immediately switching only the lock provider to Redis.
    config = RagfsBindingConfig(
        agfs=AGFSConfig(
            path=str(tmp_path),
            backend="local",
            queuefs={"backend": "sqlite", "recover_stale_sec": 0},
            pathlock={"provider": "cache"},
        ),
        cache=CacheConfig(
            provider="redis",
            params={"endpoints": [redis_url], "command_timeout_ms": 1000},
        ),
        service_name=f"backlog-migration-{uuid.uuid4().hex}",
    )
    client = create_agfs_client(config)
    agfs = AsyncAGFSClient(client)
    fs = VikingFS(client)
    tracker = TaskTracker(PersistentTaskStore(client))
    manager = QueueManager(client)
    monkeypatch.setattr(tracker_module, "_instance", tracker)
    monkeypatch.setattr(manager_module, "_instance", manager)
    queue = manager.get_queue(
        manager.REINDEX, dequeue_handler=ReindexProcessor(fs), allow_create=True
    )
    executed = []

    async def protected_executor(self, *, uri, ctx, lease_guard, **kwargs):
        item = by_uri[uri]
        task_ctx = get_task_context()
        assert task_ctx.task_id == item["task_id"]
        assert task_ctx.work_id == item["work_id"]
        assert ctx.account_id == "migration" and ctx.user.user_id == "tester"
        assert (
            await tracker.get_work_state(
                item["task_id"], item["work_id"], account_id="migration", user_id="tester"
            )
            is WorkState.RUNNING
        )
        lease = lease_guard.lease
        assert time.monotonic() - item["acquired_at"] < 30
        assert lease["owner_id"] != item["old_owner"]
        assert lease["lease_ref"] != item["old_lease_ref"]
        handoff = await agfs.pathlock_to_handoff(lease)
        assert handoff["handle_mode"] == "logical_path"
        assert handoff["covered_paths"] == [{"path": item["path"], "kind": item["kind"]}]
        output = item["path"] + "/consumed.md" if item["kind"] == "tree" else item["path"]
        with pytest.raises(LockAcquisitionError):
            await agfs.pathlock_acquire_exact(output)
        await agfs.write(
            output,
            item["task_id"].encode(),
            fs_ctx={"account_id": ctx.account_id, "lease_ref": lease["lease_ref"]},
        )
        executed.append(uri)
        return {"uri": uri, "output": output}

    monkeypatch.setattr(ReindexExecutor, "_run", protected_executor)
    try:
        status = await queue.get_status()
        assert (status.pending, status.in_progress) == (2, 0)
        messages = await queue.snapshot()
        assert {message["id"] for message in messages} == {item["message_id"] for item in expected}
        metadata = [extract_task_metadata(message) for message in messages]
        assert {entry.work_id for entry in metadata} == {item["work_id"] for item in expected}
        for item in expected:
            assert (
                await tracker.get_work_state(
                    item["task_id"], item["work_id"], account_id="migration", user_id="tester"
                )
                is None
            )
        restored = await manager.prepare_task_tracking(tracker)
        assert {task.task_id for task in restored} == {item["task_id"] for item in expected}
        for item in expected:
            assert (
                await tracker.get_work_state(
                    item["task_id"], item["work_id"], account_id="migration", user_id="tester"
                )
                is WorkState.PENDING
            )
        # Run the actual concurrent QueueManager worker rather than calling the
        # recovery helper or processor directly.
        manager.start()
        async with asyncio.timeout(10):
            while not (await queue.get_status()).is_complete:
                await asyncio.sleep(0.05)
        await asyncio.to_thread(manager.stop)
        status = await queue.get_status()
        assert status.processed == 2
        assert status.error_count == status.requeue_count == 0, status.errors
        assert sorted(executed) == sorted(by_uri)
        assert await queue.snapshot() == []
        assert all(p.read_bytes() == data for p, data in old_files.items())
        for item in expected:
            task = await tracker.get(item["task_id"], account_id="migration", user_id="tester")
            assert task.status is TaskStatus.COMPLETED
            assert task.result["uri"] == item["uri"]
            assert await agfs.read(task.result["output"]) == item["task_id"].encode()
            assert (
                await tracker.get_work_state(
                    item["task_id"], item["work_id"], account_id="migration", user_id="tester"
                )
                is WorkState.DONE
            )
            # Releasing the recovered Tree/Exact lease allows the next writer.
            lease = await agfs.pathlock_acquire_exact(task.result["output"])
            await agfs.pathlock_release(lease)
    finally:
        await asyncio.to_thread(manager.stop)
        await agfs.run("close")

    # Reopen persistent storage to rule out merely clearing an in-memory queue
    # or projecting task completion from the consumer's process-local cache.
    reopened = create_agfs_client(config)
    reopened_agfs = AsyncAGFSClient(reopened)
    try:
        restarted_queue = QueueManager(reopened).get_queue(QueueManager.REINDEX, allow_create=True)
        assert await restarted_queue.snapshot() == []
        assert (await restarted_queue.get_status()).is_complete
        restarted_tracker = TaskTracker(PersistentTaskStore(reopened))
        for item in expected:
            task = await restarted_tracker.get(
                item["task_id"], account_id="migration", user_id="tester"
            )
            assert task.status is TaskStatus.COMPLETED
            assert await reopened_agfs.read(task.result["output"]) == item["task_id"].encode()
    finally:
        await reopened_agfs.run("close")


@pytest.mark.asyncio
@pytest.mark.parametrize("historic", [False, True], ids=["current", "historic"])
async def test_two_queue_managers_recover_same_backlog_coverage(tmp_path, monkeypatch, historic):
    """Two native clients compete for one old handoff using real SQLite/Redis.

    Synchronization only forces the race and holds the winning write until the
    losing consumer reports a real recovery conflict. No lock result is mocked.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL required for filesystem → Redis migration integration")
    manifest = tmp_path / "contended.json"
    producer = subprocess.run(
        [
            sys.executable,
            "-c",
            _BACKLOG_PRODUCER,
            str(tmp_path),
            str(manifest),
            "historic" if historic else "current",
            "pending",
            "contended",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert producer.returncode == 0, producer.stderr
    expected = json.loads(manifest.read_text())
    by_task = {item["task_id"]: item for item in expected}
    assert len(by_task) == 2
    assert len({item["old_owner"] for item in expected}) == 1
    old_files = {p: p.read_bytes() for p in (tmp_path / "viking").rglob(".exact.ovlock.*")}
    assert len(old_files) == 1
    config = RagfsBindingConfig(
        agfs=AGFSConfig(
            path=str(tmp_path),
            backend="local",
            queuefs={"backend": "sqlite", "recover_stale_sec": 0},
            pathlock={"provider": "cache"},
        ),
        cache=CacheConfig(
            provider="redis",
            params={"endpoints": [redis_url], "command_timeout_ms": 1000},
        ),
        service_name=f"contended-migration-{uuid.uuid4().hex}",
    )
    # Open both backends before processing: SQLite startup recovery must not
    # reclaim a delivery that the other consumer has already started.
    clients = [create_agfs_client(config) for _ in range(2)]
    filesystems = [VikingFS(client) for client in clients]
    managers = [QueueManager(client, max_concurrent_reindex=1) for client in clients]
    tracker = TaskTracker(PersistentTaskStore(clients[0]))
    monkeypatch.setattr(tracker_module, "_instance", tracker)
    monkeypatch.setattr(manager_module, "_instance", managers[0])
    queues = []
    barrier = threading.Barrier(2)
    conflict_seen, entered, finish = threading.Event(), threading.Event(), threading.Event()
    state_lock = threading.Lock()
    adoptions = 0
    active = 0
    executed = []
    conflicts = []
    original_requeue = ReindexProcessor._requeue_lock_handoff

    async def record_conflict(self, msg, exc):
        if isinstance(exc, HandoffRecoveryBlocked):
            with state_lock:
                conflicts.append(msg.task_id)
            conflict_seen.set()
        return await original_requeue(self, msg, exc)

    monkeypatch.setattr(ReindexProcessor, "_requeue_lock_handoff", record_conflict)
    for fs, manager in zip(filesystems, managers, strict=True):
        original_adopt = fs._async_agfs.pathlock_adopt

        async def simultaneous_adopt(*args, original=original_adopt, **kwargs):
            nonlocal adoptions
            with state_lock:
                adoptions += 1
                synchronize = adoptions <= 2
            if synchronize:
                await asyncio.to_thread(barrier.wait, 5)
            return await original(*args, **kwargs)

        monkeypatch.setattr(fs._async_agfs, "pathlock_adopt", simultaneous_adopt)
        queues.append(
            manager.get_queue(
                manager.REINDEX, dequeue_handler=ReindexProcessor(fs), allow_create=True
            )
        )

    async def protected_executor(self, *, uri, ctx, lease_guard, **kwargs):
        nonlocal active
        task_id = get_task_context().task_id
        item = by_task[task_id]
        lease = lease_guard.lease
        agfs = lease_guard._agfs
        assert lease["owner_id"] != item["old_owner"]
        assert lease["lease_ref"] != item["old_lease_ref"]
        handoff = await agfs.pathlock_to_handoff(lease)
        assert handoff["covered_paths"] == [{"path": item["path"], "kind": "tree"}]
        output = item["path"] + "/contended.md"
        with state_lock:
            active += 1
            assert active == 1, "two consumers entered the protected write"
        try:
            entered.set()
            assert await asyncio.to_thread(finish.wait, 5), "winning write was never released"
            with pytest.raises(LockAcquisitionError):
                await filesystems[0]._async_agfs.pathlock_acquire_exact(output)
            previous = await agfs.read(output)
            await agfs.write(
                output,
                previous + task_id.encode() + b"\n",
                fs_ctx={"account_id": ctx.account_id, "lease_ref": lease["lease_ref"]},
            )
            with state_lock:
                executed.append((task_id, lease["owner_id"]))
            return {"uri": uri, "output": output}
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(ReindexExecutor, "_run", protected_executor)
    try:
        await managers[0].prepare_task_tracking(tracker)
        for manager in managers:
            manager.start()
        assert await asyncio.to_thread(entered.wait, 5), "neither consumer acquired recovery lock"
        assert await asyncio.to_thread(conflict_seen.wait, 5), (
            "no real recovery contention observed"
        )
        assert len(executed) == 0
        finish.set()
        async with asyncio.timeout(10):
            while not (await queues[0].get_status()).is_complete:
                await asyncio.sleep(0.05)
        for manager in managers:
            await asyncio.to_thread(manager.stop)
        assert len(conflicts) == 1
        assert {task_id for task_id, _ in executed} == set(by_task)
        assert len({owner for _, owner in executed}) == 2
        assert active == 0
        statuses = [await queue.get_status() for queue in queues]
        assert sum(status.requeue_count for status in statuses) == 1
        assert sum(status.error_count for status in statuses) == 0
        assert await queues[0].snapshot() == []
        assert all(p.read_bytes() == data for p, data in old_files.items())
        for item in expected:
            task = await tracker.get(item["task_id"], account_id="migration", user_id="tester")
            assert task.status is TaskStatus.COMPLETED
            assert (
                await tracker.get_work_state(
                    item["task_id"], item["work_id"], account_id="migration", user_id="tester"
                )
                is WorkState.DONE
            )
        agfs = filesystems[0]._async_agfs
        output = expected[0]["path"] + "/contended.md"
        assert set((await agfs.read(output)).decode().splitlines()) == set(by_task)
        lease = await agfs.pathlock_acquire_tree(expected[0]["path"])
        await agfs.pathlock_release(lease)
    finally:
        finish.set()
        for manager in managers:
            await asyncio.to_thread(manager.stop)
        for fs in filesystems:
            await fs._async_agfs.run("close")
    reopened = create_agfs_client(config)
    agfs = AsyncAGFSClient(reopened)
    try:
        assert set((await agfs.read(output)).decode().splitlines()) == set(by_task)
        assert (
            await QueueManager(reopened)
            .get_queue(QueueManager.REINDEX, allow_create=True)
            .snapshot()
            == []
        )
    finally:
        await agfs.run("close")


@pytest.mark.asyncio
async def test_recovery_retry_exhaustion_fails_task_and_drains_queue(tmp_path, monkeypatch):
    """A competing Redis owner survives; exhausted backlog is failed and ACKed."""
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL required for filesystem → Redis migration integration")
    manifest = tmp_path / "exhausted.json"
    producer = subprocess.run(
        [
            sys.executable,
            "-c",
            _BACKLOG_PRODUCER,
            str(tmp_path),
            str(manifest),
            "current",
            "pending",
            "contended",
            "8",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert producer.returncode == 0, producer.stderr
    expected = json.loads(manifest.read_text())
    client = create_agfs_client(
        RagfsBindingConfig(
            agfs=AGFSConfig(
                path=str(tmp_path),
                backend="local",
                queuefs={"backend": "sqlite", "recover_stale_sec": 0},
                pathlock={"provider": "cache"},
            ),
            cache=CacheConfig(
                provider="redis",
                params={"endpoints": [redis_url], "command_timeout_ms": 1000},
            ),
            service_name=f"exhausted-migration-{uuid.uuid4().hex}",
        )
    )
    fs = VikingFS(client)
    agfs = fs._async_agfs
    tracker = TaskTracker(PersistentTaskStore(client))
    manager = QueueManager(client)
    monkeypatch.setattr(tracker_module, "_instance", tracker)
    monkeypatch.setattr(manager_module, "_instance", manager)
    queue = manager.get_queue(
        manager.REINDEX, dequeue_handler=ReindexProcessor(fs), allow_create=True
    )

    async def forbidden_executor(*args, **kwargs):
        raise AssertionError("exhausted recovery must not execute business writes")

    monkeypatch.setattr(ReindexExecutor, "_run", forbidden_executor)
    competing = await agfs.pathlock_acquire_tree(expected[0]["path"])
    try:
        await manager.prepare_task_tracking(tracker)
        manager.start()
        async with asyncio.timeout(10):
            while not (await queue.get_status()).is_complete:
                await asyncio.sleep(0.05)
        await asyncio.to_thread(manager.stop)
        status = await queue.get_status()
        assert status.error_count == 2
        assert status.requeue_count == 0
        assert await queue.snapshot() == []
        assert await agfs.read(expected[0]["path"] + "/contended.md") == b""
        for item in expected:
            task = await tracker.get(item["task_id"], account_id="migration", user_id="tester")
            assert task.status is TaskStatus.FAILED
            assert (
                await tracker.get_work_state(
                    item["task_id"], item["work_id"], account_id="migration", user_id="tester"
                )
                is WorkState.FAILED
            )
        # Exhaustion must never release the unrelated competing writer's lease.
        with pytest.raises(LockAcquisitionError):
            await agfs.pathlock_acquire_exact(expected[0]["path"] + "/child.md")
    finally:
        await asyncio.to_thread(manager.stop)
        await agfs.pathlock_release(competing)
        await agfs.run("close")
