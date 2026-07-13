# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real PostgreSQL concurrency/recovery tests for the fenced outbox writer."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
from typing import Any

import pytest
import pytest_asyncio

from openviking.server import fenced_operation
from openviking.server.fenced_operation import (
    FencedOperationConflict,
    FencedOperationEnvelope,
)
from openviking.server.fenced_postgres import (
    PostgresFencedOperationQueue,
    validate_postgres_fencing_schema,
)
from openviking.server.fenced_writer import (
    FencedCommitWorkItem,
    FencedOutboxItem,
    PermanentFencedEffectError,
    PostgresFencedOutboxWriter,
    PostgresFencedWriterPool,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.asyncio


class CommitEnvelope(FencedOperationEnvelope):
    keep_recent_count: int = 0
    wait: bool = False


def _dsn() -> str:
    value = os.getenv("OPENVIKING_ALICE_FENCING_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("PostgreSQL fencing integration DSN is not configured")
    return value


def _owner_dsn() -> str:
    value = os.getenv("OPENVIKING_ALICE_FENCING_TEST_OWNER_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("PostgreSQL fencing test owner DSN is not configured")
    return value


def _ctx(
    account_id: str = "writer-test-account",
    user_id: str = "writer-test-user",
) -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id, user_id),
        role=Role.USER,
    )


def _env(
    token: int,
    operation_id: str,
    *,
    scope: str = "scope-1",
    turn: str = "turn-1",
) -> FencedOperationEnvelope:
    return FencedOperationEnvelope(
        writer="alice",
        session_scope_id=scope,
        turn_id=turn,
        operation_id=operation_id,
        fencing_token=token,
    )


def _commit_env(
    token: int,
    operation_id: str,
    *,
    scope: str = "scope-1",
    turn: str = "turn-1",
    wait: bool,
) -> CommitEnvelope:
    return CommitEnvelope(
        writer="alice",
        session_scope_id=scope,
        turn_id=turn,
        operation_id=operation_id,
        fencing_token=token,
        wait=wait,
    )


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    import psycopg2

    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return list(cursor.fetchall())


def _owner_rows(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    import psycopg2

    with psycopg2.connect(_owner_dsn()) as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return list(cursor.fetchall())


async def _wait_for_rows(
    query: str,
    expected: list[tuple[Any, ...]],
    *,
    timeout: float = 2.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        rows = await asyncio.to_thread(_rows, query)
        if rows == expected:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"timed out waiting for rows: {rows!r}")
        await asyncio.sleep(0.02)


class _FakeTaskTracker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.fail_error: Exception | None = None

    async def fail(self, task_id: str, _error: str, **_kwargs: Any) -> None:
        if self.fail_error is not None:
            error, self.fail_error = self.fail_error, None
            raise error
        self.failures.append(task_id)


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakeTaskTracker:
    await validate_postgres_fencing_schema()
    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_writer_claimed",
        lambda _operation_id: None,
    )
    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_writer_effect_started",
        lambda _operation_id: None,
    )
    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_effect_before_receipt",
        lambda _operation: None,
    )
    tracker = _FakeTaskTracker()
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: tracker,
    )

    def _clean() -> None:
        import psycopg2

        with psycopg2.connect(_owner_dsn()) as conn, conn.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS openviking_fencing_test")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS openviking_fencing_test.test_effects (
                    operation_id text PRIMARY KEY,
                    value text NOT NULL
                )
                """
            )
            cursor.execute("TRUNCATE openviking_fencing_test.test_effects")
            for table in (
                "commit_work_outbox",
                "effect_outbox",
                "session_turn_closure",
                "effect_receipt",
                "operation_receipt",
                "session_binding",
                "scope_state",
            ):
                cursor.execute(f"TRUNCATE openviking_fencing.{table} CASCADE")

    await asyncio.to_thread(_clean)
    return tracker


def _test_effect(item: FencedOutboxItem, value: str) -> dict[str, Any]:
    import psycopg2

    with psycopg2.connect(_owner_dsn()) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO openviking_fencing_test.test_effects(operation_id,value)
            VALUES (%s,%s)
            ON CONFLICT (operation_id) DO UPDATE SET value=EXCLUDED.value
            """,
            (item.operation_id, value),
        )
    return {"operation_id": item.operation_id, "value": value}


