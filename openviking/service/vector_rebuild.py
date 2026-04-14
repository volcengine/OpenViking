# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Full-account vector rebuild helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

INDEXABLE_SCOPE_ROOTS = (
    "viking://resources",
    "viking://user",
    "viking://agent",
    "viking://session",
)


@dataclass
class AccountVectorRebuildReport:
    """Summary for a rebuilt account."""

    account_id: str
    deleted_records: int
    indexed_directories: int
    queue_status: dict[str, Any]


class VectorRebuildService:
    """Rebuild vectors by deleting account-scoped records and reindexing AGFS content."""

    def __init__(self, service, *, ls_node_limit: int = 100_000):
        self._service = service
        self._ls_node_limit = ls_node_limit

    def _root_ctx(self, account_id: str) -> RequestContext:
        return RequestContext(
            user=UserIdentifier(account_id, "system", "system"),
            role=Role.ROOT,
        )

    async def discover_accounts(self) -> list[str]:
        """Discover accounts present in the local workspace."""
        viking_fs = self._service.viking_fs
        if viking_fs is None:
            raise RuntimeError("VikingFS not initialized")

        try:
            entries = await viking_fs.list_account_roots()
        except AttributeError:
            entries = viking_fs.agfs.ls("/local")

        accounts = [
            entry.get("name", "")
            for entry in entries
            if entry.get("isDir") and entry.get("name")
        ]
        return sorted(set(accounts))

    async def _discover_directories(self, root_uri: str, ctx: RequestContext) -> list[str]:
        viking_fs = self._service.viking_fs
        if viking_fs is None:
            raise RuntimeError("VikingFS not initialized")

        if not await viking_fs.exists(root_uri, ctx=ctx):
            return []

        discovered: list[str] = []
        seen: set[str] = set()
        stack = [root_uri]
        while stack:
            current_uri = stack.pop()
            if current_uri in seen:
                continue
            seen.add(current_uri)
            discovered.append(current_uri)

            try:
                entries = await viking_fs.ls(
                    current_uri,
                    output="original",
                    show_all_hidden=False,
                    node_limit=self._ls_node_limit,
                    ctx=ctx,
                )
            except Exception as exc:
                logger.warning("Failed to enumerate %s during vector rebuild: %s", current_uri, exc)
                continue

            child_dirs = [
                entry.get("uri")
                for entry in entries
                if entry.get("isDir") and isinstance(entry.get("uri"), str)
            ]
            stack.extend(sorted(child_dirs, reverse=True))

        return discovered

    async def rebuild_account(
        self,
        account_id: str,
        *,
        wait_timeout: Optional[float] = None,
    ) -> AccountVectorRebuildReport:
        """Delete all vectors for one account and rebuild from AGFS content."""
        if self._service.vikingdb_manager is None:
            raise RuntimeError("VikingDBManager not initialized")

        ctx = self._root_ctx(account_id)
        deleted_records = await self._service.vikingdb_manager.delete_account_data(
            account_id, ctx=ctx
        )

        directories: list[str] = []
        for root_uri in INDEXABLE_SCOPE_ROOTS:
            directories.extend(await self._discover_directories(root_uri, ctx))

        ordered_directories = list(dict.fromkeys(directories))
        for uri in ordered_directories:
            await self._service.resources.build_index([uri], ctx=ctx)

        queue_status = await self._service.resources.wait_processed(timeout=wait_timeout)
        return AccountVectorRebuildReport(
            account_id=account_id,
            deleted_records=deleted_records,
            indexed_directories=len(ordered_directories),
            queue_status=queue_status,
        )

    async def rebuild_accounts(
        self,
        account_ids: Optional[list[str]] = None,
        *,
        wait_timeout: Optional[float] = None,
    ) -> list[AccountVectorRebuildReport]:
        """Rebuild one or more accounts."""
        accounts = account_ids or await self.discover_accounts()
        reports: list[AccountVectorRebuildReport] = []
        for account_id in accounts:
            reports.append(await self.rebuild_account(account_id, wait_timeout=wait_timeout))
        return reports
