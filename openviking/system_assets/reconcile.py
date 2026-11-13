# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reconcile packaged system assets across all active accounts."""

import asyncio
import json
from collections.abc import Collection
from typing import Any, Protocol

from openviking.pyagfs import AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.server.account_stores.models import AccountSummary
from openviking.server.identity import RequestContext, Role
from openviking.system_assets.sync import (
    SYSTEM_ASSET_MANIFEST_PATH,
    SystemAssetSynchronizer,
    system_asset_sync_lock,
)
from openviking_cli.session.user_id import UserIdentifier


class AccountLister(Protocol):
    async def list_accounts(self, **filters) -> list[AccountSummary]: ...


class SystemAssetReconciler:
    """Coordinate bounded, paginated system-asset sync for active accounts."""

    def __init__(
        self,
        synchronizer: SystemAssetSynchronizer,
        account_lister: AccountLister,
        agfs: AsyncAGFSClient,
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
        self._agfs = agfs
        self._user_id = user_id
        self._skip_account_ids = frozenset(skip_account_ids or ())
        self._page_size = page_size
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def sync_if_changed(self) -> int:
        manifest = self._synchronizer.manifest
        digest = self._synchronizer.manifest_digest
        if await self._read_manifest_digest() == digest:
            return 0

        async with system_asset_sync_lock(self._agfs) as lease:
            if await self._read_manifest_digest() == digest:
                return 0
            synced_accounts = await self._sync_all()
            await self._write_manifest(manifest, lease)
            return synced_accounts

    async def _sync_all(self) -> int:
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

    async def _read_manifest_digest(self) -> str | None:
        fs_ctx = fs_ctx_from_agfs_path(SYSTEM_ASSET_MANIFEST_PATH)
        fs_ctx["bypass_cache"] = "true"
        try:
            result = await self._agfs.read(SYSTEM_ASSET_MANIFEST_PATH, fs_ctx=fs_ctx)
        except (AGFSNotFoundError, FileNotFoundError):
            return None

        raw = result if isinstance(result, bytes) else getattr(result, "content", result)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        digest = payload.get("sha256") if isinstance(payload, dict) else None
        return digest if isinstance(digest, str) else None

    async def _write_manifest(self, manifest: bytes, lease: Any) -> None:
        fs_ctx = _lease_fs_ctx(SYSTEM_ASSET_MANIFEST_PATH, lease)
        await self._agfs.ensure_parent_dirs(SYSTEM_ASSET_MANIFEST_PATH, fs_ctx=fs_ctx)
        await self._agfs.write(
            SYSTEM_ASSET_MANIFEST_PATH,
            manifest,
            fs_ctx=fs_ctx,
            auto_pathlock=False,
        )

    async def _sync_account(self, account: AccountSummary) -> int:
        account_id = account.get("account_id")
        if not account_id:
            raise ValueError("account summary is missing account_id")
        ctx = RequestContext(
            user=UserIdentifier(account_id, self._user_id),
            role=Role(Role.ROOT),
        )
        async with self._semaphore:
            return await self._synchronizer.sync_under_lock(ctx)


def _lease_fs_ctx(path: str, lease: Any) -> dict[str, str]:
    fs_ctx = fs_ctx_from_agfs_path(path)
    lease_ref = getattr(lease, "lease_ref", None) or getattr(lease, "id", None)
    if isinstance(lease, dict):
        lease_ref = lease.get("lease_ref", lease_ref)
    if isinstance(lease_ref, str) and lease_ref:
        fs_ctx["lease_ref"] = lease_ref
    return fs_ctx
