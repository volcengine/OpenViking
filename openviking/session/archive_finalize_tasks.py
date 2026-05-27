# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent state for session archive finalization."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import get_openviking_config

MAX_ARCHIVE_FINALIZE_ATTEMPTS = 3
ARCHIVE_FINALIZE_LEASE_SECONDS = 300
ARCHIVE_FINALIZE_RETRY_DELAY_SECONDS = 1.0
PREPARING_STALE_SECONDS = 5

STATE_PREPARING = "preparing"
STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_RETRY = "retry"
STATE_COMPLETED = "completed"
STATE_TERMINAL_FAILED = "terminal_failed"
_ARCHIVE_ID_RE = re.compile(r"^archive_(\d+)$")


def archive_index_from_id(archive_id: str) -> int:
    """Return the numeric archive index from an archive_NNN identifier."""
    match = _ARCHIVE_ID_RE.fullmatch(archive_id)
    if not match:
        raise ValueError(f"Invalid archive ID: {archive_id}")
    return int(match.group(1))


@dataclass(frozen=True)
class ArchiveFinalizeTask:
    account_id: str
    user_id: str
    agent_id: str
    role: str
    namespace_policy: dict[str, Any]
    session_id: str
    archive_id: str
    archive_uri: str
    state: str
    attempt_count: int
    task_tracker_id: str
    usage_records: list[dict[str, Any]]
    lease_owner: str = ""
    lease_until: float = 0.0
    next_run_at: float = 0.0
    last_error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    claimed_from_state: str = ""

    def request_context(self) -> RequestContext:
        return RequestContext(
            user=UserIdentifier(self.account_id, self.user_id, self.agent_id),
            role=Role(self.role),
            namespace_policy=AccountNamespacePolicy.from_dict(self.namespace_policy),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "session_id": self.session_id,
            "archive_id": self.archive_id,
            "archive_uri": self.archive_uri,
            "state": self.state,
            "attempt_count": self.attempt_count,
            "task_tracker_id": self.task_tracker_id,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until,
            "next_run_at": self.next_run_at,
            "last_error": self.last_error,
        }


def _queue_db_path() -> str:
    config = get_openviking_config()
    storage = config.storage
    queuefs = storage.agfs.queuefs
    configured = queuefs.db_path or storage.agfs.queue_db_path
    if configured:
        return str(Path(configured).expanduser().resolve())
    return str(Path(storage.workspace).expanduser().resolve() / "_system" / "queue" / "queue.db")