async def test_writer_completes_effect_and_atomically_erases_outbox_payload() -> None:
    await PostgresFencedOperationQueue(_ctx(), _env(1, "op-1")).submit(
        "message",
        "session-1",
    )

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        return await asyncio.to_thread(_test_effect, item, "done")

    writer = PostgresFencedOutboxWriter(execute)
    assert await writer.run_once() is True

    receipt = await PostgresFencedOperationQueue.get(_ctx(), "op-1")
    assert receipt is not None
    assert receipt.state == "completed"
    assert receipt.result == {"operation_id": "op-1", "value": "done"}
    assert await asyncio.to_thread(
        _rows,
        "SELECT count(*) FROM openviking_fencing.effect_outbox",
    ) == [(0,)]
    assert await asyncio.to_thread(
        _rows,
        "SELECT writer,session_scope_id FROM openviking_fencing.session_binding",
    ) == [("alice", "scope-1")]


async def test_higher_fence_after_claim_but_before_effect_stales_without_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await PostgresFencedOperationQueue(_ctx(), _env(1, "old-op")).submit(
        "message",
        "session-1",
    )
    claimed = asyncio.Event()
    resume = asyncio.Event()
    effects: list[str] = []

    async def claimed_seam(_operation_id: str) -> None:
        claimed.set()
        await resume.wait()

    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_writer_claimed",
        claimed_seam,
    )

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        effects.append(item.operation_id)
        return {"operation_id": item.operation_id}

    writer = PostgresFencedOutboxWriter(execute)
    old_task = asyncio.create_task(writer.run_once())
    await claimed.wait()
    await PostgresFencedOperationQueue(
        _ctx(),
        _env(2, "new-op", turn="turn-2"),
    ).submit("message", "session-1")
    resume.set()
    assert await old_task is True
    assert effects == []
    old = await PostgresFencedOperationQueue.get(_ctx(), "old-op")
    assert old is not None and old.state == "stale"


async def test_higher_exact_replay_after_claim_uses_latest_token_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = "exact-replay-after-claim"
    await PostgresFencedOperationQueue(_ctx(), _env(1, operation_id)).submit("message", "session-1")
    claimed = asyncio.Event()
    resume = asyncio.Event()
    effects: list[tuple[str, int]] = []

    async def claimed_seam(claimed_operation_id: str) -> None:
        if claimed_operation_id == operation_id:
            claimed.set()
            await resume.wait()

    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_writer_claimed",
        claimed_seam,
    )

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        effects.append((item.operation_id, item.fencing_token))
        return {"operation_id": item.operation_id}

    writer_task = asyncio.create_task(PostgresFencedOutboxWriter(execute).run_once())
    try:
        await asyncio.wait_for(claimed.wait(), timeout=2)
        replay = await asyncio.wait_for(
            PostgresFencedOperationQueue(_ctx(), _env(2, operation_id)).submit(
                "message", "session-1"
            ),
            timeout=2,
        )
        assert replay.replayed is True
        assert replay.state == "running"
        assert replay.fencing_token == 2
    finally:
        resume.set()

    assert await asyncio.wait_for(writer_task, timeout=2) is True
    assert effects == [(operation_id, 2)]
    receipt = await PostgresFencedOperationQueue.get(_ctx(), operation_id)
    assert receipt is not None
    assert receipt.state == "completed"
    assert receipt.fencing_token == 2


async def test_completed_old_turn_replay_cannot_reclaim_newer_active_turn() -> None:
    old_operation = "completed-turn-a"
    new_operation = "queued-turn-b"
    effects: list[str] = []

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        effects.append(item.operation_id)
        return {"operation_id": item.operation_id}

    writer = PostgresFencedOutboxWriter(execute)
    await PostgresFencedOperationQueue(_ctx(), _env(5, old_operation, turn="turn-a")).submit(
        "message", "session-turn-replay"
    )
    assert await writer.run_once() is True
    await PostgresFencedOperationQueue(_ctx(), _env(6, new_operation, turn="turn-b")).submit(
        "message", "session-turn-replay"
    )

    # A completed response-loss replay at/below its persisted receipt token
    # remains readable, but does not adopt the newer turn's token.
    lower_replay = await PostgresFencedOperationQueue(
        _ctx(), _env(4, old_operation, turn="turn-a")
    ).submit("message", "session-turn-replay")
    assert lower_replay.replayed is True
    assert lower_replay.state == "completed"
    assert lower_replay.fencing_token == 5
    pure_replay = await PostgresFencedOperationQueue(
        _ctx(), _env(5, old_operation, turn="turn-a")
    ).submit("message", "session-turn-replay")
    assert pure_replay.replayed is True
    assert pure_replay.state == "completed"
    assert pure_replay.fencing_token == 5

    # W1(A,t10) must not leapfrog W2(B,t6) by reusing W1's completed receipt.
    with pytest.raises(FencedOperationConflict) as conflict:
        await PostgresFencedOperationQueue(_ctx(), _env(10, old_operation, turn="turn-a")).submit(
            "message", "session-turn-replay"
        )
    assert conflict.value.details["reason"] == "turn_fence_conflict"
    assert await asyncio.to_thread(
        _rows,
        "SELECT highest_fencing_token,active_turn_id FROM openviking_fencing.scope_state",
    ) == [(6, "turn-b")]
    assert await asyncio.to_thread(
        _rows,
        "SELECT operation_id,fencing_token FROM openviking_fencing.effect_outbox",
    ) == [(new_operation, 6)]

    assert await writer.run_once() is True
    assert effects == [old_operation, new_operation]


