# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime helpers for server-side automatic session commits."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from openviking.server.identity import RequestContext, Role
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

SESSION_AUTO_COMMIT_INDEX_URI = "/local/_system/session_auto_commit/index.json"
SESSION_AUTO_COMMIT_INDEX_TMP_URI = "/local/_system/session_auto_commit/index.json.tmp"
SESSION_AUTO_COMMIT_INDEX_BAK_URI = "/local/_system/session_auto_commit/index.json.bak"


@dataclass(frozen=True)
class IndexedSession:
    account_id: str
    user_id: str
    session_id: str
    next_check_at: str


class SessionAutoCommitIndex:
    """Persistent index of active idle auto-commit candidates."""

    def __init__(self, viking_fs: Any):
        self._viking_fs = viking_fs
        self._lock = asyncio.Lock()
        self._index_data: Dict[str, Any] = {"meta": {"updated_at": ""}, "data": {}}
        self._initialized = False
        self._ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            self._index_data = await self._load_index()
            self._initialized = True

    async def list_sessions(self) -> List[IndexedSession]:
        await self.initialize()
        async with self._lock:
            return list(_iter_indexed_sessions(self._index_data))

    async def get_next_due_sessions(self, now: datetime) -> List[IndexedSession]:
        await self.initialize()
        due: List[IndexedSession] = []
        async with self._lock:
            for item in _iter_indexed_sessions(self._index_data):
                try:
                    next_dt = datetime.fromisoformat(item.next_check_at)
                except Exception:
                    continue
                if next_dt <= now:
                    due.append(item)
        return due

    async def upsert_session(
        self,
        account_id: str,
        user_id: str,
        session_id: str,
        *,
        next_check_at: str,
    ) -> None:
        await self.initialize()
        async with self._lock:
            data = _clone_index_data(self._index_data)
            session_node = (
                data.setdefault("data", {}).setdefault(account_id, {}).setdefault(user_id, {})
            )
            session_node[session_id] = {"next_check_at": next_check_at}
            data.setdefault("meta", {})["updated_at"] = get_current_timestamp()
            await self._persist_locked(data)

    async def remove_session(self, account_id: str, user_id: str, session_id: str) -> None:
        await self.initialize()
        async with self._lock:
            data = _clone_index_data(self._index_data)
            users = data.get("data", {}).get(account_id)
            if not isinstance(users, dict):
                return
            sessions = users.get(user_id)
            if not isinstance(sessions, dict) or session_id not in sessions:
                return
            sessions.pop(session_id, None)
            if not sessions:
                users.pop(user_id, None)
            if not users:
                data.get("data", {}).pop(account_id, None)
            data.setdefault("meta", {})["updated_at"] = get_current_timestamp()
            await self._persist_locked(data)

    async def _load_index(self) -> Dict[str, Any]:
        data = await self._read_index_file(SESSION_AUTO_COMMIT_INDEX_URI)
        if data is None:
            data = await self._read_index_file(SESSION_AUTO_COMMIT_INDEX_BAK_URI)
        if not isinstance(data, dict):
            return {"meta": {"updated_at": ""}, "data": {}}
        if not isinstance(data.get("meta"), dict):
            data["meta"] = {"updated_at": ""}
        if not isinstance(data.get("data"), dict):
            data["data"] = {}
        return data

    async def _read_index_file(self, uri: str) -> Optional[Dict[str, Any]]:
        try:
            content = await self._viking_fs.read_file(uri, ctx=self._ctx)
        except NotFoundError:
            return None
        except Exception as exc:
            logger.warning("Failed to read session auto-commit index %s: %s", uri, exc)
            return None
        if not content or not content.strip():
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid session auto-commit index JSON in %s: %s", uri, exc)
            return None

    async def _persist_locked(self, data: Dict[str, Any]) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        json.loads(content)

        await self._viking_fs.write_file(SESSION_AUTO_COMMIT_INDEX_TMP_URI, content, ctx=self._ctx)
        try:
            if await self._viking_fs.exists(SESSION_AUTO_COMMIT_INDEX_BAK_URI, ctx=self._ctx):
                await self._viking_fs.rm(SESSION_AUTO_COMMIT_INDEX_BAK_URI, ctx=self._ctx)
        except Exception:
            pass
        try:
            if await self._viking_fs.exists(SESSION_AUTO_COMMIT_INDEX_URI, ctx=self._ctx):
                await self._viking_fs.mv(
                    SESSION_AUTO_COMMIT_INDEX_URI,
                    SESSION_AUTO_COMMIT_INDEX_BAK_URI,
                    ctx=self._ctx,
                )
        except Exception as exc:
            logger.warning("Failed to rotate session auto-commit index backup: %s", exc)
        await self._viking_fs.mv(
            SESSION_AUTO_COMMIT_INDEX_TMP_URI,
            SESSION_AUTO_COMMIT_INDEX_URI,
            ctx=self._ctx,
        )
        self._index_data = data


