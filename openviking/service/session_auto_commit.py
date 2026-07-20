# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Runtime helpers for server-side automatic session commits."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from openviking.pyagfs import AsyncAGFSClient
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking.session.auto_commit_policy import AutoCommitPolicy
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

SESSION_META_SUFFIX = "/.meta.json"
AGFS_SESSION_SCAN_ROOT = "/local"


class SessionAutoCommitScheduler:
    """Scheduler for idle-based automatic session commits."""

    DEFAULT_CHECK_INTERVAL = 60.0

    def __init__(
        self,
        session_service: Any,
        config: Any,
        *,
        check_interval: Optional[float] = None,
        sleep: Any = asyncio.sleep,
    ):
        self._session_service = session_service
        self._config = config
        self._check_interval = (
            self.DEFAULT_CHECK_INTERVAL if check_interval is None else float(check_interval)
        )
        self._scan_batch_size = max(1, int(getattr(config, "scan_batch_size", 16) or 16))
        self._scan_batch_pause_seconds = max(
            0.0,
            float(getattr(config, "scan_batch_pause_seconds", 0.0) or 0.0),
        )
        self._sleep = sleep
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._agfs_client: Optional[AsyncAGFSClient] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info(
            "SessionAutoCommitScheduler started with check interval %.3fs", self._check_interval
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
                await self._sleep(self._check_interval)
            except asyncio.CancelledError:
                break

            try:
                if self._config.idle_enabled:
                    await self._scan_once()
            except Exception as exc:
                logger.error("Session auto-commit scheduler loop failed: %s", exc, exc_info=True)

    async def _scan_once(self) -> None:
        now = datetime.now()
        scanned = 0
        due = 0
        scheduled = 0
        agfs = self._get_agfs_client()
        async for batch in self._iter_session_meta_path_batches(agfs):
            batch_scanned, batch_due, batch_scheduled = await self._process_meta_batch(
                agfs, batch, now
            )
            scanned += batch_scanned
            due += batch_due
            scheduled += batch_scheduled

        if due > 0:
            logger.info(
                "SessionAutoCommitScheduler scanned=%d due=%d scheduled=%d",
                scanned,
                due,
                scheduled,
            )

    def _get_agfs_client(self) -> AsyncAGFSClient:
        if self._agfs_client is None:
            self._agfs_client = AsyncAGFSClient(self._session_service.viking_fs.agfs)
        return self._agfs_client

    async def _process_meta_batch(
        self,
        agfs: AsyncAGFSClient,
        batch: list[str],
        now: datetime,
    ) -> tuple[int, int, int]:
        results = await asyncio.gather(
            *(self._read_idle_candidate(agfs, meta_path, now) for meta_path in batch)
        )
        due = 0
        scheduled = 0
        for item in results:
            if item is None:
                continue
            session_id, account_id, user_id = item
            due += 1
            ctx = RequestContext(
                user=UserIdentifier(account_id=account_id, user_id=user_id),
                role=Role.USER,
            )
            did_schedule = await self._session_service.maybe_schedule_auto_commit(
                session_id,
                ctx,
                reason_hint="idle_timeout",
            )
            if did_schedule:
                scheduled += 1
        return len(batch), due, scheduled

    async def _read_idle_candidate(
        self,
        agfs: AsyncAGFSClient,
        meta_path: str,
        now: datetime,
    ) -> Optional[tuple[str, str, str]]:
        try:
            content = await agfs.read(meta_path)
            raw = content.decode("utf-8") if isinstance(content, bytes) else str(content)
            meta = json.loads(raw)
            if not isinstance(meta, dict):
                logger.warning(
                    "Invalid session meta object for idle auto-commit: %s (%s)",
                    meta_path,
                    type(meta).__name__,
                )
                return None
        except json.JSONDecodeError as exc:
            logger.warning(
                "Invalid session meta JSON for idle auto-commit: %s (%s)",
                meta_path,
                exc,
            )
            return None
        except Exception as exc:
            if is_not_found_error(exc):
                logger.debug(
                    "Session meta disappeared during idle auto-commit scan: %s",
                    meta_path,
                )
                return None
            logger.warning(
                "Failed to read session meta for idle auto-commit: %s",
                meta_path,
                exc_info=True,
            )
            return None

        try:
            if not _is_idle_candidate(meta, now):
                return None
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Invalid session meta fields for idle auto-commit: %s (%s)",
                meta_path,
                exc,
            )
            return None

        session_id = _session_id_from_meta_path(meta_path)
        account_id = _account_id_from_meta_path(meta_path)
        user_id = _user_id_from_meta_path(meta_path)
        if not session_id or not account_id or not user_id:
            return None
        return session_id, account_id, user_id

    async def _iter_session_meta_path_batches(
        self, agfs: AsyncAGFSClient
    ) -> AsyncIterator[list[str]]:
        try:
            account_entries = await agfs.ls(AGFS_SESSION_SCAN_ROOT)
        except Exception:
            logger.warning("Failed to scan AGFS tree for idle auto-commit", exc_info=True)
            return

        batch: list[str] = []
        seen: set[str] = set()
        for account_entry in account_entries:
            account_id = str(account_entry.get("name") or "").strip()
            if not account_id or account_id == "_system":
                continue
            if not account_entry.get("isDir", False):
                continue

            try:
                user_entries = await agfs.ls(f"/local/{account_id}/user")
            except Exception as exc:
                if is_not_found_error(exc):
                    logger.debug(
                        "Account user directory missing during idle auto-commit scan: %s",
                        account_id,
                    )
                else:
                    logger.warning(
                        "Failed to scan users for idle auto-commit: /local/%s/user",
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
                    if is_not_found_error(exc):
                        logger.debug(
                            "User sessions directory missing during idle auto-commit scan: %s",
                            sessions_root,
                        )
                    else:
                        logger.warning(
                            "Failed to scan sessions for idle auto-commit: %s",
                            sessions_root,
                            exc_info=True,
                        )
                    continue

                for session_entry in session_entries:
                    session_id = str(session_entry.get("name") or "").strip()
                    if not session_id or not session_entry.get("isDir", False):
                        continue
                    meta_path = f"{sessions_root}/{session_id}{SESSION_META_SUFFIX}"
                    if meta_path not in seen:
                        seen.add(meta_path)
                        batch.append(meta_path)
                    if len(batch) >= self._scan_batch_size:
                        yield batch
                        batch = []
                        if self._scan_batch_pause_seconds > 0:
                            await self._sleep(self._scan_batch_pause_seconds)
        if batch:
            yield batch


def resolve_policy(policy: Optional[Dict[str, Any]]) -> AutoCommitPolicy:
    """Resolve a stored policy dict into an effective policy (defaults filled)."""
    return AutoCommitPolicy.from_dict(policy if isinstance(policy, dict) else None)


def get_idle_timeout_seconds(policy: Optional[Dict[str, Any]]) -> Optional[int]:
    if policy is None:
        return None
    seconds = resolve_policy(policy).idle_timeout_seconds
    return seconds if seconds > 0 else None


def get_token_threshold(policy: Optional[Dict[str, Any]]) -> Optional[int]:
    if policy is None:
        return None
    threshold = resolve_policy(policy).pending_token_threshold
    return threshold if threshold > 0 else None


def get_message_count_threshold(policy: Optional[Dict[str, Any]]) -> Optional[int]:
    if policy is None:
        return None
    threshold = resolve_policy(policy).message_count_threshold
    return threshold if threshold > 0 else None


def get_min_commit_interval_seconds(policy: Optional[Dict[str, Any]]) -> int:
    if policy is None:
        return 0
    return max(0, resolve_policy(policy).min_commit_interval_seconds)


def get_keep_recent_count(policy: Optional[Dict[str, Any]]) -> int:
    if policy is None:
        return 0
    return max(0, resolve_policy(policy).keep_recent_count)


def compute_next_check_at(last_message_at: str, idle_timeout_seconds: int) -> Optional[str]:
    if not last_message_at:
        return None
    try:
        base = parse_iso_datetime(last_message_at)
    except Exception:
        return None
    return (base + timedelta(seconds=idle_timeout_seconds)).isoformat()


def is_next_check_due(next_check_at: str, now: datetime) -> Optional[bool]:
    try:
        next_dt = parse_iso_datetime(next_check_at)
    except Exception:
        return None

    compare_now = now
    if next_dt.tzinfo is not None:
        if compare_now.tzinfo is None:
            compare_now = datetime.fromtimestamp(compare_now.timestamp(), tz=next_dt.tzinfo)
        else:
            compare_now = compare_now.astimezone(next_dt.tzinfo)
    elif compare_now.tzinfo is not None:
        compare_now = compare_now.replace(tzinfo=None)

    return next_dt <= compare_now


def has_uncommitted_content(meta: Dict[str, Any]) -> bool:
    keep_recent_count = _coerce_non_negative_int(meta.get("keep_recent_count", 0))
    return bool(
        _coerce_non_negative_int(meta.get("pending_tokens", 0)) > 0
        or _coerce_non_negative_int(meta.get("message_count", 0)) > keep_recent_count
    )


def _is_idle_policy_due(meta: Dict[str, Any], now: datetime) -> bool:
    idle_timeout = get_idle_timeout_seconds(meta.get("auto_commit_policy"))
    if idle_timeout is None:
        return False
    if not has_uncommitted_content(meta):
        return False
    next_check_at = compute_next_check_at(meta.get("last_message_at", ""), idle_timeout)
    if not next_check_at:
        return False
    return is_next_check_due(next_check_at, now) is True


def _coerce_non_negative_int(value: Any) -> int:
    parsed = int(value or 0)
    return max(0, parsed)


def _is_idle_candidate(meta: Dict[str, Any], now: datetime) -> bool:
    return _is_idle_policy_due(meta, now)


def _session_id_from_meta_path(meta_path: str) -> str:
    if not meta_path.endswith(SESSION_META_SUFFIX):
        return ""
    parts = [part for part in meta_path.split("/") if part]
    if len(parts) >= 6 and parts[0] == "local" and parts[2] == "user" and parts[4] == "sessions":
        return parts[5]
    return ""


def _account_id_from_meta_path(meta_path: str) -> str:
    parts = [part for part in meta_path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "local":
        return parts[1]
    return ""


def _user_id_from_meta_path(meta_path: str) -> str:
    parts = [part for part in meta_path.split("/") if part]
    if len(parts) >= 6 and parts[0] == "local" and parts[2] == "user" and parts[4] == "sessions":
        return parts[3]
    return ""