async def test_running_old_turn_replay_cannot_reclaim_newer_active_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_operation = "running-turn-a"
    new_operation = "running-turn-b"
    claimed = asyncio.Event()
    resume = asyncio.Event()
    effects: list[str] = []

    async def claimed_seam(operation_id: str) -> None:
        if operation_id == old_operation:
            claimed.set()
            await resume.wait()

    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_writer_claimed",
        claimed_seam,
    )

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        effects.append(item.operation_id)
        return {"operation_id": item.operation_id}

    await PostgresFencedOperationQueue(_ctx(), _env(5, old_operation, turn="turn-a")).submit(
        "message", "session-running-replay"
    )
    writer = PostgresFencedOutboxWriter(execute)
    old_task = asyncio.create_task(writer.run_once())
    try:
        await asyncio.wait_for(claimed.wait(), timeout=2)
        await PostgresFencedOperationQueue(_ctx(), _env(6, new_operation, turn="turn-b")).submit(
            "message", "session-running-replay"
        )
        with pytest.raises(FencedOperationConflict) as conflict:
            await PostgresFencedOperationQueue(
                _ctx(), _env(10, old_operation, turn="turn-a")
            ).submit("message", "session-running-replay")
        assert conflict.value.details["reason"] == "turn_fence_conflict"
        assert await asyncio.to_thread(
            _rows,
            "SELECT highest_fencing_token,active_turn_id FROM openviking_fencing.scope_state",
        ) == [(6, "turn-b")]
    finally:
        resume.set()

    assert await asyncio.wait_for(old_task, timeout=2) is True
    assert effects == []
    assert await writer.run_once() is True
    assert effects == [new_operation]


async def test_completed_replay_cannot_leapfrog_newer_same_turn_fence() -> None:
    old_operation = "completed-fence-five"
    new_operation = "queued-fence-six"
    effects: list[str] = []

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        effects.append(item.operation_id)
        return {"operation_id": item.operation_id}

    writer = PostgresFencedOutboxWriter(execute)
    await PostgresFencedOperationQueue(_ctx(), _env(5, old_operation, turn="same-turn")).submit(
        "message", "session-same-turn-fence"
    )
    assert await writer.run_once() is True
    await PostgresFencedOperationQueue(_ctx(), _env(6, new_operation, turn="same-turn")).submit(
        "message", "session-same-turn-fence"
    )

    with pytest.raises(FencedOperationConflict) as conflict:
        await PostgresFencedOperationQueue(_ctx(), _env(6, old_operation, turn="same-turn")).submit(
            "message", "session-same-turn-fence"
        )
    assert conflict.value.details["reason"] == "turn_fence_conflict"
    assert await asyncio.to_thread(
        _rows,
        "SELECT highest_fencing_token,active_turn_id FROM openviking_fencing.scope_state",
    ) == [(6, "same-turn")]
    assert await writer.run_once() is True
    assert effects == [old_operation, new_operation]


async def test_claim_and_higher_submit_cleanup_have_no_row_lock_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_id = "claim-cleanup-lock-order"
    await PostgresFencedOperationQueue(_ctx(), _env(1, operation_id)).submit(
        "message", "session-lock-order"
    )
    receipt_locked = threading.Event()
    release_claim = threading.Event()
    original_lock_receipt = PostgresFencedOutboxWriter._lock_operation_receipt
    gate_used = False

    def gated_lock_receipt(cursor, item):
        nonlocal gate_used
        locked = original_lock_receipt(cursor, item)
        if item.operation_id == operation_id and not gate_used:
            gate_used = True
            receipt_locked.set()
            if not release_claim.wait(timeout=3):
                raise TimeoutError("test did not release claim receipt gate")
        return locked

    monkeypatch.setattr(
        PostgresFencedOutboxWriter,
        "_lock_operation_receipt",
        staticmethod(gated_lock_receipt),
    )
    effects: list[str] = []

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        effects.append(item.operation_id)
        return {"operation_id": item.operation_id}

    writer = PostgresFencedOutboxWriter(execute)
    writer_task = asyncio.create_task(writer.run_once())
    higher_task: asyncio.Task[Any] | None = None
    try:
        assert await asyncio.to_thread(receipt_locked.wait, 2)
        higher_task = asyncio.create_task(
            PostgresFencedOperationQueue(
                _ctx(), _env(2, "claim-cleanup-higher", turn="turn-2")
            ).submit("message", "session-lock-order")
        )
        # Scope acceptance commits before eager stale-payload cleanup waits on
        # the claimed receipt.  This is the property that removes the former
        # scope/outbox -> receipt cycle.
        await _wait_for_rows(
            "SELECT highest_fencing_token FROM "
            "openviking_fencing.scope_state WHERE session_scope_id='scope-1'",
            [(2,)],
        )
    finally:
        release_claim.set()

    assert higher_task is not None
    writer_done, higher = await asyncio.wait_for(
        asyncio.gather(writer_task, higher_task), timeout=3
    )
    assert writer_done is True
    assert higher.state == "queued"
    assert effects == []
    old = await PostgresFencedOperationQueue.get(_ctx(), operation_id)
    assert old is not None and old.state == "stale"
    assert await writer.run_once() is True
    assert effects == ["claim-cleanup-higher"]