def _decode_json(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


class ArchiveFinalizeTaskStore:
    """SQLite-backed task state for archive finalization."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._schema_ready = False
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect_raw(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _connect(self) -> sqlite3.Connection:
        if not Path(self.db_path).exists():
            self._schema_ready = False
        conn = self._connect_raw()
        if not self._schema_ready:
            self._ensure_schema_on_connection(conn)
            conn.commit()
            self._schema_ready = True
        return conn

    def _ensure_schema(self) -> None:
        with self._connect_raw() as conn:
            self._ensure_schema_on_connection(conn)
        self._schema_ready = True

    @staticmethod
    def _ensure_schema_on_connection(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_archive_finalize_tasks (
              account_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              agent_id TEXT NOT NULL,
              role TEXT NOT NULL,
              namespace_policy_json TEXT NOT NULL,
              session_id TEXT NOT NULL,
              archive_id TEXT NOT NULL,
              archive_uri TEXT NOT NULL,
              state TEXT NOT NULL,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              task_tracker_id TEXT NOT NULL DEFAULT '',
              usage_records_json TEXT NOT NULL DEFAULT '[]',
              lease_owner TEXT NOT NULL DEFAULT '',
              lease_until REAL NOT NULL DEFAULT 0,
              next_run_at REAL NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              PRIMARY KEY (account_id, user_id, session_id, archive_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_archive_finalize_claim
            ON session_archive_finalize_tasks(state, next_run_at, lease_until, created_at)
            """
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> ArchiveFinalizeTask:
        return ArchiveFinalizeTask(
            account_id=row["account_id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            role=row["role"],
            namespace_policy=_decode_json(row["namespace_policy_json"], {}),
            session_id=row["session_id"],
            archive_id=row["archive_id"],
            archive_uri=row["archive_uri"],
            state=row["state"],
            attempt_count=int(row["attempt_count"]),
            task_tracker_id=row["task_tracker_id"],
            usage_records=_decode_json(row["usage_records_json"], []),
            lease_owner=row["lease_owner"],
            lease_until=float(row["lease_until"] or 0),
            next_run_at=float(row["next_run_at"] or 0),
            last_error=row["last_error"],
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
        )

    def create_preparing(
        self,
        *,
        ctx: RequestContext,
        session_id: str,
        archive_id: str,
        archive_uri: str,
        task_tracker_id: str,
        usage_records: list[dict[str, Any]],
    ) -> None:
        archive_index_from_id(archive_id)
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_archive_finalize_tasks (
                  account_id, user_id, agent_id, role, namespace_policy_json,
                  session_id, archive_id, archive_uri, state, attempt_count,
                  task_tracker_id, usage_records_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    ctx.account_id,
                    ctx.user.user_id,
                    ctx.user.agent_id,
                    ctx.role.value,
                    json.dumps(ctx.namespace_policy.to_dict(), ensure_ascii=False),
                    session_id,
                    archive_id,
                    archive_uri,
                    STATE_PREPARING,
                    task_tracker_id,
                    json.dumps(usage_records, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    async def create_preparing_async(
        self,
        *,
        ctx: RequestContext,
        session_id: str,
        archive_id: str,
        archive_uri: str,
        task_tracker_id: str,
        usage_records: list[dict[str, Any]],
    ) -> None:
        await asyncio.to_thread(
            self.create_preparing,
            ctx=ctx,
            session_id=session_id,
            archive_id=archive_id,
            archive_uri=archive_uri,
            task_tracker_id=task_tracker_id,
            usage_records=usage_records,
        )

    def mark_pending(self, ctx: RequestContext, session_id: str, archive_id: str) -> None:
        self._update_state(ctx, session_id, archive_id, STATE_PENDING)

    async def mark_pending_async(
        self,
        ctx: RequestContext,
        session_id: str,
        archive_id: str,
    ) -> None:
        await asyncio.to_thread(self.mark_pending, ctx, session_id, archive_id)

    def delete(self, ctx: RequestContext, session_id: str, archive_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM session_archive_finalize_tasks
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                """,
                (ctx.account_id, ctx.user.user_id, session_id, archive_id),
            )

    async def delete_async(
        self,
        ctx: RequestContext,
        session_id: str,
        archive_id: str,
    ) -> None:
        await asyncio.to_thread(self.delete, ctx, session_id, archive_id)

    def get(
        self,
        ctx: RequestContext,
        session_id: str,
        archive_id: str,
    ) -> Optional[ArchiveFinalizeTask]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM session_archive_finalize_tasks
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                """,
                (ctx.account_id, ctx.user.user_id, session_id, archive_id),
            ).fetchone()
        return self._task_from_row(row) if row else None

    async def get_async(
        self,
        ctx: RequestContext,
        session_id: str,
        archive_id: str,
    ) -> Optional[ArchiveFinalizeTask]:
        return await asyncio.to_thread(self.get, ctx, session_id, archive_id)

    def get_blocking_failed(
        self,
        ctx: RequestContext,
        session_id: str,
    ) -> Optional[ArchiveFinalizeTask]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM session_archive_finalize_tasks
                WHERE account_id=? AND user_id=? AND session_id=? AND state=?
                ORDER BY CAST(SUBSTR(archive_id, 9) AS INTEGER) ASC, archive_id ASC
                LIMIT 1
                """,
                (ctx.account_id, ctx.user.user_id, session_id, STATE_TERMINAL_FAILED),
            ).fetchone()
        return self._task_from_row(row) if row else None

    async def get_blocking_failed_async(
        self,
        ctx: RequestContext,
        session_id: str,
    ) -> Optional[ArchiveFinalizeTask]:
        return await asyncio.to_thread(self.get_blocking_failed, ctx, session_id)

    def claim_next(self, owner: str) -> Optional[ArchiveFinalizeTask]:
        try:
            return self._claim_next(owner)
        except sqlite3.OperationalError as exc:
            if "no such table: session_archive_finalize_tasks" not in str(exc):
                raise
            self._schema_ready = False
            self._ensure_schema()
            return None

    async def claim_next_async(self, owner: str) -> Optional[ArchiveFinalizeTask]:
        return await asyncio.to_thread(self.claim_next, owner)

    def _claim_next(self, owner: str) -> Optional[ArchiveFinalizeTask]:
        now = time.time()
        lease_until = now + ARCHIVE_FINALIZE_LEASE_SECONDS
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT * FROM session_archive_finalize_tasks
                WHERE state=?
                   OR (state=? AND next_run_at<=?)
                   OR (state=? AND lease_until<=?)
                   OR (state=? AND created_at<=?)
                ORDER BY created_at ASC
                """,
                (
                    STATE_PENDING,
                    STATE_RETRY,
                    now,
                    STATE_RUNNING,
                    now,
                    STATE_PREPARING,
                    now - PREPARING_STALE_SECONDS,
                ),
            ).fetchall()
            for row in rows:
                task = self._task_from_row(row)
                if self._has_active_session_task(conn, task, now):
                    continue
                if self._has_incomplete_prior_task(conn, task):
                    continue
                conn.execute(
                    """
                    UPDATE session_archive_finalize_tasks
                    SET state=?, lease_owner=?, lease_until=?, updated_at=?
                    WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                    """,
                    (
                        STATE_RUNNING,
                        owner,
                        lease_until,
                        now,
                        task.account_id,
                        task.user_id,
                        task.session_id,
                        task.archive_id,
                    ),
                )
                conn.commit()
                claimed = conn.execute(
                    """
                    SELECT * FROM session_archive_finalize_tasks
                    WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                    """,
                    (task.account_id, task.user_id, task.session_id, task.archive_id),
                ).fetchone()
                return replace(self._task_from_row(claimed), claimed_from_state=task.state)
            conn.commit()
        return None

    def complete(self, task: ArchiveFinalizeTask) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_archive_finalize_tasks
                SET state=?, lease_owner='', lease_until=0, next_run_at=0,
                    last_error='', updated_at=?
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                """,
                (
                    STATE_COMPLETED,
                    now,
                    task.account_id,
                    task.user_id,
                    task.session_id,
                    task.archive_id,
                ),
            )

    async def complete_async(self, task: ArchiveFinalizeTask) -> None:
        await asyncio.to_thread(self.complete, task)

    def release(self, task: ArchiveFinalizeTask) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_archive_finalize_tasks
                SET state=?, lease_owner='', lease_until=0, updated_at=?
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                  AND state=?
                """,
                (
                    STATE_PENDING,
                    now,
                    task.account_id,
                    task.user_id,
                    task.session_id,
                    task.archive_id,
                    STATE_RUNNING,
                ),
            )

    async def release_async(self, task: ArchiveFinalizeTask) -> None:
        await asyncio.to_thread(self.release, task)

    def fail(self, task: ArchiveFinalizeTask, error: str) -> str:
        now = time.time()
        attempt_count = task.attempt_count + 1
        state = STATE_TERMINAL_FAILED
        next_run_at = 0.0
        if attempt_count < MAX_ARCHIVE_FINALIZE_ATTEMPTS:
            state = STATE_RETRY
            next_run_at = now + ARCHIVE_FINALIZE_RETRY_DELAY_SECONDS
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_archive_finalize_tasks
                SET state=?, attempt_count=?, lease_owner='', lease_until=0,
                    next_run_at=?, last_error=?, updated_at=?
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                """,
                (
                    state,
                    attempt_count,
                    next_run_at,
                    error,
                    now,
                    task.account_id,
                    task.user_id,
                    task.session_id,
                    task.archive_id,
                ),
            )
        return state

    async def fail_async(self, task: ArchiveFinalizeTask, error: str) -> str:
        return await asyncio.to_thread(self.fail, task, error)

    def reset_for_retry(
        self,
        task: ArchiveFinalizeTask,
        *,
        task_tracker_id: str,
    ) -> ArchiveFinalizeTask:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_archive_finalize_tasks
                SET state=?, attempt_count=0, task_tracker_id=?, lease_owner='',
                    lease_until=0, next_run_at=0, last_error='', updated_at=?
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                """,
                (
                    STATE_PENDING,
                    task_tracker_id,
                    now,
                    task.account_id,
                    task.user_id,
                    task.session_id,
                    task.archive_id,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM session_archive_finalize_tasks
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                """,
                (task.account_id, task.user_id, task.session_id, task.archive_id),
            ).fetchone()
        return self._task_from_row(row)

    async def reset_for_retry_async(
        self,
        task: ArchiveFinalizeTask,
        *,
        task_tracker_id: str,
    ) -> ArchiveFinalizeTask:
        return await asyncio.to_thread(
            self.reset_for_retry,
            task,
            task_tracker_id=task_tracker_id,
        )

    def _update_state(
        self,
        ctx: RequestContext,
        session_id: str,
        archive_id: str,
        state: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_archive_finalize_tasks
                SET state=?, updated_at=?
                WHERE account_id=? AND user_id=? AND session_id=? AND archive_id=?
                """,
                (state, time.time(), ctx.account_id, ctx.user.user_id, session_id, archive_id),
            )

    @staticmethod
    def _has_active_session_task(
        conn: sqlite3.Connection,
        task: ArchiveFinalizeTask,
        now: float,
    ) -> bool:
        row = conn.execute(
            """
            SELECT 1 FROM session_archive_finalize_tasks
            WHERE account_id=? AND user_id=? AND session_id=? AND archive_id<>?
              AND state=? AND lease_until>?
            LIMIT 1
            """,
            (
                task.account_id,
                task.user_id,
                task.session_id,
                task.archive_id,
                STATE_RUNNING,
                now,
            ),
        ).fetchone()
        return row is not None

    @staticmethod
    def _has_incomplete_prior_task(
        conn: sqlite3.Connection,
        task: ArchiveFinalizeTask,
    ) -> bool:
        row = conn.execute(
            """
            SELECT 1 FROM session_archive_finalize_tasks
            WHERE account_id=? AND user_id=? AND session_id=?
              AND CAST(SUBSTR(archive_id, 9) AS INTEGER)
                  < CAST(SUBSTR(?, 9) AS INTEGER)
              AND state<>?
            LIMIT 1
            """,
            (
                task.account_id,
                task.user_id,
                task.session_id,
                task.archive_id,
                STATE_COMPLETED,
            ),
        ).fetchone()
        return row is not None


_store: Optional[ArchiveFinalizeTaskStore] = None
_store_path: str = ""


def get_archive_finalize_task_store() -> ArchiveFinalizeTaskStore:
    global _store, _store_path
    path = _queue_db_path()
    if _store is None or _store_path != path:
        _store = ArchiveFinalizeTaskStore(path)
        _store_path = path
    return _store
