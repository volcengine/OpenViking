# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable, scope-ordered writer for Alice fenced-session outbox effects."""

from __future__ import annotations

import asyncio
import inspect
import os
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional

from openviking.server.fenced_postgres import (
    SCHEMA,
    _advisory_key,
    _connect,
    _json_value,
)
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

EffectExecutor = Callable[["FencedOutboxItem"], Awaitable[dict[str, Any]]]
TaskWaiter = Callable[["FencedCommitWorkItem"], Awaitable[str]]
FailStop = Callable[[BaseException], None]


class PermanentFencedEffectError(Exception):
    """A validated rejection that is proven to precede the external effect."""

    def __init__(
        self,
        reason: str,
        *,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


@dataclass(frozen=True)
class FencedOutboxItem:
    sequence_id: int
    account_id: str
    user_id: str
    writer: str
    session_scope_id: str
    operation_id: str
    operation: str
    resource_id: str
    turn_id: str
    digest: str
    fencing_token: int
    request_payload: dict[str, Any]
    actor_peer_id: Optional[str]
    state: str
    attempt_count: int
    claim_token: Optional[str]
    effect_started_at: Optional[datetime]
    submitted_at: datetime

    @property
    def scope_key(self) -> tuple[str, str, str, str]:
        return (
            self.account_id,
            self.user_id,
            self.writer,
            self.session_scope_id,
        )

    @property
    def receipt_key(self) -> tuple[str, str, str, str, str]:
        return (*self.scope_key, self.operation_id)


@dataclass(frozen=True)
class FencedCommitWorkItem:
    sequence_id: int
    account_id: str
    user_id: str
    writer: str
    session_scope_id: str
    operation_id: str
    session_id: str
    task_id: str
    archive_uri: str
    wait_for_completion: bool
    state: str
    attempt_count: int
    claim_token: Optional[str]

    @property
    def receipt_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.account_id,
            self.user_id,
            self.writer,
            self.session_scope_id,
            self.operation_id,
        )


@dataclass
class _ClaimHandle:
    conn: Any
    item: FencedOutboxItem
    lock_keys: tuple[int, ...]


@dataclass
class _CommitClaimHandle:
    conn: Any
    item: FencedCommitWorkItem
    lock_keys: tuple[int, ...]


@dataclass(frozen=True)
class _ClaimOutcome:
    handle: Optional[_ClaimHandle]
    processed: bool


_OUTBOX_SELECT = f"""
    SELECT o.sequence_id, o.account_id, o.user_id, o.writer,
           o.session_scope_id, o.operation_id, o.operation, o.resource_id,
           o.turn_id, o.digest, o.fencing_token, o.request_payload,
           o.actor_peer_id, o.state, o.attempt_count, o.claim_token,
           o.effect_started_at, r.submitted_at
    FROM {SCHEMA}.effect_outbox o
    JOIN {SCHEMA}.operation_receipt r
      USING (account_id,user_id,writer,session_scope_id,operation_id)
"""

_COMMIT_WORK_SELECT = f"""
    SELECT sequence_id,account_id,user_id,writer,session_scope_id,operation_id,
           session_id,task_id,archive_uri,wait_for_completion,state,
           attempt_count,claim_token
    FROM {SCHEMA}.commit_work_outbox
"""


def _outbox_item(row: Any) -> FencedOutboxItem:
    if row is None or len(row) != 18:
        raise RuntimeError("Corrupt PostgreSQL fenced outbox row")
    payload = _json_value(row[11])
    submitted_at = row[17]
    if not isinstance(submitted_at, datetime):
        raise RuntimeError("Corrupt PostgreSQL fenced outbox submitted_at")
    item = FencedOutboxItem(
        sequence_id=int(row[0]),
        account_id=str(row[1]),
        user_id=str(row[2]),
        writer=str(row[3]),
        session_scope_id=str(row[4]),
        operation_id=str(row[5]),
        operation=str(row[6]),
        resource_id=str(row[7]),
        turn_id=str(row[8]),
        digest=str(row[9]),
        fencing_token=int(row[10]),
        request_payload=payload,
        actor_peer_id=None if row[12] is None else str(row[12]),
        state=str(row[13]),
        attempt_count=int(row[14]),
        claim_token=None if row[15] is None else str(row[15]),
        effect_started_at=row[16],
        submitted_at=submitted_at,
    )
    if item.writer != "alice":
        raise RuntimeError("Fenced outbox contains a non-Alice writer")
    return item


def _commit_work_item(row: Any) -> FencedCommitWorkItem:
    if row is None or len(row) != 13:
        raise RuntimeError("Corrupt PostgreSQL fenced commit work row")
    item = FencedCommitWorkItem(
        sequence_id=int(row[0]),
        account_id=str(row[1]),
        user_id=str(row[2]),
        writer=str(row[3]),
        session_scope_id=str(row[4]),
        operation_id=str(row[5]),
        session_id=str(row[6]),
        task_id=str(row[7]),
        archive_uri=str(row[8]),
        wait_for_completion=bool(row[9]),
        state=str(row[10]),
        attempt_count=int(row[11]),
        claim_token=None if row[12] is None else str(row[12]),
    )
    if item.writer != "alice":
        raise RuntimeError("Fenced commit work contains a non-Alice writer")
    return item