async def test_started_effect_is_deterministically_recovered_after_crash_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await PostgresFencedOperationQueue(_ctx(), _env(1, "recover-op")).submit(
        "message",
        "session-1",
    )

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        return await asyncio.to_thread(_test_effect, item, "stable")

    async def crash_after_effect(_operation: str) -> None:
        raise SystemExit(91)

    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_effect_before_receipt",
        crash_after_effect,
    )
    first_writer = PostgresFencedOutboxWriter(execute)
    with pytest.raises(SystemExit, match="91"):
        await first_writer.run_once()

    running = await asyncio.to_thread(
        _rows,
        "SELECT state,effect_started_at IS NOT NULL FROM openviking_fencing.effect_outbox",
    )
    assert running == [("running", True)]

    monkeypatch.setattr(
        fenced_operation,
        "after_fenced_effect_before_receipt",
        lambda _operation: None,
    )
    recovery_writer = PostgresFencedOutboxWriter(execute)
    assert await recovery_writer.run_once() is True
    receipt = await PostgresFencedOperationQueue.get(_ctx(), "recover-op")
    assert receipt is not None and receipt.state == "completed"
    # The external helper is operation-addressed, so physical replay leaves one
    # logical effect even though the first process lost its response receipt.
    assert await asyncio.to_thread(
        _owner_rows,
        "SELECT operation_id,value FROM openviking_fencing_test.test_effects",
    ) == [("recover-op", "stable")]


async def test_same_scope_is_ordered_while_different_scope_runs_in_parallel() -> None:
    await PostgresFencedOperationQueue(_ctx(), _env(1, "scope-a-1")).submit(
        "message",
        "session-a-1",
    )
    await PostgresFencedOperationQueue(_ctx(), _env(1, "scope-a-2")).submit(
        "message",
        "session-a-2",
    )
    await PostgresFencedOperationQueue(
        _ctx(),
        _env(1, "scope-b-1", scope="scope-2"),
    ).submit("message", "session-b-1")

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    effects: list[str] = []

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        if item.operation_id == "scope-a-1":
            first_started.set()
            await release_first.wait()
        effects.append(item.operation_id)
        return {"operation_id": item.operation_id}

    first_worker = PostgresFencedOutboxWriter(execute)
    second_worker = PostgresFencedOutboxWriter(execute)
    first_task = asyncio.create_task(first_worker.run_once())
    await first_started.wait()

    # The second worker skips scope-a-2 because scope-a-1 is still its earliest
    # active sequence, but it can complete independent scope-2 immediately.
    assert await second_worker.run_once() is True
    assert effects == ["scope-b-1"]
    release_first.set()
    assert await first_task is True
    assert await second_worker.run_once() is True
    assert effects == ["scope-b-1", "scope-a-1", "scope-a-2"]


