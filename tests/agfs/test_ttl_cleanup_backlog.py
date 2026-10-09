# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Drain real file/vector backlog through daily scheduling and strict cleanup."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking.service.ttl_cleanup import TTLCleanupService, _ttl_cleanup_message
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.vector_ids import vector_record_id
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import indexed_fs as indexed_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.service.test_ttl_daily_cleanup import CleanupQueue, scheduler_for
from tests.unit.service.test_ttl_daily_cleanup import clock as clock
from tests.unit.storage.ttl_test_storage import read_record


@pytest.mark.asyncio
async def test_backlog_strictly_drains_vectors_and_recovers_partial_failures(
    indexed_fs, monkeypatch, clock
):
    """Three native-storage pages, six silent vector failures, then a restart."""
    fs, vectors = indexed_fs
    ctx = root_ctx()
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    parent = "viking://user/default/memories/events"
    uris = [f"{parent}/{datetime(2025, 1, 1) + timedelta(days=i):%Y/%m/%d}" for i in range(205)]
    failures = set(uris[::40])
    live = parent + "/2026/09/29/live.txt"
    for owner in [*uris, live.rsplit("/", 1)[0]]:
        uri = owner + "/event.txt" if owner in uris else live
        content = "Expired L2" if owner in uris else "Live control"
        await fs.write_file(uri, content, ctx=ctx)
        if owner in uris:
            fields = {"expires_at": "2020-01-01T00:00:00.000Z"}
            await fs.write_file(owner + "/.meta.json", json.dumps(fields), ctx=ctx)
        await vectors.upsert(
            {
                "id": vector_record_id(ctx.account_id, uri, 2),
                "uri": uri,
                "level": 2,
                "abstract": content,
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            ctx=ctx,
        )

    skipped = set()
    delete = fs._delete_from_vector_store

    async def silent_vector_failure(targets, **kwargs):
        target = targets[-1]
        if target in failures and target not in skipped:
            skipped.add(target)
            return  # Backend reports success but leaves a real vector behind.
        await delete(targets, **kwargs)

    monkeypatch.setattr(fs, "_delete_from_vector_store", silent_vector_failure)
    tracker = TaskTracker(PersistentTaskStore(fs._async_agfs))
    set_task_tracker(tracker)
    queue = CleanupQueue()
    scheduler = scheduler_for(fs, queue)
    cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
    messages = {}
    try:
        for _ in range(4):
            await scheduler._scan_once()
            while queue.items:
                message = queue.items.popleft()
                messages[message["target"]["object_uri"]] = message
                await cleanup._process(message)
        assert set(messages) == set(uris)
        assert queue.peak == 100
        assert skipped == failures
        remaining = await vectors.query(ctx=ctx, limit=300)
        assert {row["uri"] for row in remaining} == {owner + "/event.txt" for owner in failures} | {
            live
        }
        for uri in failures:
            assert await read_record(fs, ctx.account_id, uri) is not None
            message = messages[uri]
            task = await tracker.get(
                message["task_id"], account_id=message["account_id"], user_id=message["user_id"]
            )
            assert task.status is TaskStatus.FAILED

        # Rebuild both task tracking and scheduling from their persisted state.
        tracker = TaskTracker(PersistentTaskStore(fs._async_agfs))
        set_task_tracker(tracker)
        scheduler = scheduler_for(fs, queue)
        clock.current += timedelta(days=1)
        await scheduler._scan_once()
        assert len(queue.items) >= len(failures)
        while queue.items:
            await cleanup._process(queue.items.popleft())
        for uri in uris:
            assert await read_record(fs, ctx.account_id, uri) is None
        remaining = await vectors.query(ctx=ctx, limit=300)
        assert {(row["uri"], row["level"]) for row in remaining} == {(live, 2)}
        assert await fs.read_file(live, ctx=ctx) == "Live control"
        for owner in uris:
            assert not await fs.exists(owner, ctx=ctx)
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["vector_confirmation", "silent_file", "file_error"])
async def test_partial_failure_retains_expiry_for_next_scan(
    indexed_fs, monkeypatch, clock, failure
):
    fs, vectors = indexed_fs
    ctx = root_ctx()
    owner = "viking://user/default/memories/events/2026/09/28"
    uri = owner + "/confirmation.txt"
    fields = {"expires_at": "2020-01-01T00:00:00.000Z"}
    await fs.write_file(uri, "Expired L2", ctx=ctx)
    await fs.write_file(owner + "/.meta.json", json.dumps(fields), ctx=ctx)
    await vectors.upsert(
        {
            "id": vector_record_id(ctx.account_id, uri, 2),
            "uri": uri,
            "level": 2,
            "abstract": "expired",
            "vector": [0.1, 0.2, 0.3, 0.4],
        },
        ctx=ctx,
    )
    record = await read_record(fs, ctx.account_id, owner)
    delete = AsyncMock(wraps=fs._delete_from_vector_store)
    monkeypatch.setattr(fs, "_delete_from_vector_store", delete)
    original_confirm = fs._confirm_vector_scope_cleared
    original_rm = fs._async_agfs.rm
    calls = 0

    async def delayed_confirmation(*args, **kwargs):
        nonlocal calls
        calls += 1
        await original_confirm(*args, **kwargs)  # Actual vectors are already gone.
        if failure == "vector_confirmation" and calls == 1:
            raise RuntimeError("simulate lagging count replica")

    monkeypatch.setattr(fs, "_confirm_vector_scope_cleared", delayed_confirmation)
    failed_file = False

    async def fail_first_file(path, **kwargs):
        nonlocal failed_file
        if (
            failure != "vector_confirmation"
            and path == fs._uri_to_path(uri, ctx=ctx)
            and not failed_file
        ):
            failed_file = True
            if failure == "file_error":
                raise OSError("file delete failed")
            return {}  # Acknowledged without removing the body.
        return await original_rm(path, **kwargs)

    monkeypatch.setattr(fs._async_agfs, "rm", fail_first_file)
    cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
    set_task_tracker(TaskTracker(PersistentTaskStore(fs._async_agfs)))
    try:
        assert (
            await cleanup._process(
                _ttl_cleanup_message(
                    record=record,
                )
            )
        ).outcome is ProcessOutcome.FAILED
        set_task_tracker(TaskTracker(PersistentTaskStore(fs._async_agfs)))
        assert await read_record(fs, ctx.account_id, owner) == record
        clock.current += timedelta(days=1)
        queue = CleanupQueue()
        await scheduler_for(fs, queue)._scan_once()
        assert (await cleanup._process(queue.items.popleft())).outcome is ProcessOutcome.SUCCESS
        assert delete.await_count == 2
        assert calls == 2
        assert await read_record(fs, ctx.account_id, owner) is None
        backend = await vectors._get_backend_for_context(ctx)
        assert await backend.strict_query(limit=10) == []
        assert not await fs.exists(owner, ctx=ctx, include_expired=True)
    finally:
        set_task_tracker(None)
