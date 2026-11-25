# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Local idle candidate index; session files remain authoritative."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from openviking.service.session_auto_commit import (
    compute_next_check_at,
    get_idle_timeout_seconds,
)
from openviking.session.session import SessionExpirySnapshot
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class IdleRegistration:
    account_id: str
    user_id: str
    session_id: str
    next_check_at_ms: int
    registration_version: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.account_id, self.user_id, self.session_id


class SQLiteSessionIdleIndex:
    """Persist discovery hints, using versions to protect concurrent registrations."""

    # Session path locks order saves and registration across handles/workers.
    # No filesystem operations occur while holding a SQLite transaction.
    report_under_lock = True

    def __init__(self, path: str, *, busy_timeout_ms: int = 5000) -> None:
        if path == ":memory:":
            raise ValueError("Session idle index requires a persistent SQLite path")
        self._path = Path(path).expanduser().resolve()
        self._busy_timeout_ms = busy_timeout_ms
        self._connection = None
        self._lock = asyncio.Lock()

    async def _connect_locked(self):
        if self._connection is None:
            import aiosqlite

            await asyncio.to_thread(self._path.parent.mkdir, parents=True, exist_ok=True)
            connection = await aiosqlite.connect(
                str(self._path), timeout=self._busy_timeout_ms / 1000, isolation_level=None
            )
            try:
                async with connection.execute("PRAGMA journal_mode=WAL") as cursor:
                    row = await cursor.fetchone()
                    if row is None or str(row[0]).lower() != "wal":
                        raise RuntimeError("Session idle index could not enable WAL")
                await connection.execute(
                    """CREATE TABLE IF NOT EXISTS session_idle (
                        account_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        next_check_at_ms INTEGER NOT NULL,
                        registration_version TEXT NOT NULL,
                        PRIMARY KEY (account_id, user_id, session_id)
                    )"""
                )
                await connection.execute(
                    """CREATE INDEX IF NOT EXISTS session_idle_next_check
                    ON session_idle (next_check_at_ms, account_id, user_id, session_id)"""
                )
            except BaseException:
                await connection.close()
                raise
            self._connection = connection
        return self._connection

    async def start(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def report_after_append(self, snapshot: SessionExpirySnapshot) -> None:
        """Best effort registration after the authoritative save, under its lock."""
        try:
            timeout = get_idle_timeout_seconds(snapshot.auto_commit_policy)
            if timeout is None:
                # Existing disabled registrations are removed lazily by the scanner.
                return
            next_check = compute_next_check_at(snapshot.last_message_at, timeout)
            if next_check is None:
                raise ValueError("Session last_message_at is invalid")
            await self.register(
                snapshot.account_id,
                snapshot.user_id,
                snapshot.session_id,
                int(parse_iso_datetime(next_check).timestamp() * 1000),
            )
        except Exception as exc:
            self.record_report_failure("report_failed", exc)

    async def report_after_policy_update(self, snapshot: SessionExpirySnapshot) -> None:
        await self.report_after_append(snapshot)

    def record_report_failure(self, reason: str, exc: Exception | None = None) -> None:
        logger.warning(
            "session idle registration failed reason=%s error_type=%s",
            reason,
            type(exc).__name__ if exc else "unknown",
            extra={"degraded": True, "operation": "session.idle_registration"},
        )

    async def register(
        self, account_id: str, user_id: str, session_id: str, next_check_at_ms: int
    ) -> None:
        async with self._lock:
            connection = await self._connect_locked()
            await connection.execute(
                """INSERT INTO session_idle VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (account_id, user_id, session_id) DO UPDATE SET
                    next_check_at_ms=excluded.next_check_at_ms,
                    registration_version=excluded.registration_version""",
                (account_id, user_id, session_id, next_check_at_ms, uuid4().hex),
            )

    async def due(self, now_ms: int, *, limit: int = 100) -> list[IdleRegistration]:
        async with self._lock:
            connection = await self._connect_locked()
            async with connection.execute(
                """SELECT account_id, user_id, session_id, next_check_at_ms, registration_version
                FROM session_idle WHERE next_check_at_ms <= ?
                ORDER BY next_check_at_ms, account_id, user_id, session_id LIMIT ?""",
                (now_ms, limit),
            ) as cursor:
                return [IdleRegistration(*row) for row in await cursor.fetchall()]

    async def remove(self, registration: IdleRegistration) -> None:
        async with self._lock:
            connection = await self._connect_locked()
            await connection.execute(
                """DELETE FROM session_idle WHERE account_id=? AND user_id=? AND session_id=?
                AND registration_version=?""",
                (*registration.key, registration.registration_version),
            )

    async def defer(self, registration: IdleRegistration, next_check_at_ms: int) -> None:
        async with self._lock:
            connection = await self._connect_locked()
            await connection.execute(
                """UPDATE session_idle SET next_check_at_ms=?
                WHERE account_id=? AND user_id=? AND session_id=? AND registration_version=?""",
                (next_check_at_ms, *registration.key, registration.registration_version),
            )
