# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Correctness tests for registry-driven, generation-fenced TTL cleanup."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.core.ttl import OBJECT_TYPE_EVENT, OBJECT_TYPE_SESSION
from openviking.pyagfs.exceptions import AGFSNetworkError, AGFSTimeoutError
from openviking.service import ttl_cleanup
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking.storage.ttl_registry import TTLRecord
from openviking_cli.exceptions import NotFoundError


class _TaskStore:
    def __init__(self):
        self.tasks = {}

    async def create(self, task):
        self.tasks[task.task_id] = task

    async def update(self, task):
        self.tasks[task.task_id] = task

    async def get(self, task_id, *, account_id=None, user_id=None):
        return None

    async def list(self, account_id, *, user_id=None):
        return []

    async def delete(self, task_id, *, account_id, user_id=None):
        self.tasks.pop(task_id, None)


SESSION_URI = "viking://user/u1/sessions/s1"
EVENT_URI = "viking://user/u1/memories/events/2026/e.md"
PAST = "2020-01-01T00:00:00.000Z"
FUTURE = "2999-01-01T00:00:00.000Z"
GENERATION = "generation-1"


def _record(
    object_type: str = OBJECT_TYPE_SESSION,
    *,
    object_uri: str = SESSION_URI,
    expires_at: str = PAST,
    generation: str = GENERATION,
) -> TTLRecord:
    return TTLRecord(
        object_uri=object_uri,
        object_type=object_type,
        account_id="acct",
        user_id="u1",
        expires_at=expires_at,
        generation=generation,
    )


def _message(record: TTLRecord, *, task_id: str = "task-1", retry_count: int = 0) -> dict:
    return ttl_cleanup._ttl_cleanup_message(record=record, task_id=task_id, retry_count=retry_count)


def _session_meta(expires_at: str = PAST, generation: str = GENERATION) -> str:
    return json.dumps({"expires_at": expires_at, "ttl_generation": generation})


def _event_body(expires_at: str = PAST, generation: str = GENERATION) -> str:
    fields = {"expires_at": expires_at, "ttl_generation": generation}
    return f"<!-- MEMORY_FIELDS {json.dumps(fields)} -->\nbody text"


def _make_service(
    *,
    record: TTLRecord,
    live_content: str | Exception,
    rm_error: Exception | None = None,
):
    registry = SimpleNamespace(
        get=AsyncMock(return_value=record),
        get_scheduled=AsyncMock(
            return_value={"payload": {"record": asdict(record), "retry_count": 0}}
        ),
        upsert=AsyncMock(),
        defer_retry=AsyncMock(return_value=True),
        remove_if_generation=AsyncMock(return_value=True),
    )
    read_file = (
        AsyncMock(side_effect=live_content)
        if isinstance(live_content, Exception)
        else AsyncMock(return_value=live_content)
    )
    agfs = SimpleNamespace(
        pathlock_acquire_tree=AsyncMock(return_value={"lease_ref": "tree"}),
        pathlock_acquire_exact=AsyncMock(return_value={"lease_ref": "exact"}),
        pathlock_acquire_batch=AsyncMock(return_value={"lease_ref": "batch"}),
        pathlock_release=AsyncMock(),
    )
    viking_fs = SimpleNamespace(
        ttl_registry=registry,
        _async_agfs=agfs,
        _uri_to_path=lambda uri, ctx=None: f"/local/acct/{uri.removeprefix('viking://')}",
        read_file=read_file,
        remove_files=AsyncMock(),
        rm=AsyncMock(side_effect=rm_error),
        _delete_from_vector_store=AsyncMock(),
        _confirm_vector_uris_cleared=AsyncMock(),
        _count_cache={"stale": (1, 0)},
    )
    queue = SimpleNamespace(snapshot=AsyncMock(return_value=[]), enqueue=AsyncMock())
    queue_manager = SimpleNamespace(
        TTL_CLEANUP="ttl_cleanup",
        SEMANTIC="semantic",
        get_queue=lambda name, **kwargs: queue,
        enqueue=AsyncMock(),
    )
    service = SimpleNamespace(viking_fs=viking_fs, _queue_manager=queue_manager)
    cleanup = ttl_cleanup.TTLCleanupService.__new__(ttl_cleanup.TTLCleanupService)
    cleanup._service = service
    return cleanup, viking_fs, registry, queue_manager


