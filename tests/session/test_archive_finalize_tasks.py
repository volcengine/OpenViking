# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

import openviking.session.archive_finalize_tasks as task_module
from openviking.server.identity import RequestContext, Role
from openviking.session.archive_finalize_tasks import (
    STATE_COMPLETED,
    STATE_PENDING,
    STATE_RETRY,
    STATE_TERMINAL_FAILED,
    ArchiveFinalizeTaskStore,
    archive_index_from_id,
)
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


def _create_pending(
    store: ArchiveFinalizeTaskStore,
    ctx: RequestContext,
    archive_id: str,
) -> None:
    store.create_preparing(
        ctx=ctx,
        session_id="session-1",
        archive_id=archive_id,
        archive_uri=f"viking://sessions/session-1/history/{archive_id}",
        task_tracker_id=f"task-{archive_id}",
        usage_records=[],
    )
    store.mark_pending(ctx, "session-1", archive_id)


def test_archive_finalize_tasks_run_serially_per_session(temp_dir):
    ctx = _ctx()
    store = ArchiveFinalizeTaskStore(str(temp_dir / "archive-finalize.db"))
    _create_pending(store, ctx, "archive_001")
    _create_pending(store, ctx, "archive_002")

    first = store.claim_next("worker-1")
    assert first is not None
    assert first.archive_id == "archive_001"

    assert store.claim_next("worker-2") is None

    store.release(first)
    first = store.claim_next("worker-2")
    assert first is not None
    assert first.archive_id == "archive_001"

    store.complete(first)
    second = store.claim_next("worker-2")
    assert second is not None
    assert second.archive_id == "archive_002"


def test_archive_finalize_tasks_order_by_numeric_archive_index(temp_dir):
    ctx = _ctx()
    store = ArchiveFinalizeTaskStore(str(temp_dir / "archive-finalize.db"))
    _create_pending(store, ctx, "archive_1000")
    _create_pending(store, ctx, "archive_999")

    first = store.claim_next("worker-1")
    assert first is not None
    assert first.archive_id == "archive_999"

    store.complete(first)
    second = store.claim_next("worker-1")
    assert second is not None
    assert second.archive_id == "archive_1000"


def test_archive_finalize_terminal_failure_can_be_reset_for_manual_retry(temp_dir, monkeypatch):
    monkeypatch.setattr(task_module, "ARCHIVE_FINALIZE_RETRY_DELAY_SECONDS", 0)

    ctx = _ctx()
    store = ArchiveFinalizeTaskStore(str(temp_dir / "archive-finalize.db"))
    _create_pending(store, ctx, "archive_001")

    task = store.claim_next("worker-1")
    assert task is not None
    assert store.fail(task, "provider timeout 1") == STATE_RETRY

    task = store.claim_next("worker-1")
    assert task is not None
    assert store.fail(task, "provider timeout 2") == STATE_RETRY

    task = store.claim_next("worker-1")
    assert task is not None
    assert store.fail(task, "provider timeout 3") == STATE_TERMINAL_FAILED

    blocking = store.get_blocking_failed(ctx, "session-1")
    assert blocking is not None
    assert blocking.state == STATE_TERMINAL_FAILED
    assert blocking.attempt_count == 3
    assert blocking.last_error == "provider timeout 3"

    reset = store.reset_for_retry(blocking, task_tracker_id="manual-retry-task")
    assert reset.state == STATE_PENDING
    assert reset.attempt_count == 0
    assert reset.last_error == ""
    assert store.get_blocking_failed(ctx, "session-1") is None

    store.complete(reset)
    assert store.get(ctx, "session-1", "archive_001").state == STATE_COMPLETED


def test_archive_finalize_task_store_rejects_invalid_archive_id(temp_dir):
    ctx = _ctx()
    store = ArchiveFinalizeTaskStore(str(temp_dir / "archive-finalize.db"))

    with pytest.raises(ValueError, match="Invalid archive ID"):
        archive_index_from_id("latest")

    with pytest.raises(ValueError, match="Invalid archive ID"):
        store.create_preparing(
            ctx=ctx,
            session_id="session-1",
            archive_id="latest",
            archive_uri="viking://sessions/session-1/history/latest",
            task_tracker_id="task-latest",
            usage_records=[],
        )
