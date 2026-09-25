# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Drain real file/vector backlog through daily scheduling and strict cleanup."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking.service.ttl_cleanup import TTLCleanupService, _ttl_cleanup_message
from openviking.storage.abstract_overview import render_abstract_overview
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.ttl_registry import TTLRegistry
from openviking.storage.vector_ids import vector_record_id
from openviking.utils.time_utils import parse_iso_datetime
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import indexed_fs as indexed_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.service.test_ttl_daily_cleanup import CleanupQueue, scheduler_for
from tests.unit.service.test_ttl_daily_cleanup import clock as clock


@pytest.mark.asyncio
async def test_backlog_strictly_drains_vectors_and_recovers_partial_failures(
    indexed_fs, monkeypatch, clock
):
    """Three native-storage pages, six silent vector failures, then a restart."""
    fs, vectors = indexed_fs
    ctx = root_ctx()
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    parent = "viking://user/default/memories/events/backlog"
    uris = [f"{parent}/e{i:03}.txt" for i in range(205)]
    failures = set(uris[::40])
    summaries = {}
    for level, filename in [(0, ".abstract.md"), (1, ".overview.md")]:
        uri = parent + "/" + filename
        summaries[uri] = render_abstract_overview(level, parent, f"Retained L{level}")
        await fs.write_file(uri, summaries[uri], ctx=ctx)
        await vectors.upsert(
            {
                "id": vector_record_id(ctx.account_id, parent, level),
                "uri": parent,
                "level": level,
                "abstract": f"Retained L{level}",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            ctx=ctx,
        )
    live = parent + "/live.txt"
    for uri in [*uris, live]:
        content = "Live control"
        if uri != live:
            fields = {"expires_at": "2020-01-01T00:00:00.000Z", "ttl_generation": uri}
            content = f"<!-- MEMORY_FIELDS {json.dumps(fields)} -->\nExpired L2"
        await fs.write_file(uri, content, ctx=ctx)
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
    scheduler = scheduler_for(fs.ttl_registry, queue)
    cleanup = TTLCleanupService(
        service=SimpleNamespace(viking_fs=fs), service_loop=asyncio.get_running_loop()
    )
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
        for uri in uris:
            record = await fs.ttl_registry.get(ctx.account_id, uri)
            assert (record is not None) == (uri in failures)
        remaining = await vectors.query(ctx=ctx, limit=300)
        assert {row["uri"] for row in remaining if row["level"] == 2} == failures | {live}
        for uri in failures:
            message = messages[uri]
            task = await tracker.get(
                message["task_id"], account_id=message["account_id"], user_id=message["user_id"]
            )
            assert task.status is TaskStatus.RUNNING
            assert task.stage == "retrying"

        # Rebuild both task tracking and scheduling from their persisted state.
        tracker = TaskTracker(PersistentTaskStore(fs._async_agfs))
        set_task_tracker(tracker)
        scheduler = scheduler_for(TTLRegistry(fs._async_agfs), queue)
        clock.current += timedelta(minutes=1)
        await scheduler._scan_once()
        assert len(queue.items) == len(failures)
        while queue.items:
            await cleanup._process(queue.items.popleft())
        # The first retry only confirms the accepted deletion. These six
        # injected no-op deletes still have vectors, so a later attempt repairs
        # them instead of either declaring success or polling forever.
        for uri in failures:
            assert await fs.ttl_registry.get(ctx.account_id, uri) is not None
        clock.current += timedelta(minutes=2)
        await scheduler._scan_once()
        assert len(queue.items) == len(failures)
        while queue.items:
            await cleanup._process(queue.items.popleft())
        for uri in uris:
            assert await fs.ttl_registry.get(ctx.account_id, uri) is None
        remaining = await vectors.query(ctx=ctx, limit=300)
        assert {(row["uri"], row["level"]) for row in remaining} == {
            (parent, 0),
            (parent, 1),
            (live, 2),
        }
        for uri, content in summaries.items():
            assert await fs.read_file(uri, ctx=ctx) == content
        assert await fs.read_file(live, ctx=ctx) == "Live control"
        files = await fs._async_agfs.ls(fs._uri_to_path(parent, ctx=ctx), limit=300)
        assert not any(entry["name"].startswith("e") for entry in files)
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
async def test_confirmation_lag_recovers_without_repeating_delete(indexed_fs, monkeypatch, clock):
    fs, vectors = indexed_fs
    ctx = root_ctx()
    uri = "viking://user/default/memories/events/confirmation.txt"
    fields = {"expires_at": "2020-01-01T00:00:00.000Z", "ttl_generation": "confirmation"}
    await fs.write_file(uri, f"<!-- MEMORY_FIELDS {json.dumps(fields)} -->\nExpired L2", ctx=ctx)
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
    record = await fs.ttl_registry.get(ctx.account_id, uri)
    delete = AsyncMock(wraps=fs._delete_from_vector_store)
    monkeypatch.setattr(fs, "_delete_from_vector_store", delete)
    original_confirm = fs._confirm_vector_scope_cleared
    calls = 0

    async def delayed_confirmation(*args, **kwargs):
        nonlocal calls
        calls += 1
        await original_confirm(*args, **kwargs)  # Actual vectors are already gone.
        if calls == 1:
            raise RuntimeError("simulate lagging count replica")

    monkeypatch.setattr(fs, "_confirm_vector_scope_cleared", delayed_confirmation)
    cleanup = TTLCleanupService(
        service=SimpleNamespace(viking_fs=fs), service_loop=asyncio.get_running_loop()
    )
    set_task_tracker(TaskTracker(PersistentTaskStore(fs._async_agfs)))
    try:
        assert (
            await cleanup._process(_ttl_cleanup_message(record=record))
        ).outcome is ProcessOutcome.REQUEUED
        set_task_tracker(TaskTracker(PersistentTaskStore(fs._async_agfs)))
        fs.ttl_registry = TTLRegistry(fs._async_agfs)
        item = await fs.ttl_registry.get_scheduled(ctx.account_id, uri)
        clock.current = parse_iso_datetime(item["run_at"])
        queue = CleanupQueue()
        await scheduler_for(fs.ttl_registry, queue)._scan_once()
        assert (await cleanup._process(queue.items.popleft())).outcome is ProcessOutcome.SUCCESS
        delete.assert_awaited_once()
        assert calls == 2
        assert await fs.ttl_registry.get(ctx.account_id, uri) is None
    finally:
        set_task_tracker(None)