class SessionAutoCommitScheduler:
    """Scheduler for idle-based automatic session commits."""

    DEFAULT_CHECK_INTERVAL = 60.0

    def __init__(
        self, session_service: Any, config: Any, *, check_interval: float = DEFAULT_CHECK_INTERVAL
    ):
        self._session_service = session_service
        self._config = config
        self._check_interval = check_interval
        self._index: Optional[SessionAutoCommitIndex] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def index(self) -> Optional[SessionAutoCommitIndex]:
        return self._index

    async def start(self) -> None:
        if self._running:
            return
        self._index = SessionAutoCommitIndex(self._session_service.viking_fs)
        await self._index.initialize()
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                if self._config.idle_enabled and self._index is not None:
                    due = await self._index.get_next_due_sessions(datetime.now())
                    for item in due:
                        ctx = RequestContext(
                            user=UserIdentifier(account_id=item.account_id, user_id=item.user_id),
                            role=Role.USER,
                        )
                        await self._session_service.maybe_schedule_auto_commit(
                            item.session_id,
                            ctx,
                            reason_hint="idle_timeout",
                        )
            except Exception as exc:
                logger.error("Session auto-commit scheduler loop failed: %s", exc, exc_info=True)
            try:
                await asyncio.sleep(self._check_interval)
            except asyncio.CancelledError:
                break


def should_enable_auto_commit(policy: Optional[Dict[str, Any]]) -> bool:
    return bool(isinstance(policy, dict) and policy.get("enabled") is True)


def get_idle_timeout_seconds(policy: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(policy, dict):
        return None
    value = policy.get("idle_timeout_seconds")
    if value is None:
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def get_token_threshold(policy: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(policy, dict):
        return None
    value = policy.get("token_threshold")
    if value is None:
        return None
    try:
        threshold = int(value)
    except (TypeError, ValueError):
        return None
    return threshold if threshold >= 0 else None


def compute_next_check_at(last_message_at: str, idle_timeout_seconds: int) -> Optional[str]:
    if not last_message_at:
        return None
    try:
        base = datetime.fromisoformat(last_message_at)
    except Exception:
        return None
    return (base + timedelta(seconds=idle_timeout_seconds)).isoformat()


def _clone_index_data(data: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False))


def _iter_indexed_sessions(index_data: Dict[str, Any]) -> List[IndexedSession]:
    results: List[IndexedSession] = []
    data = index_data.get("data", {})
    if not isinstance(data, dict):
        return results
    for account_id, users in data.items():
        if not isinstance(users, dict):
            continue
        for user_id, sessions in users.items():
            if not isinstance(sessions, dict):
                continue
            for session_id, payload in sessions.items():
                if not isinstance(payload, dict):
                    continue
                next_check_at = payload.get("next_check_at")
                if not isinstance(next_check_at, str) or not next_check_at:
                    continue
                results.append(
                    IndexedSession(
                        account_id=account_id,
                        user_id=user_id,
                        session_id=session_id,
                        next_check_at=next_check_at,
                    )
                )
    return results