async def test_waiting_commit_moves_phase_two_to_durable_work_outbox() -> None:
    commit = _commit_env(1, "commit-op", wait=True)
    await PostgresFencedOperationQueue(_ctx(), commit).submit(
        "commit",
        "session-a",
    )
    await PostgresFencedOperationQueue(
        _ctx(),
        _env(1, "other-op", scope="scope-2"),
    ).submit("message", "session-b")

    task_state = "pending"
    effects: list[str] = []

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        effects.append(item.operation_id)
        if item.operation == "commit":
            return {
                "task_id": "task-1",
                "archive_uri": "viking://session/archive-1",
                "archived": True,
            }
        return {"operation_id": item.operation_id}

    async def task_waiter(_item: FencedCommitWorkItem) -> str:
        return task_state

    writer = PostgresFencedOutboxWriter(execute, task_waiter=task_waiter)
    assert await writer.run_once() is True
    commit_receipt = await PostgresFencedOperationQueue.get(_ctx(), "commit-op")
    assert commit_receipt is not None and commit_receipt.state == "running"
    replay = await PostgresFencedOperationQueue(_ctx(), commit).submit(
        "commit",
        "session-a",
    )
    assert replay.state == "running"
    assert replay.replayed is True
    waiting = await asyncio.to_thread(
        _rows,
        "SELECT task_id,archive_uri,wait_for_completion,state "
        "FROM openviking_fencing.commit_work_outbox "
        "WHERE operation_id='commit-op'",
    )
    assert waiting == [("task-1", "viking://session/archive-1", True, "pending")]
    assert await asyncio.to_thread(
        _rows,
        "SELECT count(*) FROM openviking_fencing.effect_outbox WHERE operation_id='commit-op'",
    ) == [(0,)]
    assert await asyncio.to_thread(
        _rows,
        "SELECT operation_id FROM openviking_fencing.session_turn_closure "
        "WHERE operation_id='commit-op'",
    ) == [("commit-op",)]

    # The phase-one closure is authoritative while memory extraction is still
    # pending: the old session/turn cannot be reopened by a fresh operation.
    with pytest.raises(FencedOperationConflict) as closed_message:
        await PostgresFencedOperationQueue(
            _ctx(),
            _env(1, "closed-message", turn="turn-1"),
        ).submit("message", "session-a")
    assert closed_message.value.details["reason"] == "session_turn_closed"
    with pytest.raises(FencedOperationConflict) as closed_commit:
        await PostgresFencedOperationQueue(
            _ctx(),
            _commit_env(1, "closed-commit", turn="turn-1", wait=False),
        ).submit("commit", "session-a")
    assert closed_commit.value.details["reason"] == "session_turn_closed"

    # A new turn in the same writer scope is no longer blocked by phase two.
    await PostgresFencedOperationQueue(
        _ctx(),
        _env(2, "same-scope-create", turn="turn-2"),
    ).submit("create", "session-c")
    await PostgresFencedOperationQueue(
        _ctx(),
        _env(2, "same-scope-message", turn="turn-2"),
    ).submit("message", "session-c")

    # Independent and same-scope work continue while task-1 is pending.
    assert await writer.run_once() is True
    assert await writer.run_once() is True
    assert await writer.run_once() is True
    assert effects == [
        "commit-op",
        "other-op",
        "same-scope-create",
        "same-scope-message",
    ]

    task_state = "completed"
    assert await writer.process_waiting_once() is True
    done = await PostgresFencedOperationQueue.get(_ctx(), "commit-op")
    assert done is not None and done.state == "completed"
    assert done.result == {
        "task_id": "task-1",
        "archive_uri": "viking://session/archive-1",
        "archived": True,
    }


async def test_phase2_finish_and_exact_replay_share_receipt_first_lock_order() -> None:
    operation_id = "phase2-replay-lock-order"
    commit = _commit_env(1, operation_id, wait=True)
    await PostgresFencedOperationQueue(_ctx(), commit).submit("commit", "session-phase2-lock-order")

    async def execute(_item: FencedOutboxItem) -> dict[str, Any]:
        return {
            "task_id": "task-phase2-lock-order",
            "archive_uri": "viking://session/archive-phase2-lock-order",
        }

    waiter_started = asyncio.Event()
    allow_finish = asyncio.Event()

    async def task_waiter(_item: FencedCommitWorkItem) -> str:
        waiter_started.set()
        await allow_finish.wait()
        return "completed"

    writer = PostgresFencedOutboxWriter(execute, task_waiter=task_waiter)
    assert await writer.run_once() is True
    watcher_task = asyncio.create_task(writer.process_waiting_once())
    await asyncio.wait_for(waiter_started.wait(), timeout=2)

    def _block_commit_work():
        import psycopg2

        conn = psycopg2.connect(_dsn(), application_name="test-phase2-row-blocker")
        conn.autocommit = False
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT operation_id FROM "
                "openviking_fencing.commit_work_outbox "
                "WHERE operation_id=%s FOR UPDATE",
                (operation_id,),
            )
            assert cursor.fetchone() == (operation_id,)
        return conn

    blocker = await asyncio.to_thread(_block_commit_work)
    replay_task: asyncio.Task[Any] | None = None
    try:
        allow_finish.set()
        await _wait_for_rows(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name='openviking-fenced-commit-worker' "
            "AND wait_event_type='Lock'",
            [(1,)],
        )
        replay_task = asyncio.create_task(
            PostgresFencedOperationQueue(_ctx(), _commit_env(2, operation_id, wait=True)).submit(
                "commit", "session-phase2-lock-order"
            )
        )
        await _wait_for_rows(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name='openviking-fenced-submit' "
            "AND wait_event_type='Lock'",
            [(1,)],
        )
    finally:
        await asyncio.to_thread(blocker.rollback)
        await asyncio.to_thread(blocker.close)
        allow_finish.set()

    assert replay_task is not None
    watcher_done, replay = await asyncio.wait_for(
        asyncio.gather(watcher_task, replay_task), timeout=3
    )
    assert watcher_done is True
    assert replay.replayed is True
    assert replay.state == "completed"
    assert replay.fencing_token == 2
    assert await asyncio.to_thread(
        _rows,
        "SELECT count(*) FROM openviking_fencing.commit_work_outbox",
    ) == [(0,)]


