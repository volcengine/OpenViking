# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real PostgreSQL tests for the Alice fenced-operation durable outbox."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import textwrap
import time
from typing import Any, Optional

import pytest
import pytest_asyncio
from pydantic import Field

from openviking.server.fenced_operation import (
    FencedOperationConflict,
    FencedOperationEnvelope,
)
from openviking.server.fenced_postgres import (
    POSTGRES_FENCING_DDL,
    PostgresFencedOperationQueue,
    validate_postgres_fencing_schema,
)
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.asyncio


class MessageEnvelope(FencedOperationEnvelope):
    role: str = "user"
    content: str = Field(min_length=1)
    created_at: Optional[str] = None


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


def _ctx(*, actor_peer_id: Optional[str] = None) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("fencing-test-account", "fencing-test-user"),
        role=Role.USER,
        actor_peer_id=actor_peer_id,
    )


def _envelope(
    token: int,
    operation_id: str,
    *,
    turn_id: str = "turn-1",
    scope: str = "scope-1",
    content: str = "hello",
) -> MessageEnvelope:
    return MessageEnvelope(
        writer="alice",
        session_scope_id=scope,
        turn_id=turn_id,
        operation_id=operation_id,
        fencing_token=token,
        content=content,
    )


def _db_rows(query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    import psycopg2

    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cursor:
        cursor.execute(query, params)
        return list(cursor.fetchall())


@pytest_asyncio.fixture(autouse=True)
async def clean_fencing_tables() -> None:
    await validate_postgres_fencing_schema()

    def _truncate() -> None:
        import psycopg2

        with psycopg2.connect(_owner_dsn()) as conn, conn.cursor() as cursor:
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

    await asyncio.to_thread(_truncate)


async def test_submit_is_durable_and_higher_exact_retry_reuses_one_outbox_row() -> None:
    first_envelope = _envelope(10, "same-op")
    first = await PostgresFencedOperationQueue(_ctx(), first_envelope).submit(
        "message",
        "session-1",
    )
    await asyncio.sleep(0.02)
    replay = await PostgresFencedOperationQueue(
        _ctx(),
        _envelope(11, "same-op"),
    ).submit("message", "session-1")

    assert first.state == "queued"
    assert first.replayed is False
    assert replay.state == "queued"
    assert replay.replayed is True
    assert replay.fencing_token == 11

    rows = await asyncio.to_thread(
        _db_rows,
        """
        SELECT o.operation_id, o.fencing_token, o.request_payload->>'created_at',
               r.submitted_at, r.fencing_token
        FROM openviking_fencing.effect_outbox o
        JOIN openviking_fencing.operation_receipt r
          USING (account_id,user_id,writer,session_scope_id,operation_id)
        """,
    )
    assert len(rows) == 1
    assert rows[0][0:3] == ("same-op", 11, None)
    # submitted_at is the stable effective created_at used by the writer when
    # the validated request leaves created_at unset.  A retry never replaces it.
    assert rows[0][3] is not None
    assert rows[0][4] == 11


async def test_actor_peer_is_bound_into_digest_and_replay_cannot_replace_it() -> None:
    await PostgresFencedOperationQueue(
        _ctx(actor_peer_id="peer-a"),
        _envelope(1, "peer-op"),
    ).submit("message", "session-1")

    with pytest.raises(FencedOperationConflict) as conflict:
        await PostgresFencedOperationQueue(
            _ctx(actor_peer_id="peer-b"),
            _envelope(2, "peer-op"),
        ).submit("message", "session-1")
    assert conflict.value.details["reason"] == "operation_digest_conflict"

    rows = await asyncio.to_thread(
        _db_rows,
        "SELECT actor_peer_id, fencing_token FROM openviking_fencing.effect_outbox",
    )
    assert rows == [("peer-a", 1)]


async def test_digest_and_principal_operation_scope_conflicts_fail_closed() -> None:
    await PostgresFencedOperationQueue(_ctx(), _envelope(1, "fixed-op")).submit(
        "message",
        "session-1",
    )

    with pytest.raises(FencedOperationConflict) as digest_conflict:
        await PostgresFencedOperationQueue(
            _ctx(),
            _envelope(2, "fixed-op", content="different"),
        ).submit("message", "session-1")
    assert digest_conflict.value.details["reason"] == "operation_digest_conflict"

    with pytest.raises(FencedOperationConflict) as scope_conflict:
        await PostgresFencedOperationQueue(
            _ctx(),
            _envelope(2, "fixed-op", scope="scope-2"),
        ).submit("message", "session-1")
    assert scope_conflict.value.details["reason"] == "operation_scope_conflict"


async def test_higher_new_turn_stales_only_queued_and_erases_its_payload() -> None:
    await PostgresFencedOperationQueue(_ctx(), _envelope(1, "old-op")).submit(
        "message",
        "session-1",
    )
    current = await PostgresFencedOperationQueue(
        _ctx(),
        _envelope(2, "new-op", turn_id="turn-2"),
    ).submit("message", "session-1")
    assert current.state == "queued"

    stale = await PostgresFencedOperationQueue.get(_ctx(), "old-op")
    assert stale is not None
    assert stale.state == "stale"
    assert stale.error is not None
    assert stale.error["details"]["reason"] == "stale_fence"

    outbox_rows = await asyncio.to_thread(
        _db_rows,
        "SELECT operation_id, request_payload->>'content' "
        "FROM openviking_fencing.effect_outbox ORDER BY sequence_id",
    )
    assert outbox_rows == [("new-op", "hello")]

    # A terminal stale result is immutable.  A higher token cannot silently
    # revive the old operation or advance the scope; retry needs a new op ID.
    replay = await PostgresFencedOperationQueue(
        _ctx(),
        _envelope(3, "old-op"),
    ).submit("message", "session-1")
    assert replay.state == "stale"
    scope_rows = await asyncio.to_thread(
        _db_rows,
        "SELECT highest_fencing_token, active_turn_id FROM openviking_fencing.scope_state",
    )
    assert scope_rows == [(2, "turn-2")]


async def test_eager_stale_cleanup_records_suppression_after_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openviking.metrics.datasources.session import SessionLifecycleDataSource

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        SessionLifecycleDataSource,
        "record_fenced_effect",
        lambda **payload: events.append(payload),
    )
    await PostgresFencedOperationQueue(_ctx(), _envelope(1, "metric-old")).submit(
        "message", "session-metric"
    )
    events.clear()

    await PostgresFencedOperationQueue(
        _ctx(), _envelope(2, "metric-new", turn_id="turn-new")
    ).submit("message", "session-metric")

    assert events == [{"operation": "message", "outcome": "suppressed"}]
    stale = await PostgresFencedOperationQueue.get(_ctx(), "metric-old")
    assert stale is not None and stale.state == "stale"


