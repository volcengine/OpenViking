# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scheduler and executor for opt-in session disk retention (GC).

Periodically scans the canonical per-user session roots, plans deletions
with :mod:`openviking.session.disk_gc`, and executes them exclusively via
``VikingFS.rm(recursive=True)`` so vector index entries are cleaned up in
lockstep with the filesystem. Everything is opt-in and disabled by
default; see ``memory.session_gc`` in the OpenViking config.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from openviking.pyagfs import AsyncAGFSClient
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.session.disk_gc import (
    ARCHIVE_DIR_RE,
    REASON_ARCHIVE_OVER_AGE,
    REASON_SESSION_IDLE_EXPIRED,
    ArchiveInfo,
    SessionDiskState,
    plan_disk_gc,
)
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

AGFS_SCAN_ROOT = "/local"
DEFAULT_INTERVAL_SECS = 3600.0

# (account_id, user_id, session_id) -> bool; awaited before planning.
ActivityChecker = Callable[[str, str, str], Awaitable[bool]]


def _parse_modtime(entry: Dict[str, Any]) -> Optional[datetime]:
    """Best-effort parse of an AGFS entry ``modTime`` into aware UTC."""
    raw = entry.get("modTime", entry.get("mtime", ""))
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = parse_iso_datetime(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _default_activity_checker(account_id: str, user_id: str, session_id: str) -> bool:
    """Treat a session as active while a server-side commit is running."""
    try:
        from openviking.service.task_tracker import get_task_tracker

        return await get_task_tracker().has_running(
            "session_commit",
            session_id,
            account_id=account_id,
            user_id=user_id,
        )
    except Exception:
        # Cannot prove the session is idle -> conservatively treat as active.
        return True


class SessionDiskGcScheduler:
    """Background disk-GC scheduler.

    Mirrors the lifecycle conventions of ``SessionAutoCommitScheduler``
    (start/stop + asyncio task loop) so operators see one consistent
    pattern for periodic session maintenance jobs.
    """

    def __init__(
        self,
        session_service: Any,
        config: Any,
        *,
        interval_secs: Optional[float] = None,
        sleep: Any = asyncio.sleep,
        activity_checker: Optional[ActivityChecker] = None,
    ):
        self._session_service = session_service
        self._config = config
        self._interval = DEFAULT_INTERVAL_SECS if interval_secs is None else float(interval_secs)
        self._sleep = sleep
        self._is_active = activity_checker or _default_activity_checker
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._agfs_client: Optional[AsyncAGFSClient] = None
        self._owners: Dict[str, Tuple[str, str]] = {}

    @property
    def _viking_fs(self) -> Any:
        return self._session_service.viking_fs

    def _get_agfs_client(self) -> AsyncAGFSClient:
        if self._agfs_client is None:
            self._agfs_client = AsyncAGFSClient(self._viking_fs.agfs)
        return self._agfs_client

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info(
            "SessionDiskGcScheduler started with interval %.3fs dry_run=%s",
            self._interval,
            bool(getattr(self._config, "dry_run", False)),
        )
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
                await self._sleep(self._interval)
            except asyncio.CancelledError:
                break

            try:
                if getattr(self._config, "enabled", False):
                    await self.scan_once()
            except Exception as exc:
                logger.error("Session disk-GC scheduler loop failed: %s", exc, exc_info=True)

    async def scan_once(self) -> Dict[str, int]:
        """Run one plan-and-execute pass. Also usable as a manual trigger."""
        now = datetime.now(timezone.utc)
        states = await self._collect_session_states()
        plan = plan_disk_gc(
            states,
            now=now,
            archive_max_age_days=float(getattr(self._config, "archive_max_age_days", 0.0) or 0.0),
            max_idle_days=float(getattr(self._config, "max_idle_days", 0.0) or 0.0),
        )
        dry_run = bool(getattr(self._config, "dry_run", False))
        archives_planned = sum(1 for a in plan if a.reason == REASON_ARCHIVE_OVER_AGE)
        sessions_planned = sum(1 for a in plan if a.reason == REASON_SESSION_IDLE_EXPIRED)
        logger.info(
            "SessionDiskGc plan: sessions_scanned=%d archives_planned=%d "
            "sessions_planned=%d dry_run=%s",
            len(states),
            archives_planned,
            sessions_planned,
            dry_run,
        )

        deleted = 0
        failed = 0
        if dry_run:
            for action in plan:
                logger.info("SessionDiskGc dry-run would delete %s (%s)", action.uri, action.reason)
        else:
            for action in plan:
                ctx = self._ctx_for(action.uri)
                if ctx is None:
                    failed += 1
                    logger.warning(
                        "SessionDiskGc skipped %s (%s): owner unknown", action.uri, action.reason
                    )
                    continue
                try:
                    await self._viking_fs.rm(action.uri, recursive=True, ctx=ctx)
                    deleted += 1
                    logger.info("SessionDiskGc deleted %s (%s)", action.uri, action.reason)
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "SessionDiskGc failed to delete %s (%s): %s",
                        action.uri,
                        action.reason,
                        exc,
                    )
        logger.info("SessionDiskGc run finished: deleted=%d failed=%d", deleted, failed)
        return {
            "sessions_scanned": len(states),
            "archives_planned": archives_planned,
            "sessions_planned": sessions_planned,
            "deleted": deleted,
            "failed": failed,
            "dry_run": 1 if dry_run else 0,
        }

    def _ctx_for(self, uri: str) -> Optional[RequestContext]:
        owner = self._owners.get(uri)
        if owner is None:
            # Archive actions live under the owning session URI.
            for session_uri, candidate in self._owners.items():
                if uri.startswith(session_uri + "/"):
                    owner = candidate
                    break
        if owner is None:
            return None
        account_id, user_id = owner
        return RequestContext(
            user=UserIdentifier(account_id=account_id, user_id=user_id),
            role=Role.USER,
        )

    async def _collect_session_states(self) -> List[SessionDiskState]:
        agfs = self._get_agfs_client()
        self._owners = {}
        states: List[SessionDiskState] = []
        try:
            account_entries = await agfs.ls(AGFS_SCAN_ROOT)
        except Exception:
            logger.warning("SessionDiskGc failed to scan AGFS tree", exc_info=True)
            return states

        for account_entry in account_entries:
            account_id = str(account_entry.get("name") or "").strip()
            if not account_id or account_id == "_system":
                continue
            if not account_entry.get("isDir", False):
                continue
            try:
                user_entries = await agfs.ls(f"/local/{account_id}/user")
            except Exception as exc:
                if not is_not_found_error(exc):
                    logger.warning(
                        "SessionDiskGc failed to scan users under /local/%s",
                        account_id,
                        exc_info=True,
                    )
                continue

            for user_entry in user_entries:
                user_id = str(user_entry.get("name") or "").strip()
                if not user_id or not user_entry.get("isDir", False):
                    continue
                sessions_root = f"/local/{account_id}/user/{user_id}/sessions"
                try:
                    session_entries = await agfs.ls(sessions_root)
                except Exception as exc:
                    if not is_not_found_error(exc):
                        logger.warning(
                            "SessionDiskGc failed to scan sessions: %s",
                            sessions_root,
                            exc_info=True,
                        )
                    continue

                for session_entry in session_entries:
                    session_id = str(session_entry.get("name") or "").strip()
                    if not session_id or not session_entry.get("isDir", False):
                        continue
                    state = await self._collect_session_state(
                        agfs, account_id, user_id, session_id, session_entry
                    )
                    if state is None:
                        continue
                    states.append(state)
                    self._owners[state.uri] = (account_id, user_id)
        return states

    async def _collect_session_state(
        self,
        agfs: AsyncAGFSClient,
        account_id: str,
        user_id: str,
        session_id: str,
        session_entry: Dict[str, Any],
    ) -> Optional[SessionDiskState]:
        session_uri = f"viking://user/{user_id}/sessions/{session_id}"
        session_path = f"/local/{account_id}/user/{user_id}/sessions/{session_id}"

        times = []
        session_mod = _parse_modtime(session_entry)
        if session_mod is not None:
            times.append(session_mod)

        meta_mod = await self._stat_modtime(agfs, f"{session_path}/.meta.json")
        if meta_mod is not None:
            times.append(meta_mod)

        archives: List[ArchiveInfo] = []
        try:
            history_entries = await agfs.ls(f"{session_path}/history")
        except Exception as exc:
            if not is_not_found_error(exc):
                logger.debug(
                    "SessionDiskGc failed to list history: %s", session_path, exc_info=True
                )
            history_entries = []
        for entry in history_entries:
            name = str(entry.get("name") or "").strip()
            if not name or not ARCHIVE_DIR_RE.fullmatch(name):
                continue
            if not entry.get("isDir", False):
                continue
            archive = ArchiveInfo(name=name, mtime=_parse_modtime(entry))
            archives.append(archive)
            if archive.mtime is not None:
                times.append(archive.mtime)

        last_activity = max(times) if times else None
        try:
            activity = self._is_active(account_id, user_id, session_id)
            if asyncio.iscoroutine(activity):
                activity = await activity
            is_active = bool(activity)
        except Exception:
            # Cannot prove the session is idle -> conservatively keep it.
            is_active = True

        return SessionDiskState(
            uri=session_uri,
            session_id=session_id,
            last_activity=last_activity,
            is_active=is_active,
            archives=tuple(archives),
        )

    @staticmethod
    async def _stat_modtime(agfs: AsyncAGFSClient, path: str) -> Optional[datetime]:
        try:
            entry = await agfs.stat(path)
        except Exception as exc:
            if not is_not_found_error(exc):
                logger.debug("SessionDiskGc stat failed: %s", path, exc_info=True)
            return None
        if not isinstance(entry, dict):
            return None
        return _parse_modtime(entry)