async def test_ambiguous_commit_work_is_quarantined_without_hot_loop() -> None:
    commit = _commit_env(1, "ambiguous-work", wait=True)
    await PostgresFencedOperationQueue(_ctx(), commit).submit("commit", "session-ambiguous")

    async def execute(_item: FencedOutboxItem) -> dict[str, Any]:
        return {
            "task_id": "task-ambiguous",
            "archive_uri": "viking://session/archive-ambiguous",
        }

    calls = 0

    async def ambiguous(_item: FencedCommitWorkItem) -> str:
        nonlocal calls
        calls += 1
        raise FailedPreconditionError(
            "ambiguous",
            details={"reason": "commit_phase2_effect_ambiguous"},
        )

    writer = PostgresFencedOutboxWriter(execute, task_waiter=ambiguous)
    assert await writer.run_once() is True
    assert await writer.process_waiting_once() is True
    assert await writer.process_waiting_once() is False
    assert calls == 1
    assert await asyncio.to_thread(
        _rows,
        "SELECT state,attempt_count,claim_token IS NULL,error->'details'->>'reason' "
        "FROM openviking_fencing.commit_work_outbox "
        "WHERE operation_id='ambiguous-work'",
    ) == [("ambiguous", 1, True, "commit_phase2_effect_ambiguous")]
    receipt = await PostgresFencedOperationQueue.get(_ctx(), "ambiguous-work")
    assert receipt is not None and receipt.state == "failed"


async def test_quarantine_retries_until_task_tracker_is_terminal(
    clean_tables: _FakeTaskTracker,
) -> None:
    commit = _commit_env(1, "tracker-reconcile", wait=True)
    await PostgresFencedOperationQueue(_ctx(), commit).submit("commit", "session-tracker-reconcile")

    async def execute(_item: FencedOutboxItem) -> dict[str, Any]:
        return {
            "task_id": "task-tracker-reconcile",
            "archive_uri": "viking://session/archive-tracker-reconcile",
        }

    async def ambiguous(_item: FencedCommitWorkItem) -> str:
        raise FailedPreconditionError(
            "ambiguous",
            details={"reason": "commit_phase2_effect_ambiguous"},
        )

    clean_tables.fail_error = TimeoutError("task storage unavailable")
    writer = PostgresFencedOutboxWriter(execute, task_waiter=ambiguous)
    assert await writer.run_once() is True
    assert await writer.process_waiting_once() is True
    assert await asyncio.to_thread(
        _rows,
        "SELECT state FROM openviking_fencing.commit_work_outbox "
        "WHERE operation_id='tracker-reconcile'",
    ) == [("pending",)]

    await asyncio.sleep(0.12)
    assert await writer.process_waiting_once() is True
    assert clean_tables.failures == ["task-tracker-reconcile"]
    assert await asyncio.to_thread(
        _rows,
        "SELECT state FROM openviking_fencing.commit_work_outbox "
        "WHERE operation_id='tracker-reconcile'",
    ) == [("ambiguous",)]


async def test_commit_pool_runs_different_accounts_in_parallel() -> None:
    for index, scope in ((1, "parallel-a"), (2, "parallel-b")):
        ctx = _ctx(f"writer-test-account-{index}")
        await PostgresFencedOperationQueue(
            ctx,
            _commit_env(1, f"parallel-{index}", scope=scope, wait=False),
        ).submit("commit", f"session-{index}")

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        return {
            "task_id": f"task-{item.operation_id}",
            "archive_uri": f"viking://session/{item.operation_id}",
        }

    phase1 = PostgresFencedOutboxWriter(execute)
    assert await phase1.run_once() is True
    assert await phase1.run_once() is True

    both_started = asyncio.Event()
    release = asyncio.Event()
    started: set[str] = set()

    async def phase2(item: FencedCommitWorkItem) -> str:
        started.add(item.session_id)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return "completed"

    pool = PostgresFencedWriterPool(
        execute,
        task_waiter=phase2,
        concurrency=1,
        commit_concurrency=2,
        poll_seconds=0.05,
    )
    pool.start()
    await asyncio.wait_for(both_started.wait(), timeout=2)
    assert started == {"session-1", "session-2"}
    release.set()
    await pool.stop(drain_timeout_seconds=2)
    assert await asyncio.to_thread(
        _rows, "SELECT count(*) FROM openviking_fencing.commit_work_outbox"
    ) == [(0,)]


