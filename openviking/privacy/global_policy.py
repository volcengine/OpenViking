# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Process-wide lifecycle for user-controlled GLOBAL access policies.

Python reads the policy source through the AGFS client with the owner's account
context and asks the native parser to interpret it. This module keeps one
snapshot per owner and owns the shared TTL and FIFO eviction policy.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Optional

from openviking.core.global_policy import GlobalAccessPolicy, global_policy_from_source
from openviking.core.namespace import owner_fields_for_uri, relative_uri_path
from openviking.server.error_mapping import is_not_found_error
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier

if TYPE_CHECKING:
    from openviking.storage.viking_fs import VikingFS

LOGGER = logging.getLogger(__name__)

POLICY_CACHE_TTL_SECONDS = 600
POLICY_CACHE_MAX_ENTRIES = 10_000
_POLICY_URI = "viking://user/{owner_user_id}/GLOBAL_POLICY.md"


@dataclass(frozen=True)
class _CacheEntry:
    policy: GlobalAccessPolicy
    loaded_at: float


class UserGlobalPolicyService:
    """Owner-scoped registry with a fixed TTL and FIFO eviction.

    A policy belongs to the owner of the user root, not to the actor reading it.
    Entries are therefore keyed by ``(account_id, owner_user_id)``. Missing,
    unreadable or invalid policies deny access through the fail-closed evaluator.
    """

    def __init__(self, viking_fs: "VikingFS"):
        self._viking_fs = viking_fs
        self._policies: OrderedDict[tuple[str, str], _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def for_owner(
        self,
        ctx: RequestContext,
        owner_user_id: Optional[str] = None,
    ) -> GlobalAccessPolicy:
        """Return the snapshot for one owner, reloading it after the TTL."""
        owner = ctx.user.user_id if owner_user_id is None else owner_user_id
        key = (ctx.account_id, owner)
        with self._lock:
            now = monotonic()
            entry = self._policies.get(key)
            if entry is not None and now - entry.loaded_at < POLICY_CACHE_TTL_SECONDS:
                return entry.policy
            if entry is not None:
                del self._policies[key]
            policy = self._build(key[0], key[1])
            # A read failure is transient; retry on the next call instead of
            # pinning the denial for the full TTL.
            if policy.status != "unreadable":
                self._policies[key] = _CacheEntry(policy=policy, loaded_at=now)
                self._evict_overflow()
            return policy

    def allows(
        self,
        ctx: RequestContext,
        path: str,
        *,
        owner_user_id: Optional[str] = None,
    ) -> bool:
        """Evaluate one owner-relative path; every non-valid policy denies."""
        return bool(self.for_owner(ctx, owner_user_id).allows_path(path))

    def allows_uri(self, ctx: RequestContext, uri: str) -> bool:
        """Evaluate a user-namespace URI by deriving its owner and relative path."""
        owner = owner_fields_for_uri(uri).get("owner_user_id")
        if not owner:
            return False
        relative = relative_uri_path(f"viking://user/{owner}", uri)
        if relative:
            return self.allows(ctx, relative, owner_user_id=owner)
        policy = self.for_owner(ctx, owner)
        if policy.status != "valid":
            return False
        return policy.default == "allow"

    def status(
        self,
        ctx: RequestContext,
        owner_user_id: Optional[str] = None,
    ) -> str:
        """Return the current cache status for one owner."""
        return str(self.for_owner(ctx, owner_user_id).status)

    def refresh(
        self,
        ctx: RequestContext,
        owner_user_id: Optional[str] = None,
    ) -> bool:
        """Reload one owner's policy now, ignoring the TTL."""
        self.invalidate(ctx, owner_user_id)
        return self.status(ctx, owner_user_id) == "valid"

    def invalidate(
        self,
        ctx: RequestContext,
        owner_user_id: Optional[str] = None,
    ) -> None:
        """Drop one cached entry after its policy file changes."""
        owner = ctx.user.user_id if owner_user_id is None else owner_user_id
        with self._lock:
            self._policies.pop((ctx.account_id, owner), None)

    def invalidate_account(self, account_id: str) -> None:
        """Drop every cached policy belonging to an account."""
        with self._lock:
            stale = [key for key in self._policies if key[0] == account_id]
            for key in stale:
                del self._policies[key]

    def clear(self) -> None:
        """Drop every cached policy."""
        with self._lock:
            self._policies.clear()

    def _evict_overflow(self) -> None:
        """Evict the oldest entries while the registry exceeds its bound."""
        while len(self._policies) > POLICY_CACHE_MAX_ENTRIES:
            (account_id, owner_user_id), _ = self._policies.popitem(last=False)
            LOGGER.info(
                "GLOBAL policy cache evicted the oldest entry: account=%s owner=%s size=%d max=%d",
                account_id,
                owner_user_id,
                len(self._policies),
                POLICY_CACHE_MAX_ENTRIES,
            )

    def _build(self, account_id: str, owner_user_id: str) -> GlobalAccessPolicy:
        owner_ctx = RequestContext(
            user=UserIdentifier(account_id, owner_user_id),
            role=Role.USER,
        )
        uri = _POLICY_URI.format(owner_user_id=owner_user_id)
        agfs_path = self._viking_fs._uri_to_path(uri, ctx=owner_ctx)
        try:
            # Encrypted backends need the account context to decrypt the policy
            # source. The account is already encoded in ``agfs_path``; pass it
            # explicitly so the read does not fail with a missing FsContext.
            raw = self._viking_fs.agfs.read(
                agfs_path,
                ctx={"account_id": account_id},
            )
        except Exception as exc:
            if is_not_found_error(exc):
                return global_policy_from_source(None)
            return global_policy_from_source(None, read_error=str(exc))
        return global_policy_from_source(raw)


__all__ = [
    "POLICY_CACHE_MAX_ENTRIES",
    "POLICY_CACHE_TTL_SECONDS",
    "UserGlobalPolicyService",
]
