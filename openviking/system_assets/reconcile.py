# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reconcile packaged system assets across all active accounts."""

import asyncio
from collections.abc import Collection
from typing import Protocol

from openviking.server.account_stores.models import AccountSummary
from openviking.server.identity import RequestContext, Role
from openviking.system_assets.sync import SystemAssetSynchronizer
from openviking_cli.session.user_id import UserIdentifier


class AccountLister(Protocol):
    async def list_accounts(self, **filters) -> list[AccountSummary]: ...


class SystemAssetReconciler:
    """Coordinate bounded, paginated system-asset sync for active accounts."""

    def __init__(
        self,
        synchronizer: SystemAssetSynchronizer,
        account_lister: AccountLister,
        *,
        user_id: str,
        skip_account_ids: Collection[str] | None = None,
        page_size: int = 100,
        max_concurrency: int = 4,
    ) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._synchronizer = synchronizer
        self._account_lister = account_lister
        self._user_id = user_id
        self._skip_account_ids = frozenset(skip_account_ids or ())
        self._page_size = page_size
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def sync_all(self) -> int:
        synced_accounts = 0
        page = 1
        while True:
            accounts = await self._account_lister.list_accounts(
                limit=self._page_size,
                page=page,
            )
            active_accounts = [
                account
                for account in accounts
                if account.get("status", "active") == "active"
                and account.get("account_id") not in self._skip_account_ids
            ]
            results = await asyncio.gather(
                *(self._sync_account(account) for account in active_accounts),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    raise result
            failures = [
                (account.get("account_id") or "<missing>", result)
                for account, result in zip(active_accounts, results, strict=True)
                if isinstance(result, Exception)
            ]
            if failures:
                details = ", ".join(
                    f"{account_id}: {type(error).__name__}: {error}"
                    for account_id, error in failures
                )
                raise RuntimeError(
                    f"system asset reconciliation failed for {len(failures)} account(s): {details}"
                ) from failures[0][1]

            synced_accounts += len(active_accounts)
            if len(accounts) < self._page_size:
                break
            page += 1
        return synced_accounts

    async def _sync_account(self, account: AccountSummary) -> int:
        account_id = account.get("account_id")
        if not account_id:
            raise ValueError("account summary is missing account_id")
        async with self._semaphore:
            return await self._synchronizer.sync(
                RequestContext(
                    user=UserIdentifier(account_id, self._user_id),
                    role=Role(Role.ROOT),
                )
            )