@pytest.fixture
def tracker():
    value = TaskTracker(_TaskStore())
    set_task_tracker(value)
    try:
        yield value
    finally:
        set_task_tracker(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("record", "content", "recursive", "lock_name"),
    [
        (_record(), _session_meta(), True, "pathlock_acquire_tree"),
        (
            _record(OBJECT_TYPE_EVENT, object_uri=EVENT_URI),
            _event_body(),
            False,
            "pathlock_acquire_batch",
        ),
        *[
            (
                _record(OBJECT_TYPE_EVENT, object_uri=EVENT_URI.removesuffix(".md") + extension),
                _event_body(),
                False,
                "pathlock_acquire_batch",
            )
            for extension in (".MD", ".txt", ".TXT")
        ],
        *[
            (
                _record(
                    OBJECT_TYPE_EVENT,
                    object_uri=EVENT_URI.rsplit("/", 1)[0] + "/" + filename,
                ),
                _event_body(),
                False,
                "pathlock_acquire_batch",
            )
            for filename in (".note.md", ".note.txt")
        ],
    ],
)
async def test_expired_object_is_deleted_strictly_and_registry_removed(
    tracker, record, content, recursive, lock_name
):
    cleanup, viking_fs, registry, _ = _make_service(record=record, live_content=content)

    result = await cleanup._process(_message(record))

    assert result.outcome is ProcessOutcome.SUCCESS
    getattr(viking_fs._async_agfs, lock_name).assert_awaited_once()
    viking_fs.rm.assert_any_await(
        record.object_uri,
        recursive=recursive,
        ctx=viking_fs.rm.await_args.kwargs["ctx"],
        lease_ref=viking_fs.rm.await_args.kwargs["lease_ref"],
        strict=True,
        preserve_summaries=True,
    )
    assert viking_fs.rm.await_count == 1
    viking_fs._delete_from_vector_store.assert_not_awaited()
    viking_fs._confirm_vector_uris_cleared.assert_not_awaited()
    registry.remove_if_generation.assert_awaited_once_with(
        record.account_id, record.object_uri, record.generation
    )
    assert viking_fs._count_cache == {}
    task = await tracker.get(
        "task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    assert task.status is TaskStatus.COMPLETED
    assert task.result == {"deleted": True, "source_missing": False}


@pytest.mark.asyncio
async def test_missing_source_still_runs_strict_delete_for_orphan_vectors(tracker):
    record = _record(OBJECT_TYPE_EVENT, object_uri=EVENT_URI)
    cleanup, viking_fs, registry, _ = _make_service(
        record=record, live_content=NotFoundError(EVENT_URI, "file")
    )

    result = await cleanup._process(_message(record))

    assert result.outcome is ProcessOutcome.SUCCESS
    assert viking_fs.rm.await_count == 1
    assert viking_fs.rm.await_args_list[0].kwargs["strict"] is True
    registry.remove_if_generation.assert_awaited_once()
    task = await tracker.get(
        "task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    assert task.result == {"deleted": True, "source_missing": True}


@pytest.mark.asyncio
async def test_renewal_wins_under_object_lock(tracker):
    record = _record()
    cleanup, viking_fs, registry, _ = _make_service(
        record=record, live_content=_session_meta(FUTURE)
    )

    result = await cleanup._process(_message(record))

    assert result.outcome is ProcessOutcome.SUCCESS
    viking_fs.rm.assert_not_awaited()
    registry.remove_if_generation.assert_not_awaited()
    task = await tracker.get(
        "task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    assert task.result == {"deleted": False, "skipped": "renewed"}


@pytest.mark.asyncio
async def test_future_live_expiry_repairs_earlier_registry_deadline(tracker):
    registered = _record(expires_at=PAST)
    cleanup, viking_fs, registry, _ = _make_service(
        record=registered, live_content=_session_meta(FUTURE)
    )

    result = await cleanup._process(_message(registered))

    assert result.outcome is ProcessOutcome.SUCCESS
    viking_fs.rm.assert_not_awaited()
    registry.upsert.assert_awaited_once()
    repaired = registry.upsert.await_args.args[0]
    assert repaired.generation == registered.generation
    assert repaired.expires_at == FUTURE


@pytest.mark.asyncio
async def test_old_task_repairs_registry_instead_of_deleting_recreated_object(tracker):
    old = _record(OBJECT_TYPE_EVENT, object_uri=EVENT_URI)
    cleanup, viking_fs, registry, _ = _make_service(
        record=old, live_content=_event_body(PAST, generation="generation-2")
    )

    result = await cleanup._process(_message(old))

    assert result.outcome is ProcessOutcome.SUCCESS
    viking_fs.rm.assert_not_awaited()
    registry.upsert.assert_awaited_once()
    replacement = registry.upsert.await_args.args[0]
    assert replacement.object_uri == EVENT_URI
    assert replacement.generation == "generation-2"
    registry.remove_if_generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_registry_generation_is_a_noop(tracker):
    scheduled = _record()
    current = _record(generation="generation-2")
    cleanup, viking_fs, registry, _ = _make_service(record=current, live_content=_session_meta())

    result = await cleanup._process(_message(scheduled))

    assert result.outcome is ProcessOutcome.SUCCESS
    viking_fs.read_file.assert_not_awaited()
    viking_fs.rm.assert_not_awaited()
    registry.remove_if_generation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [AGFSNetworkError, AGFSTimeoutError])
async def test_source_outage_with_not_found_text_retries_without_deleting(tracker, error_type):
    record = _record(OBJECT_TYPE_EVENT, object_uri=EVENT_URI)
    cleanup, fs, registry, _ = _make_service(
        record=record, live_content=error_type("backend endpoint not found")
    )

    result = await cleanup._process(_message(record))

    assert result.outcome is ProcessOutcome.REQUEUED
    fs.rm.assert_not_awaited()
    registry.remove_if_generation.assert_not_awaited()
    registry.defer_retry.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_failure_is_requeued_without_terminal_failure(tracker):
    record = _record()
    cleanup, _, registry, queue_manager = _make_service(
        record=record,
        live_content=_session_meta(),
        rm_error=RuntimeError("vector residue"),
    )

    registry.get_scheduled.return_value["payload"]["retry_count"] = 2
    result = await cleanup._process(_message(record, retry_count=2))

    assert result.outcome is ProcessOutcome.REQUEUED
    registry.remove_if_generation.assert_not_awaited()
    queue_manager.enqueue.assert_not_awaited()
    registry.defer_retry.assert_awaited_once()
    retry = registry.defer_retry.await_args.kwargs
    assert retry["task_id"] == "task-1"
    assert retry["retry_count"] == 3
    assert ttl_cleanup.hidden_by_ttl(retry["next_retry_at"]) is False
    task = await tracker.get(
        "task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    assert task.status is TaskStatus.RUNNING
    assert task.stage == "retrying"


@pytest.mark.asyncio
async def test_paused_cleanup_is_durably_deferred_without_deleting(monkeypatch, tracker):
    record = _record()
    cleanup, viking_fs, registry, _ = _make_service(record=record, live_content=_session_meta())
    monkeypatch.setattr(
        ttl_cleanup,
        "_cleanup_settings",
        lambda: SimpleNamespace(enabled=False, check_interval_seconds=45),
    )

    result = await cleanup._process(_message(record))

    assert result.outcome is ProcessOutcome.REQUEUED
    viking_fs.read_file.assert_not_awaited()
    viking_fs.rm.assert_not_awaited()
    retry = registry.defer_retry.await_args.kwargs
    assert retry["retry_count"] == 0
    assert retry["task_id"] == "task-1"
    assert ttl_cleanup.hidden_by_ttl(retry["next_retry_at"]) is False
    task = await tracker.get(
        "task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    assert task.status is TaskStatus.PENDING
    assert task.stage == "paused"


@pytest.mark.asyncio
async def test_ack_of_deferred_retry_does_not_complete_the_business_task(tracker):
    from openviking.service.task_queue_middleware import TaskWorkQueueMiddleware
    from openviking.storage.queuefs.queue_middleware import (
        AckContext,
        EnqueueContext,
        ProcessContext,
    )

    record = _record()
    cleanup, _, registry, _ = _make_service(
        record=record,
        live_content=_session_meta(),
        rm_error=RuntimeError("backend unavailable"),
    )
    middleware = TaskWorkQueueMiddleware(tracker._work_index)
    enqueue = EnqueueContext("ttl_cleanup", _message(record))

    async def persist(ctx):
        ctx.committed = True
        return "message-1"

    await middleware.enqueue(enqueue, persist)
    delivery = {"id": "message-1", "data": enqueue.payload}

    async def process(ctx):
        return await cleanup._process(ctx.message["data"])

    outcome = await middleware.process(
        ProcessContext("ttl_cleanup", delivery, cancel=AsyncMock()),
        process,
    )
    assert outcome.outcome is ProcessOutcome.REQUEUED
    registry.defer_retry.assert_awaited_once()

    async def ack(ctx):
        ctx.committed = True

    await middleware.ack(AckContext("ttl_cleanup", "message-1", delivery), ack)
    task = await tracker.get(
        "task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    assert task.status is TaskStatus.RUNNING
    assert task.result is None


@pytest.mark.asyncio
async def test_retry_persistence_failure_leaves_delivery_unacknowledged(tracker):
    cleanup, _, registry, _ = _make_service(
        record=_record(),
        live_content=_session_meta(),
        rm_error=RuntimeError("vector failure"),
    )
    registry.defer_retry.side_effect = RuntimeError("retry store unavailable")
    with pytest.raises(RuntimeError, match="retry store unavailable"):
        await cleanup._process(_message(_record()))


@pytest.mark.asyncio
async def test_event_cleanup_preserves_summaries_and_does_not_schedule_rebuild(tracker):
    record = _record(OBJECT_TYPE_EVENT, object_uri=EVENT_URI)
    cleanup, fs, registry, queues = _make_service(record=record, live_content=_event_body())
    result = await cleanup._process(_message(record))
    assert result.outcome is ProcessOutcome.SUCCESS
    assert [call.args[0] for call in fs.rm.await_args_list] == [EVENT_URI]
    assert fs.rm.await_args.kwargs["preserve_summaries"] is True
    fs._delete_from_vector_store.assert_not_awaited()
    queues.get_queue(queues.SEMANTIC).enqueue.assert_not_awaited()
    registry.remove_if_generation.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_delivery_is_idempotent(tracker):
    record = _record()
    await tracker.create(
        "ttl_cleanup",
        resource_id=record.object_uri,
        task_id="task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    await tracker.complete(
        "task-1",
        {"deleted": True},
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    cleanup, viking_fs, _, _ = _make_service(record=record, live_content=_session_meta())

    result = await cleanup._process(_message(record))

    assert result.outcome is ProcessOutcome.SUCCESS
    viking_fs.read_file.assert_not_awaited()
    viking_fs.rm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
async def test_failed_or_cancelled_task_does_not_ack_before_physical_cleanup(
    tracker, terminal_status
):
    record = _record()
    await tracker.create(
        "ttl_cleanup",
        resource_id=record.object_uri,
        task_id="task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    if terminal_status is TaskStatus.FAILED:
        await tracker.fail(
            "task-1",
            "old process failed",
            account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
            user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
        )
    else:
        await tracker.mark_cancelled(
            "task-1",
            account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
            user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
        )
    cleanup, viking_fs, registry, queue_manager = _make_service(
        record=record, live_content=_session_meta()
    )

    result = await cleanup._process(_message(record))

    assert result.outcome is ProcessOutcome.REQUEUED
    viking_fs.rm.assert_not_awaited()
    registry.remove_if_generation.assert_not_awaited()
    queue_manager.enqueue.assert_not_awaited()
    retry = registry.defer_retry.await_args.kwargs
    replacement = _message(record, task_id=retry["task_id"], retry_count=retry["retry_count"])
    registry.get_scheduled.return_value["payload"]["retry_count"] = retry["retry_count"]
    assert replacement["task_id"] != "task-1"
    assert replacement["retry_count"] == 1

    result = await cleanup._process(replacement)

    assert result.outcome is ProcessOutcome.SUCCESS
    viking_fs.rm.assert_awaited_once()
    registry.remove_if_generation.assert_awaited_once()
    replacement_task = await tracker.get(
        replacement["task_id"],
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    assert replacement_task.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancelled_delivery_callback_requeues_with_fresh_task_id(tracker):
    record = _record()
    await tracker.create(
        "ttl_cleanup",
        resource_id=record.object_uri,
        task_id="task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    await tracker.mark_cancelled(
        "task-1",
        account_id=ttl_cleanup.SYSTEM_TASK_ACCOUNT_ID,
        user_id=ttl_cleanup.SYSTEM_TASK_USER_ID,
    )
    cleanup, viking_fs, registry, queue_manager = _make_service(
        record=record, live_content=_session_meta()
    )

    class Dispatcher:
        async def run(self, factory):
            return await factory()

    processor = ttl_cleanup._TTLCleanupProcessor.__new__(ttl_cleanup._TTLCleanupProcessor)
    processor._cleanup_service = cleanup
    processor._dispatcher = Dispatcher()

    result = await processor.on_cancelled(_message(record))

    assert result.outcome is ProcessOutcome.REQUEUED
    viking_fs.rm.assert_not_awaited()
    queue_manager.enqueue.assert_not_awaited()
    assert registry.defer_retry.await_args.kwargs["task_id"] != "task-1"


def test_task_id_changes_when_session_expiry_is_renewed():
    first = _record(expires_at=PAST)
    renewed = _record(expires_at=FUTURE)
    assert ttl_cleanup._cleanup_task_id(first) != ttl_cleanup._cleanup_task_id(renewed)
    assert ttl_cleanup._cleanup_task_id(first) == ttl_cleanup._cleanup_task_id(first)
    assert ttl_cleanup._cleanup_task_id(first, attempt=1) != ttl_cleanup._cleanup_task_id(first)


@pytest.mark.asyncio
async def test_scheduler_enqueues_only_claimed_due_work_with_original_retry_identity():
    due = _record()
    calls = []

    async def claim_due(**kwargs):
        calls.append(kwargs)
        yield due, {"task_id": "retry-1", "retry_count": 3}

    queue = SimpleNamespace(
        enqueue=AsyncMock(),
        get_status=AsyncMock(return_value=SimpleNamespace(pending=0, in_progress=0)),
    )
    service = SimpleNamespace(
        viking_fs=SimpleNamespace(ttl_registry=SimpleNamespace(claim_due=claim_due)),
        _queue_manager=SimpleNamespace(TTL_CLEANUP="ttl_cleanup", get_queue=lambda name: queue),
    )
    await ttl_cleanup.TTLCleanupScheduler(service)._scan_once()
    message = queue.enqueue.await_args.args[0]
    assert message["target"]["object_uri"] == SESSION_URI
    assert message["task_id"] == "retry-1" and message["retry_count"] == 3
    assert calls[0]["limit"] == 100 and calls[0]["time_budget"] > 0


@pytest.mark.asyncio
async def test_scheduler_pause_and_runtime_budgets_are_independent(monkeypatch):
    calls = []

    async def claim_due(**kwargs):
        calls.append(kwargs)
        if False:
            yield None

    queue = SimpleNamespace(enqueue=AsyncMock())
    service = SimpleNamespace(
        viking_fs=SimpleNamespace(ttl_registry=SimpleNamespace(claim_due=claim_due)),
        _queue_manager=SimpleNamespace(TTL_CLEANUP="ttl_cleanup", get_queue=lambda name: queue),
    )
    settings = SimpleNamespace(
        enabled=False,
        check_interval_seconds=60.0,
        scan_jitter_seconds=12.0,
        batch_size=17,
        max_batch_bytes=4096,
        scan_time_budget_seconds=2.5,
    )
    monkeypatch.setattr(ttl_cleanup, "_cleanup_settings", lambda: settings)
    scheduler = ttl_cleanup.TTLCleanupScheduler(service)

    await scheduler._scan_once()
    assert calls == []
    assert scheduler._next_interval() >= 60.0
    assert scheduler._next_interval() <= 72.0

    settings.enabled = True
    await scheduler._scan_once()
    assert calls == [
        {
            "now": calls[0]["now"],
            "limit": 17,
            "max_bytes": 4096,
            "time_budget": 2.5,
        }
    ]
    queue.enqueue.assert_not_awaited()


def test_cleanup_message_requires_generation_fence():
    parsed = ttl_cleanup._TTLCleanupProcessor._parse_message(
        {"data": json.dumps(_message(_record()))}
    )
    assert parsed["target"]["generation"] == GENERATION
    missing = _message(_record())
    missing["target"].pop("generation")
    with pytest.raises(ValueError, match="fence"):
        ttl_cleanup._TTLCleanupProcessor._parse_message(missing)


def test_due_boundary_is_inclusive():
    record = _record(expires_at="2026-09-22T00:00:00.000Z")
    assert ttl_cleanup.hidden_by_ttl(
        record.expires_at, now=datetime(2026, 9, 22, tzinfo=timezone.utc)
    )


async def _cleanup_once(cleanup, record):
    """Exercise the strict cleanup body under its required object lease."""
    ctx, lease = await cleanup._acquire_object_lock(record)
    try:
        return await cleanup._cleanup_record(record, ctx, lease)
    finally:
        await cleanup._service.viking_fs._async_agfs.pathlock_release(lease)
