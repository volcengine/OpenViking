# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reconcile packaged system assets across all active accounts."""

import asyncio
import json
import random
from collections.abc import Collection
from typing import Any, Protocol

from openviking.pyagfs import AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.server.account_stores.models import AccountSummary
from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import ResourceBusyError
from openviking.system_assets.sync import SystemAssetSynchronizer
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_SYNC_MAX_RETRIES = 6
_SYNC_RETRY_BASE_DELAY_SECONDS = 0.2
_SYNC_RETRY_MAX_DELAY_SECONDS = 2.0

_SYSTEM_ASSET_MANIFEST_PATH = "/local/_system/system_assets_manifest.json"
_RECONCILE_LOCK_TIMEOUT_SECONDS = 300.0


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

        lease = await self._agfs.pathlock_acquire_exact(
            _SYSTEM_ASSET_MANIFEST_PATH,
            timeout_secs=_RECONCILE_LOCK_TIMEOUT_SECONDS,
        )
        try:
            if await self._read_manifest_digest() == digest:
                return 0
            synced_accounts = await self.sync_all()
            await self._write_manifest(manifest, lease)
            return synced_accounts
        finally:
            await self._agfs.pathlock_release(lease)

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

    async def _read_manifest_digest(self) -> str | None:
        fs_ctx = fs_ctx_from_agfs_path(_SYSTEM_ASSET_MANIFEST_PATH)
        fs_ctx["bypass_cache"] = "true"
        try:
            result = await self._agfs.read(_SYSTEM_ASSET_MANIFEST_PATH, fs_ctx=fs_ctx)
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
        fs_ctx = _lease_fs_ctx(_SYSTEM_ASSET_MANIFEST_PATH, lease)
        await self._agfs.ensure_parent_dirs(_SYSTEM_ASSET_MANIFEST_PATH, fs_ctx=fs_ctx)
        await self._agfs.write(
            _SYSTEM_ASSET_MANIFEST_PATH,
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
        for retry in range(_SYNC_MAX_RETRIES + 1):
            try:
                async with self._semaphore:
                    return await self._synchronizer.sync(ctx)
            except ResourceBusyError:
                if retry == _SYNC_MAX_RETRIES:
                    raise
                delay = min(
                    _SYNC_RETRY_BASE_DELAY_SECONDS * (2**retry),
                    _SYNC_RETRY_MAX_DELAY_SECONDS,
                ) * random.uniform(0.8, 1.2)
                logger.warning(
                    "System assets busy for account %s; retry %d/%d in %.2fs",
                    account_id,
                    retry + 1,
                    _SYNC_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

        raise AssertionError("unreachable")

def _lease_fs_ctx(path: str, lease: Any) -> dict[str, str]:
    fs_ctx = fs_ctx_from_agfs_path(path)
    lease_ref = getattr(lease, "lease_ref", None) or getattr(lease, "id", None)
    if isinstance(lease, dict):
        lease_ref = lease.get("lease_ref", lease_ref)
    if isinstance(lease_ref, str) and lease_ref:
        fs_ctx["lease_ref"] = lease_ref
    return fs_ctx