def _sanitized_error(
    *,
    code: str,
    message: str,
    reason: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    safe_details: dict[str, Any] = {"reason": reason}
    if details:
        for key in (
            "highest_fencing_token",
            "received_fencing_token",
            "active_turn_id",
            "operation_id",
            "session_id",
            "turn_id",
        ):
            value = details.get(key)
            if isinstance(value, (str, int, float, bool)):
                safe_details[key] = value
    return {"code": code, "message": message, "details": safe_details}


def _stale_error(item: FencedOutboxItem, highest: int) -> dict[str, Any]:
    return _sanitized_error(
        code="CONFLICT",
        message="Fencing token is stale",
        reason="stale_fence",
        details={
            "highest_fencing_token": highest,
            "received_fencing_token": item.fencing_token,
        },
    )


def _effect_failed_error() -> dict[str, Any]:
    return _sanitized_error(
        code="UNAVAILABLE",
        message="Fenced session effect failed",
        reason="effect_failed",
    )


def _default_fail_stop(exc: BaseException) -> None:
    logger.critical(
        "Fenced writer lost its PostgreSQL session lock connection; exiting",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    os._exit(70)


def _record_effect_metric(item: FencedOutboxItem, outcome: str) -> None:
    try:
        from openviking.metrics.datasources.session import (  # noqa: PLC0415
            SessionLifecycleDataSource,
        )

        SessionLifecycleDataSource.record_fenced_effect(
            operation=item.operation,
            outcome=outcome,
        )
    except Exception:
        logger.debug("Failed to record fenced effect metric", exc_info=True)


class PostgresFencedOutboxWriter:
    """One worker coroutine; scope/session advisory locks are the authority.

    Multiple instances may run concurrently.  Each instance claims only the
    earliest active sequence in a writer scope, and holds non-expiring session
    advisory locks for both scope and session until the deterministic effect is
    completed or durably deferred to a task watcher.
    """

    def __init__(
        self,
        executor: EffectExecutor,
        *,
        task_waiter: Optional[TaskWaiter] = None,
        max_attempts: int = 10,
        retry_delay_seconds: float = 0.1,
        monitor_interval_seconds: float = 0.25,
        fail_stop: FailStop = _default_fail_stop,
    ) -> None:
        self._executor = executor
        self._task_waiter = task_waiter
        self._max_attempts = max(1, int(max_attempts))
        self._retry_delay_seconds = max(0.01, float(retry_delay_seconds))
        self._monitor_interval_seconds = max(
            0.05, float(monitor_interval_seconds)
        )
        self._fail_stop = fail_stop

    @staticmethod
    def _lock_keys(item: FencedOutboxItem) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    _advisory_key("outbox-scope", *item.scope_key),
                    _advisory_key(
                        "outbox-session",
                        item.account_id,
                        item.user_id,
                        item.resource_id,
                    ),
                }
            )
        )

    @staticmethod
    def _try_session_locks(cursor, lock_keys: tuple[int, ...]) -> bool:
        acquired: list[int] = []
        for key in lock_keys:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            row = cursor.fetchone()
            if row and row[0] is True:
                acquired.append(key)
                continue
            for acquired_key in reversed(acquired):
                cursor.execute("SELECT pg_advisory_unlock(%s)", (acquired_key,))
            return False
        return True

    @staticmethod
    def _release_session_locks(cursor, lock_keys: tuple[int, ...]) -> None:
        for key in reversed(lock_keys):
            cursor.execute("SELECT pg_advisory_unlock(%s)", (key,))

    @staticmethod
    def _lock_operation_receipt(
        cursor,
        item: FencedOutboxItem,
    ) -> Optional[FencedOutboxItem]:
        cursor.execute(
            f"""
            SELECT operation,resource_id,turn_id,digest,fencing_token,state
            FROM {SCHEMA}.operation_receipt
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s
            FOR UPDATE
            """,
            item.receipt_key,
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if (
            str(row[0]) != item.operation
            or str(row[1]) != item.resource_id
            or str(row[2]) != item.turn_id
            or str(row[3]) != item.digest
            or str(row[5]) not in {"queued", "running"}
        ):
            return None
        # An exact replay may raise the replaceable fencing token after claim
        # commits but before effect authorization.  The receipt is the first
        # row in the global lock order, so its locked token is the only safe
        # value to use when subsequently checking scope_state.  The outbox row
        # is locked last and must agree before the effect-start boundary.
        return replace(item, fencing_token=int(row[4]))

    @staticmethod
    def _lock_commit_receipt(
        cursor,
        item: FencedCommitWorkItem,
    ) -> str:
        cursor.execute(
            f"""
            SELECT state
            FROM {SCHEMA}.operation_receipt
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s
            FOR UPDATE
            """,
            item.receipt_key,
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL commit operation receipt is missing")
        return str(row[0])

    @staticmethod
    def _terminalize(
        cursor,
        item: FencedOutboxItem,
        *,
        state: str,
        error: Optional[dict[str, Any]],
        result: Optional[dict[str, Any]] = None,
    ) -> None:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        cursor.execute(
            f"""
            UPDATE {SCHEMA}.operation_receipt
            SET state=%s, result=%s, error=%s, updated_at=now()
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s AND digest=%s
              AND state IN ('queued','running')
            """,
            (
                state,
                None if result is None else Json(result),
                None if error is None else Json(error),
                *item.receipt_key,
                item.digest,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("PostgreSQL fenced receipt terminal CAS failed")
        cursor.execute(
            f"""
            DELETE FROM {SCHEMA}.effect_outbox
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s AND digest=%s
            """,
            (*item.receipt_key, item.digest),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("PostgreSQL fenced outbox delete CAS failed")

    @staticmethod
    def _current_outcome(cursor, item: FencedOutboxItem) -> tuple[str, Any] | None:
        cursor.execute(
            f"""
            SELECT highest_fencing_token, active_turn_id
            FROM {SCHEMA}.scope_state
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s
            FOR UPDATE
            """,
            item.scope_key,
        )
        scope = cursor.fetchone()
        if scope is None:
            raise RuntimeError("PostgreSQL fencing scope row disappeared")
        highest, active_turn = int(scope[0]), str(scope[1])
        if item.fencing_token < highest or (
            item.fencing_token == highest and item.turn_id != active_turn
        ):
            return "stale", _stale_error(item, highest)

        cursor.execute(
            f"""
            SELECT writer, session_scope_id
            FROM {SCHEMA}.session_binding
            WHERE account_id=%s AND user_id=%s AND session_id=%s
            FOR UPDATE
            """,
            (item.account_id, item.user_id, item.resource_id),
        )
        binding = cursor.fetchone()
        if binding is not None and (str(binding[0]), str(binding[1])) != (
            item.writer,
            item.session_scope_id,
        ):
            return "conflict", _sanitized_error(
                code="CONFLICT",
                message="Session is already bound to a different writer scope",
                reason="session_scope_conflict",
                details={"session_id": item.resource_id},
            )

        cursor.execute(
            f"""
            SELECT operation_id, digest, result
            FROM {SCHEMA}.session_turn_closure
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND turn_id=%s AND session_id=%s
            FOR UPDATE
            """,
            (*item.scope_key, item.turn_id, item.resource_id),
        )
        closure = cursor.fetchone()
        if closure is None:
            return None
        closure_operation_id, closure_digest, closure_result = closure
        if (
            item.operation == "commit"
            and str(closure_operation_id) == item.operation_id
            and str(closure_digest) == item.digest
        ):
            return "completed", _json_value(closure_result)
        if item.operation in {"message", "used", "commit"}:
            return "conflict", _sanitized_error(
                code="CONFLICT",
                message="The session is closed for this turn",
                reason="session_turn_closed",
                details={"session_id": item.resource_id, "turn_id": item.turn_id},
            )
        return None

    def _claim_sync(self) -> _ClaimOutcome:
        conn = _connect(application_name="openviking-fenced-writer")
        try:
            conn.autocommit = False
            with conn.cursor() as cursor:
                cursor.execute(
                    _OUTBOX_SELECT
                    + f"""
                    WHERE o.available_at <= now()
                      AND o.writer='alice'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {SCHEMA}.effect_outbox earlier
                          WHERE earlier.account_id=o.account_id
                            AND earlier.user_id=o.user_id
                            AND earlier.sequence_id < o.sequence_id
                            AND (
                                (
                                    earlier.writer=o.writer
                                    AND earlier.session_scope_id=o.session_scope_id
                                )
                                OR earlier.resource_id=o.resource_id
                            )
                    )
                    ORDER BY o.sequence_id
                    LIMIT 32
                    """
                )
                candidates = [_outbox_item(row) for row in cursor.fetchall()]
                for candidate in candidates:
                    lock_keys = self._lock_keys(candidate)
                    if not self._try_session_locks(cursor, lock_keys):
                        continue

                    # Candidate discovery is deliberately lock-free.  Once
                    # this process owns the non-expiring writer advisory locks,
                    # take rows in the same order as submit/replay:
                    # operation_receipt -> effect_outbox.  Scope/binding/
                    # closure decisions happen in `_authorize_effect_sync`
                    # after this short claim transaction commits.
                    locked_receipt = self._lock_operation_receipt(
                        cursor, candidate
                    )
                    if locked_receipt is None:
                        self._release_session_locks(cursor, lock_keys)
                        conn.rollback()
                        continue
                    cursor.execute(
                        _OUTBOX_SELECT
                        + f"""
                        WHERE o.account_id=%s AND o.user_id=%s AND o.writer=%s
                          AND o.session_scope_id=%s AND o.operation_id=%s
                          AND o.digest=%s AND o.available_at <= now()
                          AND NOT EXISTS (
                              SELECT 1
                              FROM {SCHEMA}.effect_outbox earlier
                              WHERE earlier.account_id=o.account_id
                                AND earlier.user_id=o.user_id
                                AND earlier.sequence_id < o.sequence_id
                                AND (
                                    (
                                        earlier.writer=o.writer
                                        AND earlier.session_scope_id=o.session_scope_id
                                    )
                                    OR earlier.resource_id=o.resource_id
                                )
                          )
                        FOR UPDATE OF o
                        """,
                        (*candidate.receipt_key, candidate.digest),
                    )
                    current_row = cursor.fetchone()
                    if current_row is None:
                        self._release_session_locks(cursor, lock_keys)
                        conn.rollback()
                        continue
                    current = _outbox_item(current_row)
                    if current.sequence_id != candidate.sequence_id:
                        self._release_session_locks(cursor, lock_keys)
                        conn.rollback()
                        continue
                    if current.fencing_token != locked_receipt.fencing_token:
                        raise RuntimeError(
                            "PostgreSQL fenced receipt/outbox token mismatch"
                        )

                    claim_token = uuid.uuid4().hex
                    cursor.execute(
                        f"""
                        UPDATE {SCHEMA}.effect_outbox
                        SET state='running', claim_token=%s,
                            attempt_count=attempt_count+1,
                            claimed_at=now(), updated_at=now()
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                          AND digest=%s
                        """,
                        (claim_token, *current.receipt_key, current.digest),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("PostgreSQL fenced outbox claim CAS failed")
                    cursor.execute(
                        f"""
                        UPDATE {SCHEMA}.operation_receipt
                        SET state='running', updated_at=now()
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                          AND digest=%s AND state IN ('queued','running')
                        """,
                        (*current.receipt_key, current.digest),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("PostgreSQL fenced receipt claim CAS failed")
                    conn.commit()
                    item = replace(
                        current,
                        state="running",
                        attempt_count=current.attempt_count + 1,
                        claim_token=claim_token,
                    )
                    return _ClaimOutcome(
                        handle=_ClaimHandle(conn=conn, item=item, lock_keys=lock_keys),
                        processed=True,
                    )
            conn.rollback()
            conn.close()
            return _ClaimOutcome(handle=None, processed=False)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()
            raise

    def _authorize_effect_sync(
        self,
        handle: _ClaimHandle,
    ) -> tuple[bool, FencedOutboxItem]:
        conn = handle.conn
        item = handle.item
        try:
            with conn.cursor() as cursor:
                # Keep the row-lock order compatible with submit/replay.  In
                # particular, never hold effect_outbox while waiting on
                # scope_state: that inverted submit's scope -> outbox order
                # and produced a real PostgreSQL deadlock under exact replay.
                locked_receipt = self._lock_operation_receipt(cursor, item)
                if locked_receipt is None:
                    raise RuntimeError("PostgreSQL fenced receipt claim was lost")
                outcome = self._current_outcome(cursor, locked_receipt)
                cursor.execute(
                    _OUTBOX_SELECT
                    + """
                    WHERE o.account_id=%s AND o.user_id=%s AND o.writer=%s
                      AND o.session_scope_id=%s AND o.operation_id=%s
                      AND o.claim_token=%s
                    FOR UPDATE OF o
                    """,
                    (*item.receipt_key, item.claim_token),
                )
                current_row = cursor.fetchone()
                if current_row is None:
                    raise RuntimeError("PostgreSQL fenced claim was lost")
                current = _outbox_item(current_row)
                if current.fencing_token != locked_receipt.fencing_token:
                    raise RuntimeError(
                        "PostgreSQL fenced receipt/outbox token mismatch"
                    )
                if current.effect_started_at is not None:
                    # A durable started effect must be recovered even if a
                    # higher fence arrived after its former process died.
                    self._publish_binding(cursor, current)
                    conn.commit()
                    return True, current

                if outcome is not None:
                    state, value = outcome
                    if state == "completed":
                        self._terminalize(
                            cursor,
                            current,
                            state="completed",
                            error=None,
                            result=value,
                        )
                    else:
                        self._terminalize(
                            cursor,
                            current,
                            state=state,
                            error=value,
                        )
                    conn.commit()
                    return False, current

                # Once the durable effect-start boundary is crossed, another
                # scope must never claim the same session while this operation
                # is being recovered.  Pre-effect stale/conflict paths above do
                # not publish a placeholder; ambiguous/started work does.
                self._publish_binding(cursor, current)
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.effect_outbox
                    SET effect_started_at=now(), updated_at=now()
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                      AND claim_token=%s AND effect_started_at IS NULL
                    RETURNING effect_started_at
                    """,
                    (*current.receipt_key, current.claim_token),
                )
                started = cursor.fetchone()
                if started is None:
                    raise RuntimeError("PostgreSQL effect-start CAS failed")
                conn.commit()
                return True, replace(current, effect_started_at=started[0])
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _publish_binding(cursor, item: FencedOutboxItem) -> None:
        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.session_binding
                (account_id,user_id,session_id,writer,session_scope_id)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (account_id,user_id,session_id) DO NOTHING
            """,
            (
                item.account_id,
                item.user_id,
                item.resource_id,
                item.writer,
                item.session_scope_id,
            ),
        )
        cursor.execute(
            f"""
            SELECT writer, session_scope_id
            FROM {SCHEMA}.session_binding
            WHERE account_id=%s AND user_id=%s AND session_id=%s
            """,
            (item.account_id, item.user_id, item.resource_id),
        )
        binding = cursor.fetchone()
        if binding is None or (str(binding[0]), str(binding[1])) != (
            item.writer,
            item.session_scope_id,
        ):
            raise RuntimeError("PostgreSQL session binding completion conflict")

    @classmethod
    def _write_effect_receipt(
        cls,
        cursor,
        item: FencedOutboxItem,
        result: dict[str, Any],
    ) -> int:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        cursor.execute(
            f"""
            SELECT fencing_token
            FROM {SCHEMA}.effect_outbox
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s AND digest=%s
            FOR UPDATE
            """,
            (*item.receipt_key, item.digest),
        )
        outbox = cursor.fetchone()
        if outbox is None:
            raise RuntimeError("PostgreSQL fenced outbox disappeared before receipt")
        current_token = int(outbox[0])
        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.effect_receipt
                (account_id,user_id,writer,session_scope_id,operation_id,
                 operation,resource_id,turn_id,digest,fencing_token,result)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (
                account_id,user_id,writer,session_scope_id,operation_id
            ) DO UPDATE SET result=EXCLUDED.result,
                            fencing_token=GREATEST(
                                {SCHEMA}.effect_receipt.fencing_token,
                                EXCLUDED.fencing_token
                            ),
                            completed_at=now()
            WHERE {SCHEMA}.effect_receipt.digest=EXCLUDED.digest
            """,
            (
                *item.receipt_key,
                item.operation,
                item.resource_id,
                item.turn_id,
                item.digest,
                current_token,
                Json(result),
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("PostgreSQL effect receipt digest conflict")
        return current_token

    @staticmethod
    def _enqueue_commit_work(
        cursor,
        item: FencedOutboxItem,
        result: dict[str, Any],
        *,
        wait_for_completion: bool,
    ) -> None:
        if item.operation != "commit":
            return
        task_id = result.get("task_id")
        archive_uri = result.get("archive_uri")
        if not isinstance(task_id, str) or not task_id:
            return
        if not isinstance(archive_uri, str) or not archive_uri:
            raise RuntimeError("Fenced commit task is missing archive_uri")
        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.commit_work_outbox
                (account_id,user_id,writer,session_scope_id,operation_id,
                 session_id,task_id,archive_uri,wait_for_completion)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (
                account_id,user_id,writer,session_scope_id,operation_id
            ) DO NOTHING
            """,
            (
                *item.receipt_key,
                item.resource_id,
                task_id,
                archive_uri,
                wait_for_completion,
            ),
        )
        cursor.execute(
            f"""
            SELECT session_id,task_id,archive_uri,wait_for_completion
            FROM {SCHEMA}.commit_work_outbox
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s
            """,
            item.receipt_key,
        )
        work = cursor.fetchone()
        if work is None or (
            str(work[0]),
            str(work[1]),
            str(work[2]),
            bool(work[3]),
        ) != (
            item.resource_id,
            task_id,
            archive_uri,
            wait_for_completion,
        ):
            raise RuntimeError("PostgreSQL deterministic commit work conflict")

    @staticmethod
    def _write_commit_closure(
        cursor,
        item: FencedOutboxItem,
        result: dict[str, Any],
        current_token: int,
    ) -> None:
        if item.operation != "commit":
            return
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        cursor.execute(
            f"""
            INSERT INTO {SCHEMA}.session_turn_closure
                (account_id,user_id,writer,session_scope_id,turn_id,
                 session_id,operation_id,digest,fencing_token,result)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (
                account_id,user_id,writer,session_scope_id,turn_id,session_id
            ) DO NOTHING
            """,
            (
                *item.scope_key,
                item.turn_id,
                item.resource_id,
                item.operation_id,
                item.digest,
                current_token,
                Json(result),
            ),
        )
        if cursor.rowcount != 1:
            cursor.execute(
                f"""
                SELECT operation_id, digest
                FROM {SCHEMA}.session_turn_closure
                WHERE account_id=%s AND user_id=%s AND writer=%s
                  AND session_scope_id=%s AND turn_id=%s AND session_id=%s
                """,
                (*item.scope_key, item.turn_id, item.resource_id),
            )
            closure = cursor.fetchone()
            if closure is None or (str(closure[0]), str(closure[1])) != (
                item.operation_id,
                item.digest,
            ):
                raise RuntimeError("PostgreSQL commit closure conflict")

    @classmethod
    def _complete_on_cursor(
        cls,
        cursor,
        item: FencedOutboxItem,
        result: dict[str, Any],
    ) -> None:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        cls._publish_binding(cursor, item)
        current_token = cls._write_effect_receipt(cursor, item, result)
        cls._enqueue_commit_work(
            cursor,
            item,
            result,
            wait_for_completion=False,
        )
        cls._write_commit_closure(cursor, item, result, current_token)

        cursor.execute(
            f"""
            UPDATE {SCHEMA}.operation_receipt
            SET state='completed', result=%s, error=NULL,
                fencing_token=GREATEST(fencing_token,%s), updated_at=now()
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s AND digest=%s
              AND state='running'
            """,
            (Json(result), current_token, *item.receipt_key, item.digest),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("PostgreSQL operation completion CAS failed")
        cursor.execute(
            f"""
            DELETE FROM {SCHEMA}.effect_outbox
            WHERE account_id=%s AND user_id=%s AND writer=%s
              AND session_scope_id=%s AND operation_id=%s AND digest=%s
            """,
            (*item.receipt_key, item.digest),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("PostgreSQL outbox completion delete failed")

    def _complete_sync(
        self,
        handle: _ClaimHandle,
        result: dict[str, Any],
    ) -> None:
        try:
            with handle.conn.cursor() as cursor:
                if self._lock_operation_receipt(cursor, handle.item) is None:
                    raise RuntimeError(
                        "PostgreSQL fenced completion receipt was lost"
                    )
                self._complete_on_cursor(cursor, handle.item, result)
            handle.conn.commit()
        except Exception:
            handle.conn.rollback()
            raise

    def _defer_wait_sync(
        self,
        handle: _ClaimHandle,
        result: dict[str, Any],
    ) -> None:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        try:
            with handle.conn.cursor() as cursor:
                if self._lock_operation_receipt(cursor, handle.item) is None:
                    raise RuntimeError(
                        "PostgreSQL fenced wait receipt was lost"
                    )
                self._publish_binding(cursor, handle.item)
                current_token = self._write_effect_receipt(
                    cursor, handle.item, result
                )
                self._enqueue_commit_work(
                    cursor,
                    handle.item,
                    result,
                    wait_for_completion=True,
                )
                # Phase 1 is the session mutation boundary.  Closure is
                # permanent even if asynchronous memory extraction later
                # fails; same-turn writes must never reopen the archive.
                self._write_commit_closure(
                    cursor,
                    handle.item,
                    result,
                    current_token,
                )
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.operation_receipt
                    SET state='running', result=%s, error=NULL,
                        fencing_token=GREATEST(fencing_token,%s), updated_at=now()
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                      AND digest=%s AND state='running'
                    """,
                    (
                        Json(result),
                        current_token,
                        *handle.item.receipt_key,
                        handle.item.digest,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("PostgreSQL task-wait receipt CAS failed")
                cursor.execute(
                    f"""
                    DELETE FROM {SCHEMA}.effect_outbox
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                      AND digest=%s AND claim_token=%s
                    """,
                    (
                        *handle.item.receipt_key,
                        handle.item.digest,
                        handle.item.claim_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("PostgreSQL task-wait outbox delete failed")
            handle.conn.commit()
        except Exception:
            handle.conn.rollback()
            raise

    def _retry_or_fail_sync(self, handle: _ClaimHandle) -> str:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        item = handle.item
        try:
            with handle.conn.cursor() as cursor:
                if self._lock_operation_receipt(cursor, item) is None:
                    raise RuntimeError(
                        "PostgreSQL fenced retry receipt was lost"
                    )
                if (
                    item.effect_started_at is None
                    and item.attempt_count >= self._max_attempts
                ):
                    self._terminalize(
                        cursor,
                        item,
                        state="failed",
                        error=_effect_failed_error(),
                    )
                    outcome = "permanent_failure"
                else:
                    cursor.execute(
                        f"""
                        UPDATE {SCHEMA}.effect_outbox
                        SET state='queued', claim_token=NULL,
                            available_at=now() + (%s * interval '1 second'),
                            updated_at=now()
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                          AND digest=%s AND claim_token=%s
                        """,
                        (
                            self._retry_delay_seconds,
                            *item.receipt_key,
                            item.digest,
                            item.claim_token,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("PostgreSQL effect retry CAS failed")
                    cursor.execute(
                        f"""
                        UPDATE {SCHEMA}.operation_receipt
                        SET state=%s, error=%s, updated_at=now()
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                          AND digest=%s AND state='running'
                        """,
                        (
                            (
                                "running"
                                if item.effect_started_at is not None
                                else "queued"
                            ),
                            Json(_effect_failed_error()),
                            *item.receipt_key,
                            item.digest,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("PostgreSQL receipt retry CAS failed")
                    outcome = "retry"
            handle.conn.commit()
            return outcome
        except Exception:
            handle.conn.rollback()
            raise

    def _terminal_effect_error_sync(
        self,
        handle: _ClaimHandle,
        error: dict[str, Any],
        *,
        state: str,
    ) -> None:
        try:
            with handle.conn.cursor() as cursor:
                self._terminalize(cursor, handle.item, state=state, error=error)
            handle.conn.commit()
        except Exception:
            handle.conn.rollback()
            raise

    @staticmethod
    def _ping_sync(conn) -> None:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise RuntimeError("PostgreSQL fenced writer ping failed")
        conn.commit()

    async def _execute_with_leadership_monitor(
        self,
        handle: _ClaimHandle,
    ) -> dict[str, Any]:
        effect_task = asyncio.create_task(self._executor(handle.item))
        try:
            while not effect_task.done():
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(effect_task),
                        timeout=self._monitor_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    # Await the ping before looking at the effect result.  This
                    # avoids using one psycopg connection concurrently if the
                    # effect completes while a keepalive is in flight.
                    try:
                        await asyncio.to_thread(self._ping_sync, handle.conn)
                    except BaseException as exc:
                        self._fail_stop(exc)
                        raise
            return await effect_task
        except BaseException:
            if not effect_task.done():
                effect_task.cancel()
                await asyncio.gather(effect_task, return_exceptions=True)
            raise

    async def run_once(self) -> bool:
        outcome = await asyncio.to_thread(self._claim_sync)
        if outcome.handle is None:
            return outcome.processed
        handle = outcome.handle
        try:
            from openviking.server import fenced_operation  # noqa: PLC0415

            seam = fenced_operation.after_fenced_writer_claimed(
                handle.item.operation_id
            )
            if inspect.isawaitable(seam):
                await seam

            authorized, current = await asyncio.to_thread(
                self._authorize_effect_sync,
                handle,
            )
            handle.item = current
            if not authorized:
                _record_effect_metric(handle.item, "suppressed")
                return True

            seam = fenced_operation.after_fenced_writer_effect_started(
                handle.item.operation_id
            )
            if inspect.isawaitable(seam):
                await seam

            result = await self._execute_with_leadership_monitor(handle)
            if not isinstance(result, dict):
                raise TypeError("Fenced outbox effect must return a dict")

            seam = fenced_operation.after_fenced_effect_before_receipt(
                handle.item.operation
            )
            if inspect.isawaitable(seam):
                await seam

            wait_requested = bool(handle.item.request_payload.get("wait"))
            task_id = result.get("task_id")
            if (
                handle.item.operation == "commit"
                and wait_requested
                and isinstance(task_id, str)
                and task_id
            ):
                await asyncio.to_thread(
                    self._defer_wait_sync,
                    handle,
                    result,
                )
            else:
                await asyncio.to_thread(self._complete_sync, handle, result)
            _record_effect_metric(handle.item, "completed")
            return True
        except PermanentFencedEffectError as exc:
            logger.error(
                "Fenced outbox effect rejected before mutation",
                extra={
                    "operation_id": handle.item.operation_id,
                    "reason": exc.reason,
                },
            )
            await asyncio.to_thread(
                self._terminal_effect_error_sync,
                handle,
                _sanitized_error(
                    code="FAILED_PRECONDITION",
                    message="Fenced session effect was rejected",
                    reason=exc.reason,
                    details=exc.details,
                ),
                state="failed",
            )
            _record_effect_metric(handle.item, "permanent_failure")
            return True
        except Exception:
            logger.exception(
                "Fenced outbox effect failed; scheduling deterministic recovery",
                extra={"operation_id": handle.item.operation_id},
            )
            retry_outcome = await asyncio.to_thread(
                self._retry_or_fail_sync, handle
            )
            _record_effect_metric(handle.item, retry_outcome)
            return True
        finally:
            try:
                handle.conn.close()
            except Exception:
                pass

    @staticmethod
    def _commit_work_lock_keys(item: FencedCommitWorkItem) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    # Active-count targets are account-scoped resources.  The
                    # durable Phase 2 plan reads an absolute value and later
                    # applies it idempotently; serializing plan+apply per
                    # account prevents two sessions from both planning N+1.
                    _advisory_key(
                        "outbox-account-meta",
                        item.account_id,
                    ),
                    _advisory_key(
                # Share the exact lock namespace with Phase 1 effects.  Phase 2
                # may update session meta, so a next-turn write to the *same*
                # session must not interleave; a rotated/new session still runs
                # independently.
                        "outbox-session",
                        item.account_id,
                        item.user_id,
                        item.session_id,
                    ),
                }
            )
        )

    def _claim_commit_work_sync(self) -> Optional[_CommitClaimHandle]:
        conn = _connect(application_name="openviking-fenced-commit-worker")
        try:
            conn.autocommit = False
            with conn.cursor() as cursor:
                cursor.execute(
                    _COMMIT_WORK_SELECT
                    + f"""
                    WHERE available_at <= now() AND writer='alice'
                      AND state IN ('pending','running')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {SCHEMA}.commit_work_outbox earlier
                          WHERE earlier.account_id=commit_work_outbox.account_id
                            AND earlier.user_id=commit_work_outbox.user_id
                            AND earlier.session_id=commit_work_outbox.session_id
                            AND earlier.sequence_id < commit_work_outbox.sequence_id
                      )
                    ORDER BY sequence_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 32
                    """
                )
                candidates = [
                    _commit_work_item(row) for row in cursor.fetchall()
                ]
                for candidate in candidates:
                    lock_keys = self._commit_work_lock_keys(candidate)
                    if not self._try_session_locks(cursor, lock_keys):
                        continue
                    claim_token = uuid.uuid4().hex
                    cursor.execute(
                        f"""
                        UPDATE {SCHEMA}.commit_work_outbox
                        SET state='running',claim_token=%s,
                            attempt_count=attempt_count+1,started_at=now(),
                            updated_at=now()
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                        RETURNING attempt_count
                        """,
                        (claim_token, *candidate.receipt_key),
                    )
                    claimed = cursor.fetchone()
                    if claimed is None:
                        raise RuntimeError("PostgreSQL commit work claim CAS failed")
                    conn.commit()
                    return _CommitClaimHandle(
                        conn=conn,
                        item=replace(
                            candidate,
                            state="running",
                            attempt_count=int(claimed[0]),
                            claim_token=claim_token,
                        ),
                        lock_keys=lock_keys,
                    )
            conn.rollback()
            conn.close()
            return None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()
            raise

    def _finish_commit_work_sync(
        self,
        handle: _CommitClaimHandle,
        status: str,
    ) -> None:
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        item = handle.item
        try:
            with handle.conn.cursor() as cursor:
                receipt_state = self._lock_commit_receipt(cursor, item)
                cursor.execute(
                    f"""
                    SELECT wait_for_completion
                    FROM {SCHEMA}.commit_work_outbox
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                      AND claim_token=%s
                    FOR UPDATE
                    """,
                    (*item.receipt_key, item.claim_token),
                )
                work = cursor.fetchone()
                if work is None:
                    raise RuntimeError("PostgreSQL commit work claim was lost")
                wait_for_completion = bool(work[0])
                if wait_for_completion:
                    if receipt_state != "running":
                        raise RuntimeError(
                            "PostgreSQL wait=true receipt is not running"
                        )
                    if status == "completed":
                        cursor.execute(
                            f"""
                            UPDATE {SCHEMA}.operation_receipt
                            SET state='completed',error=NULL,updated_at=now()
                            WHERE account_id=%s AND user_id=%s AND writer=%s
                              AND session_scope_id=%s AND operation_id=%s
                              AND state='running'
                            """,
                            item.receipt_key,
                        )
                    else:
                        cursor.execute(
                            f"""
                            UPDATE {SCHEMA}.operation_receipt
                            SET state='failed',error=%s,updated_at=now()
                            WHERE account_id=%s AND user_id=%s AND writer=%s
                              AND session_scope_id=%s AND operation_id=%s
                              AND state='running'
                            """,
                            (Json(_effect_failed_error()), *item.receipt_key),
                        )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "PostgreSQL wait=true receipt completion CAS failed"
                        )
                cursor.execute(
                    f"""
                    DELETE FROM {SCHEMA}.commit_work_outbox
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                      AND claim_token=%s
                    """,
                    (*item.receipt_key, item.claim_token),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("PostgreSQL commit work delete CAS failed")
            handle.conn.commit()
        except Exception:
            handle.conn.rollback()
            raise

    def _retry_commit_work_sync(self, handle: _CommitClaimHandle) -> None:
        try:
            with handle.conn.cursor() as cursor:
                self._lock_commit_receipt(cursor, handle.item)
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.commit_work_outbox
                    SET state='pending',claim_token=NULL,
                        available_at=now() + (%s * interval '1 second'),
                        updated_at=now()
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                      AND claim_token=%s
                    """,
                    (
                        self._retry_delay_seconds,
                        *handle.item.receipt_key,
                        handle.item.claim_token,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("PostgreSQL commit work retry CAS failed")
            handle.conn.commit()
        except Exception:
            handle.conn.rollback()
            raise

    def _quarantine_commit_work_sync(
        self,
        handle: _CommitClaimHandle,
        *,
        reason: str,
    ) -> None:
        """Persist an ambiguous non-replayable effect for manual forward-fix."""
        from psycopg2.extras import Json  # type: ignore  # noqa: PLC0415

        error = _sanitized_error(
            code="FAILED_PRECONDITION",
            message="Fenced commit phase2 requires operator review",
            reason=reason,
        )
        try:
            with handle.conn.cursor() as cursor:
                receipt_state = self._lock_commit_receipt(
                    cursor, handle.item
                )
                cursor.execute(
                    f"""
                    UPDATE {SCHEMA}.commit_work_outbox
                    SET state='ambiguous',claim_token=NULL,error=%s,
                        updated_at=now()
                    WHERE account_id=%s AND user_id=%s AND writer=%s
                      AND session_scope_id=%s AND operation_id=%s
                      AND claim_token=%s
                    RETURNING wait_for_completion
                    """,
                    (
                        Json(error),
                        *handle.item.receipt_key,
                        handle.item.claim_token,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError(
                        "PostgreSQL commit work quarantine CAS failed"
                    )
                if bool(row[0]):
                    if receipt_state != "running":
                        raise RuntimeError(
                            "PostgreSQL ambiguous receipt is not running"
                        )
                    cursor.execute(
                        f"""
                        UPDATE {SCHEMA}.operation_receipt
                        SET state='failed',error=%s,updated_at=now()
                        WHERE account_id=%s AND user_id=%s AND writer=%s
                          AND session_scope_id=%s AND operation_id=%s
                          AND state='running'
                        """,
                        (Json(error), *handle.item.receipt_key),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(
                            "PostgreSQL ambiguous receipt CAS failed"
                        )
            handle.conn.commit()
        except Exception:
            handle.conn.rollback()
            raise

    async def _run_commit_work_with_monitor(
        self,
        handle: _CommitClaimHandle,
    ) -> str:
        if self._task_waiter is None:
            raise RuntimeError("Fenced commit work executor is not configured")
        work_task = asyncio.create_task(self._task_waiter(handle.item))
        try:
            while not work_task.done():
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(work_task),
                        timeout=self._monitor_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    try:
                        await asyncio.to_thread(self._ping_sync, handle.conn)
                    except BaseException as exc:
                        self._fail_stop(exc)
                        raise
            return await work_task
        except BaseException:
            if not work_task.done():
                work_task.cancel()
                await asyncio.gather(work_task, return_exceptions=True)
            raise

    async def process_waiting_once(self) -> bool:
        if self._task_waiter is None:
            return False
        handle = await asyncio.to_thread(self._claim_commit_work_sync)
        if handle is None:
            return False
        try:
            status = await self._run_commit_work_with_monitor(handle)
            if status not in {"completed", "failed"}:
                raise RuntimeError("Commit work did not reach a terminal task state")
            await asyncio.to_thread(self._finish_commit_work_sync, handle, status)
            return True
        except FailedPreconditionError as exc:
            reason = (getattr(exc, "details", None) or {}).get("reason")
            permanent_reasons = {
                "commit_phase2_effect_ambiguous",
                "commit_phase2_task_ambiguous",
                "commit_manifest_missing",
                "commit_phase2_manifest_corrupt",
                "commit_work_identity_conflict",
                "commit_phase2_payload_missing",
                "commit_phase2_payload_corrupt",
                "session_messages_missing",
                "session_messages_corrupt",
                "session_meta_missing",
                "session_meta_corrupt",
                "usage_journal_corrupt",
            }
            if reason not in permanent_reasons:
                logger.warning(
                    "Fenced commit precondition is retryable; preserving work",
                    extra={"operation_id": handle.item.operation_id},
                )
                await asyncio.to_thread(self._retry_commit_work_sync, handle)
                return True
            quarantine_reason = (
                "commit_phase2_effect_ambiguous"
                if reason
                in {
                    "commit_phase2_effect_ambiguous",
                    "commit_phase2_task_ambiguous",
                }
                else "commit_phase2_invalid"
            )
            logger.error(
                "Fenced commit work quarantined after a permanent precondition",
                extra={"operation_id": handle.item.operation_id},
            )
            try:
                from openviking.service.task_tracker import (  # noqa: PLC0415
                    get_task_tracker,
                )

                await get_task_tracker().fail(
                    handle.item.task_id,
                    f"{quarantine_reason}:{reason or 'unknown'}",
                    account_id=handle.item.account_id,
                    user_id=handle.item.user_id,
                )
            except Exception:
                logger.exception(
                    "Failed to terminalize fenced commit task; preserving "
                    "reconciliation work",
                    extra={"operation_id": handle.item.operation_id},
                )
                # Do not make the PostgreSQL row permanently unclaimable until
                # TaskTracker records the same terminal decision.  Otherwise a
                # transient AGFS failure leaves a pending/running task paired
                # with an `ambiguous` row that no worker will ever reclaim.
                await asyncio.to_thread(self._retry_commit_work_sync, handle)
                return True
            await asyncio.to_thread(
                self._quarantine_commit_work_sync,
                handle,
                reason=quarantine_reason,
            )
            return True
        except Exception:
            logger.exception(
                "Fenced commit work failed; scheduling durable recovery",
                extra={"operation_id": handle.item.operation_id},
            )
            await asyncio.to_thread(self._retry_commit_work_sync, handle)
            return True
        finally:
            try:
                handle.conn.close()
            except Exception:
                pass


class PostgresFencedWriterPool:
    """Supervised effect/commit pools with bounded idle connection churn."""

    def __init__(
        self,
        executor: EffectExecutor,
        *,
        task_waiter: Optional[TaskWaiter] = None,
        concurrency: int = 2,
        commit_concurrency: int = 2,
        poll_seconds: float = 0.25,
        max_idle_seconds: float = 1.0,
        drain_timeout_seconds: float = 30.0,
    ) -> None:
        self._poll_seconds = max(0.05, float(poll_seconds))
        self._max_idle_seconds = max(
            self._poll_seconds, float(max_idle_seconds)
        )
        self._drain_timeout_seconds = max(
            0.1, float(drain_timeout_seconds)
        )
        self._task_waiter_configured = task_waiter is not None
        self._workers = [
            PostgresFencedOutboxWriter(executor, task_waiter=task_waiter)
            for _ in range(max(1, int(concurrency)))
        ]
        self._commit_workers = [
            PostgresFencedOutboxWriter(executor, task_waiter=task_waiter)
            for _ in range(max(1, int(commit_concurrency)))
        ]
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False
        self._unhealthy = False
        self._unexpected_failure: Optional[str] = None

    @property
    def healthy(self) -> bool:
        return bool(
            self._tasks
            and not self._stopping
            and not self._unhealthy
            and all(not task.done() for task in self._tasks)
        )

    @property
    def effect_concurrency(self) -> int:
        return len(self._workers)

    @property
    def commit_concurrency(self) -> int:
        return len(self._commit_workers)

    async def _idle(self, current: float) -> float:
        jittered = current * random.uniform(0.9, 1.1)
        await asyncio.sleep(jittered)
        return min(self._max_idle_seconds, current * 2)

    async def _worker_loop(self, worker: PostgresFencedOutboxWriter) -> None:
        idle_seconds = self._poll_seconds
        while not self._stopping:
            try:
                processed = await worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._unhealthy = True
                self._unexpected_failure = type(exc).__name__
                logger.exception(
                    "Fenced effect worker iteration failed; readiness is false"
                )
                if not self._stopping:
                    idle_seconds = await self._idle(idle_seconds)
                continue
            if processed:
                idle_seconds = self._poll_seconds
            elif not self._stopping:
                idle_seconds = await self._idle(idle_seconds)

    async def _watcher_loop(self, watcher: PostgresFencedOutboxWriter) -> None:
        idle_seconds = self._poll_seconds
        while not self._stopping:
            try:
                processed = await watcher.process_waiting_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._unhealthy = True
                self._unexpected_failure = type(exc).__name__
                logger.exception(
                    "Fenced commit worker iteration failed; readiness is false"
                )
                if not self._stopping:
                    idle_seconds = await self._idle(idle_seconds)
                continue
            if processed:
                idle_seconds = self._poll_seconds
            elif not self._stopping:
                idle_seconds = await self._idle(idle_seconds)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        if self._stopping or task.cancelled():
            return
        self._unhealthy = True
        try:
            exc = task.exception()
        except BaseException as error:
            exc = error
        self._unexpected_failure = (
            type(exc).__name__ if exc is not None else "unexpected_exit"
        )
        logger.critical(
            "Fenced writer pool loop exited unexpectedly; readiness is false",
            exc_info=(
                (type(exc), exc, exc.__traceback__)
                if exc is not None
                else None
            ),
        )

    def start(self) -> None:
        if self._tasks:
            return
        self._stopping = False
        self._unhealthy = False
        self._unexpected_failure = None
        tasks = [
            asyncio.create_task(
                self._worker_loop(worker),
                name=f"openviking-fenced-effect-{index}",
            )
            for index, worker in enumerate(self._workers)
        ]
        if self._task_waiter_configured:
            tasks.extend(
                asyncio.create_task(
                    self._watcher_loop(worker),
                    name=f"openviking-fenced-commit-{index}",
                )
                for index, worker in enumerate(self._commit_workers)
            )
        self._tasks = tasks
        for task in tasks:
            task.add_done_callback(self._task_done)

    async def stop(self, *, drain_timeout_seconds: Optional[float] = None) -> None:
        tasks = list(self._tasks)
        if not tasks:
            return
        self._stopping = True
        timeout = (
            self._drain_timeout_seconds
            if drain_timeout_seconds is None
            else max(0.0, float(drain_timeout_seconds))
        )
        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks = []
