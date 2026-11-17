# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Periodic cleanup for rules that have remained archived past retention."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from openviking.pyagfs import AsyncAGFSClient
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

AGFS_ACCOUNT_ROOT = "/local"


class RuleCleanupScheduler:
    """Scan user rule libraries and remove expired archived rules."""

    def __init__(
        self,
        rule_service: Any,
        viking_fs: Any,
        config: Any,
        *,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._rule_service = rule_service
        self._viking_fs = viking_fs
        self._config = config
        self._interval = float(config.archived_cleanup_interval_seconds)
        self._sleep = sleep
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._agfs_client: AsyncAGFSClient | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "RuleCleanupScheduler started with interval %.3fs and retention %d days",
            self._interval,
            self._config.archived_retention_days,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            started = time.monotonic()
            try:
                await self._scan_once()
            except Exception as exc:
                logger.info(
                    "Archived-rule cleanup scan failed; retrying next interval",
                    extra={"error_type": type(exc).__name__},
                )
            wait = self._interval - (time.monotonic() - started)
            if wait <= 0:
                continue
            try:
                await self._sleep(wait)
            except asyncio.CancelledError:
                break

    async def _scan_once(self) -> None:
        users = 0
        deleted = 0
        failed = 0
        async for account_id, user_id in self._iter_rule_users():
            users += 1
            ctx = RequestContext(
                user=UserIdentifier(account_id=account_id, user_id=user_id),
                role=Role(Role.USER),
            )
            try:
                result = await self._rule_service.cleanup_archived_rules(ctx=ctx)
            except Exception as exc:
                failed += 1
                logger.debug(
                    "Archived-rule cleanup failed for a user",
                    extra={
                        "account_id": account_id,
                        "user_id": user_id,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            deleted += len(result["deleted"])
            failed += len(result["failed"])
        if deleted or failed:
            logger.info(
                "RuleCleanupScheduler users=%d deleted=%d failed=%d",
                users,
                deleted,
                failed,
            )

    async def _iter_rule_users(self) -> AsyncIterator[tuple[str, str]]:
        agfs = self._get_agfs_client()
        try:
            account_entries = await agfs.ls(AGFS_ACCOUNT_ROOT)
        except Exception as exc:
            logger.info(
                "Archived-rule account discovery failed; retrying next interval",
                extra={"error_type": type(exc).__name__},
            )
            return

        failed_accounts = 0
        failed_libraries = 0
        for account_entry in account_entries:
            account_id = str(account_entry.get("name") or "").strip()
            if not account_id or account_id == "_system" or not account_entry.get("isDir", False):
                continue
            users_root = f"{AGFS_ACCOUNT_ROOT}/{account_id}/user"
            try:
                user_entries = await agfs.ls(users_root)
            except Exception as exc:
                if not is_not_found_error(exc):
                    failed_accounts += 1
                    logger.debug(
                        "Archived-rule user discovery failed",
                        extra={"error_type": type(exc).__name__},
                    )
                continue

            for user_entry in user_entries:
                user_id = str(user_entry.get("name") or "").strip()
                if not user_id or not user_entry.get("isDir", False):
                    continue
                rules_root = f"{users_root}/{user_id}/memories/rules"
                try:
                    stat = await agfs.stat(rules_root)
                except Exception as exc:
                    if not is_not_found_error(exc):
                        failed_libraries += 1
                        logger.debug(
                            "Archived-rule library inspection failed",
                            extra={"error_type": type(exc).__name__},
                        )
                    continue
                if isinstance(stat, dict) and stat.get("isDir", False):
                    yield account_id, user_id

        if failed_accounts or failed_libraries:
            logger.info(
                "Archived-rule cleanup discovery completed with errors account_failures=%d library_failures=%d",
                failed_accounts,
                failed_libraries,
                extra={"failed": failed_accounts + failed_libraries},
            )

    def _get_agfs_client(self) -> AsyncAGFSClient:
        if self._agfs_client is None:
            self._agfs_client = AsyncAGFSClient(self._viking_fs.agfs)
        return self._agfs_client