async def test_commit_pool_serializes_different_sessions_in_same_account() -> None:
    def _insert() -> None:
        import psycopg2

        with psycopg2.connect(_dsn()) as conn, conn.cursor() as cursor:
            for index in (1, 2):
                cursor.execute(
                    """
                    INSERT INTO openviking_fencing.operation_receipt
                        (account_id,user_id,writer,session_scope_id,operation_id,
                         operation,resource_id,turn_id,digest,fencing_token,state)
                    VALUES ('shared-account',%s,'alice',%s,%s,'commit',%s,
                            %s,%s,1,'completed')
                    """,
                    (
                        f"user-{index}",
                        f"shared-scope-{index}",
                        f"shared-operation-{index}",
                        f"shared-session-{index}",
                        f"shared-turn-{index}",
                        f"shared-digest-{index}",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO openviking_fencing.commit_work_outbox
                        (account_id,user_id,writer,session_scope_id,operation_id,
                         session_id,task_id,archive_uri,wait_for_completion)
                    VALUES ('shared-account',%s,'alice',%s,%s,%s,%s,%s,false)
                    """,
                    (
                        f"user-{index}",
                        f"shared-scope-{index}",
                        f"shared-operation-{index}",
                        f"shared-session-{index}",
                        f"shared-task-{index}",
                        f"viking://session/shared-{index}",
                    ),
                )

    await asyncio.to_thread(_insert)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    started: list[str] = []

    async def phase2(item: FencedCommitWorkItem) -> str:
        started.append(item.session_id)
        if len(started) == 1:
            first_started.set()
            await release_first.wait()
        return "completed"

    async def no_effect(_item: FencedOutboxItem) -> dict[str, Any]:
        return {}

    pool = PostgresFencedWriterPool(
        no_effect,
        task_waiter=phase2,
        concurrency=1,
        commit_concurrency=2,
        poll_seconds=0.05,
    )
    pool.start()
    await asyncio.wait_for(first_started.wait(), timeout=2)
    await asyncio.sleep(0.15)
    assert len(started) == 1
    release_first.set()
    deadline = asyncio.get_running_loop().time() + 2
    while len(started) != 2:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.02)
    await pool.stop(drain_timeout_seconds=2)


async def test_permanent_pre_effect_rejection_releases_scope_order() -> None:
    await PostgresFencedOperationQueue(_ctx(), _env(1, "permanent-1")).submit(
        "message", "session-permanent"
    )
    await PostgresFencedOperationQueue(_ctx(), _env(1, "permanent-2")).submit(
        "message", "session-permanent"
    )

    async def execute(item: FencedOutboxItem) -> dict[str, Any]:
        if item.operation_id == "permanent-1":
            raise PermanentFencedEffectError("session_not_found")
        return {"operation_id": item.operation_id}

    writer = PostgresFencedOutboxWriter(execute)
    assert await writer.run_once() is True
    rejected = await PostgresFencedOperationQueue.get(_ctx(), "permanent-1")
    assert rejected is not None and rejected.state == "failed"
    assert await writer.run_once() is True
    successor = await PostgresFencedOperationQueue.get(_ctx(), "permanent-2")
    assert successor is not None and successor.state == "completed"


async def test_commit_pool_preserves_same_session_order() -> None:
    def _insert() -> None:
        import psycopg2

        with psycopg2.connect(_dsn()) as conn, conn.cursor() as cursor:
            for index in (1, 2):
                cursor.execute(
                    """
                    INSERT INTO openviking_fencing.operation_receipt
                        (account_id,user_id,writer,session_scope_id,operation_id,
                         operation,resource_id,turn_id,digest,fencing_token,state)
                    VALUES (%s,%s,'alice',%s,%s,'commit','same-session',%s,%s,
                            1,'completed')
                    """,
                    (
                        "writer-test-account",
                        "writer-test-user",
                        f"ordered-{index}",
                        f"ordered-{index}",
                        f"ordered-turn-{index}",
                        f"ordered-digest-{index}",
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO openviking_fencing.commit_work_outbox
                        (account_id,user_id,writer,session_scope_id,operation_id,
                         session_id,task_id,archive_uri,wait_for_completion)
                    VALUES (%s,%s,'alice',%s,%s,'same-session',%s,%s,false)
                    """,
                    (
                        "writer-test-account",
                        "writer-test-user",
                        f"ordered-{index}",
                        f"ordered-{index}",
                        f"ordered-task-{index}",
                        f"viking://session/ordered-{index}",
                    ),
                )

    await asyncio.to_thread(_insert)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def phase2(item: FencedCommitWorkItem) -> str:
        order.append(item.operation_id)
        if item.operation_id == "ordered-1":
            first_started.set()
            await release_first.wait()
        return "completed"

    async def no_effect(_item: FencedOutboxItem) -> dict[str, Any]:
        return {}

    pool = PostgresFencedWriterPool(
        no_effect,
        task_waiter=phase2,
        concurrency=1,
        commit_concurrency=2,
        poll_seconds=0.05,
    )
    pool.start()
    await asyncio.wait_for(first_started.wait(), timeout=2)
    await asyncio.sleep(0.15)
    assert order == ["ordered-1"]
    release_first.set()
    deadline = asyncio.get_running_loop().time() + 2
    while order != ["ordered-1", "ordered-2"]:
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.02)
    await pool.stop(drain_timeout_seconds=2)