async def test_v2_submit_metric_classifies_stale_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openviking.metrics.datasources.session import SessionLifecycleDataSource

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        SessionLifecycleDataSource,
        "record_fencing",
        lambda **payload: events.append(payload),
    )
    await PostgresFencedOperationQueue(
        _ctx(),
        _envelope(2, "metric-high", turn_id="turn-high"),
    ).submit("message", "session-metric")
    events.clear()

    with pytest.raises(FencedOperationConflict) as stale:
        await PostgresFencedOperationQueue(
            _ctx(),
            _envelope(1, "metric-low", turn_id="turn-low"),
        ).submit("message", "session-metric")
    assert stale.value.details["reason"] == "stale_fence"
    assert len(events) == 1
    assert events[0]["operation"] == "message"
    assert events[0]["outcome"] == "stale"


async def test_completed_response_loss_replay_wins_over_later_commit_closure() -> None:
    envelope = _envelope(1, "message-op")
    queued = await PostgresFencedOperationQueue(_ctx(), envelope).submit(
        "message",
        "session-1",
    )

    def _complete_then_close() -> None:
        import psycopg2
        from psycopg2.extras import Json

        result = {"message_id": "stable-message"}
        with psycopg2.connect(_owner_dsn()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE openviking_fencing.operation_receipt
                SET state='completed', result=%s, updated_at=now()
                WHERE operation_id='message-op'
                """,
                (Json(result),),
            )
            cursor.execute(
                """
                INSERT INTO openviking_fencing.effect_receipt
                    (account_id,user_id,writer,session_scope_id,operation_id,
                     operation,resource_id,turn_id,digest,fencing_token,result)
                SELECT account_id,user_id,writer,session_scope_id,operation_id,
                       operation,resource_id,turn_id,digest,fencing_token,%s
                FROM openviking_fencing.operation_receipt
                WHERE operation_id='message-op'
                """,
                (Json(result),),
            )
            cursor.execute(
                "DELETE FROM openviking_fencing.effect_outbox WHERE operation_id='message-op'"
            )
            cursor.execute(
                """
                INSERT INTO openviking_fencing.session_turn_closure
                    (account_id,user_id,writer,session_scope_id,turn_id,session_id,
                     operation_id,digest,fencing_token,result)
                VALUES ('fencing-test-account','fencing-test-user','alice','scope-1',
                        'turn-1','session-1','commit-op','commit-digest',2,%s)
                """,
                (Json({"archived": True}),),
            )

    await asyncio.to_thread(_complete_then_close)

    replay = await PostgresFencedOperationQueue(
        _ctx(),
        _envelope(2, "message-op"),
    ).submit("message", "session-1")
    assert replay.state == "completed"
    assert replay.result == {"message_id": "stable-message"}
    assert replay.replayed is True
    assert replay.digest == queued.digest

    fences = await asyncio.to_thread(
        _db_rows,
        "SELECT r.fencing_token, e.fencing_token "
        "FROM openviking_fencing.operation_receipt r "
        "JOIN openviking_fencing.effect_receipt e USING "
        "(account_id,user_id,writer,session_scope_id,operation_id)",
    )
    assert fences == [(2, 2)]


_CHILD_COMMON = """
import asyncio
import sys
from openviking.server import fenced_operation
from openviking.server.fenced_operation import FencedOperationConflict
from openviking.server.fenced_operation import FencedOperationEnvelope
from openviking.server.fenced_postgres import PostgresFencedOperationQueue
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier

ctx = RequestContext(UserIdentifier('fencing-test-account', 'fencing-test-user'), Role.USER)
env = FencedOperationEnvelope(
    writer='alice', session_scope_id='scope-1', turn_id='turn-1',
    operation_id='frozen-op', fencing_token=1,
)
"""


async def _start_frozen_child(source: str) -> asyncio.subprocess.Process:
    env = os.environ.copy()
    env["OPENVIKING_ALICE_FENCING_DATABASE_URL"] = _dsn()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        source,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert process.stdout is not None
    ready = await asyncio.wait_for(process.stdout.readline(), timeout=10)
    assert ready.strip() == b"READY"
    os.kill(process.pid, signal.SIGSTOP)
    return process


async def _resume_child(process: asyncio.subprocess.Process) -> tuple[str, str]:
    assert process.stdin is not None
    process.stdin.write(b"continue\n")
    await process.stdin.drain()
    os.kill(process.pid, signal.SIGCONT)
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    return stdout.decode(), stderr.decode()


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="requires POSIX SIGSTOP")
async def test_sigstop_before_submit_allows_bounded_higher_takeover() -> None:
    child_source = _CHILD_COMMON + textwrap.dedent(
        """
        async def seam(_operation):
            print('READY', flush=True)
            await asyncio.to_thread(sys.stdin.readline)
        fenced_operation.after_fenced_submit_preflight = seam

        async def main():
            try:
                await PostgresFencedOperationQueue(ctx, env).submit('message', 'session-1')
            except FencedOperationConflict as exc:
                print('STALE ' + exc.details['reason'], flush=True)
        asyncio.run(main())
        """
    )
    child = await _start_frozen_child(child_source)
    started = time.monotonic()
    try:
        higher = await PostgresFencedOperationQueue(
            _ctx(),
            _envelope(2, "higher-op", turn_id="turn-2"),
        ).submit("message", "session-1")
        assert time.monotonic() - started < 1.0
        assert higher.state == "queued"
        stdout, stderr = await _resume_child(child)
        assert "STALE stale_fence" in stdout
        assert stderr == ""
    finally:
        if child.returncode is None:
            os.kill(child.pid, signal.SIGCONT)
            child.kill()
            await child.wait()

    rows = await asyncio.to_thread(
        _db_rows,
        "SELECT operation_id FROM openviking_fencing.effect_outbox",
    )
    assert rows == [("higher-op",)]


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="requires POSIX SIGSTOP")
async def test_sigstop_inside_submit_releases_transaction_lock_by_db_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENVIKING_ALICE_FENCING_SUBMIT_IDLE_TIMEOUT_MS", "700")
    child_source = _CHILD_COMMON + textwrap.dedent(
        """
        def seam(_operation):
            print('READY', flush=True)
            sys.stdin.readline()
        fenced_operation.after_fenced_submit_locks_acquired = seam

        async def main():
            try:
                await PostgresFencedOperationQueue(ctx, env).submit('message', 'session-1')
            except Exception as exc:
                print(type(exc).__name__, flush=True)
        asyncio.run(main())
        """
    )
    child = await _start_frozen_child(child_source)
    started = time.monotonic()
    try:
        higher = await PostgresFencedOperationQueue(
            _ctx(),
            _envelope(2, "higher-op", turn_id="turn-2"),
        ).submit("message", "session-1")
        elapsed = time.monotonic() - started
        assert 0.5 <= elapsed < 3.0
        assert higher.state == "queued"
        stdout, _stderr = await _resume_child(child)
        assert "UnavailableError" in stdout
    finally:
        if child.returncode is None:
            os.kill(child.pid, signal.SIGCONT)
            child.kill()
            await child.wait()

    rows = await asyncio.to_thread(
        _db_rows,
        "SELECT operation_id FROM openviking_fencing.effect_outbox",
    )
    assert rows == [("higher-op",)]


async def test_startup_schema_validation_rejects_half_migration() -> None:
    import psycopg2

    def _drop_critical_column() -> None:
        with psycopg2.connect(_owner_dsn()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE openviking_fencing.effect_outbox DROP COLUMN effect_started_at"
            )

    def _restore() -> None:
        with psycopg2.connect(_owner_dsn()) as conn, conn.cursor() as cursor:
            cursor.execute(POSTGRES_FENCING_DDL)

    await asyncio.to_thread(_drop_critical_column)
    try:
        with pytest.raises(RuntimeError, match="missing columns effect_started_at"):
            await validate_postgres_fencing_schema()
    finally:
        await asyncio.to_thread(_restore)
    await validate_postgres_fencing_schema()


async def test_startup_schema_validation_rejects_real_owner_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OPENVIKING_ALICE_FENCING_DATABASE_URL",
        _owner_dsn(),
    )

    with pytest.raises(RuntimeError, match="authenticate and connect as openviking_fencing"):
        await validate_postgres_fencing_schema()