async def test_empty_pool_uses_bounded_idle_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def empty_effect(_self: PostgresFencedOutboxWriter) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    async def empty_commit(_self: PostgresFencedOutboxWriter) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    monkeypatch.setattr(PostgresFencedOutboxWriter, "run_once", empty_effect)
    monkeypatch.setattr(
        PostgresFencedOutboxWriter,
        "process_waiting_once",
        empty_commit,
    )

    async def no_effect(_item: FencedOutboxItem) -> dict[str, Any]:
        return {}

    async def no_commit(_item: FencedCommitWorkItem) -> str:
        return "completed"

    pool = PostgresFencedWriterPool(
        no_effect,
        task_waiter=no_commit,
        concurrency=2,
        commit_concurrency=2,
        poll_seconds=0.25,
        max_idle_seconds=1.0,
    )
    pool.start()
    await asyncio.sleep(1.05)
    await pool.stop(drain_timeout_seconds=2)
    assert attempts <= 16


async def test_idle_pool_stop_does_not_wait_full_configured_drain() -> None:
    async def no_effect(_item: FencedOutboxItem) -> dict[str, Any]:
        return {}

    pool = PostgresFencedWriterPool(
        no_effect,
        concurrency=1,
        poll_seconds=0.05,
        max_idle_seconds=0.1,
        drain_timeout_seconds=1860.0,
    )
    pool.start()
    await asyncio.sleep(0.06)
    await asyncio.wait_for(pool.stop(), timeout=1)
    assert pool.healthy is False


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="requires POSIX signals")
async def test_sigstop_writer_holds_authority_until_process_death() -> None:
    await PostgresFencedOperationQueue(
        _ctx(),
        _env(1, "frozen-old", turn="turn-old"),
    ).submit("message", "frozen-session")
    child = """
import asyncio
import os
import signal
from openviking.server import fenced_operation
from openviking.server.fenced_writer import PostgresFencedOutboxWriter

async def freeze(operation_id):
    print('EFFECT_STARTED:' + operation_id, flush=True)
    os.kill(os.getpid(), signal.SIGSTOP)

async def effect(item):
    print('UNEXPECTED_EFFECT:' + item.operation_id, flush=True)
    return {'operation_id': item.operation_id}

fenced_operation.after_fenced_writer_effect_started = freeze
asyncio.run(PostgresFencedOutboxWriter(effect).run_once())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        child,
        cwd=os.getcwd(),
        env=dict(os.environ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        assert process.stdout is not None
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            assert remaining > 0, "child did not reach durable effect_started"
            line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            assert line, "child exited before SIGSTOP seam"
            if line.decode().strip() == "EFFECT_STARTED:frozen-old":
                break

        await PostgresFencedOperationQueue(
            _ctx(),
            _env(2, "higher-new", turn="turn-new"),
        ).submit("message", "frozen-session")

        effects: list[str] = []

        async def execute(item: FencedOutboxItem) -> dict[str, Any]:
            effects.append(item.operation_id)
            return {"operation_id": item.operation_id}

        replacement = PostgresFencedOutboxWriter(execute)
        assert await replacement.run_once() is False
        assert effects == []

        os.kill(process.pid, signal.SIGKILL)
        await asyncio.wait_for(process.wait(), timeout=5)
        assert await replacement.run_once() is True
        assert await replacement.run_once() is True
        assert effects == ["frozen-old", "higher-new"]
    finally:
        if process.returncode is None:
            os.kill(process.pid, signal.SIGKILL)
            await process.wait()
