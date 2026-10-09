# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Account TTL markers and the shared lock for late vector writes.

Directory metadata is the only stored deadline. TTLRecord is a queue payload,
not a second per-object index.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from openviking.pyagfs import AsyncAGFSClient
from openviking.server.error_mapping import is_storage_not_found
from openviking.service.task_store import SYSTEM_TASK_ACCOUNT_ID

_ROOT = "/local"
_VECTOR_LOCK_ROOT = f"/local/{SYSTEM_TASK_ACCOUNT_ID}/_system/tasks/_scheduled/ttl_cleanup"
_MARKER = "_system/ttl/.enabled"


@dataclass(frozen=True)
class TTLRecord:
    object_uri: str
    object_type: str
    account_id: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TTLRecord":
        return cls(
            object_uri=str(value["object_uri"]),
            object_type=str(value["object_type"]),
            account_id=str(value["account_id"]),
        )


class TTLRegistry:
    """Track accounts with TTL; deadlines are stored only in directory metadata."""

    def __init__(self, agfs: AsyncAGFSClient):
        self._agfs = agfs
        self._known_accounts: set[str] = set()

    @staticmethod
    def vector_lock_path(account_id: str, uri: str) -> str:
        # Preserve the existing lock identity for in-flight embedding workers.
        key = json.dumps([account_id, uri], separators=(",", ":"))
        digest = hashlib.sha256(key.encode()).hexdigest()
        return f"{_VECTOR_LOCK_ROOT}/records/{digest[:2]}/{digest}.json.vector_lock"

    @staticmethod
    def marker_path(account_id: str) -> str:
        return f"{_ROOT}/{account_id}/{_MARKER}"

    async def account_may_have_records(self, account_id: str) -> bool:
        """Cache presence only so another worker can enable the first object."""
        if account_id in self._known_accounts:
            return True
        try:
            await self._agfs.stat(self.marker_path(account_id), bypass_cache=True)
        except Exception as exc:
            # On storage errors, let metadata checks enforce visibility.
            return not is_storage_not_found(exc)
        self._known_accounts.add(account_id)
        return True

    async def mark_account(self, account_id: str) -> None:
        """Publish before the first expiry; a failed write leaves a harmless marker."""
        if account_id not in self._known_accounts:
            marker = self.marker_path(account_id)
            await self._agfs.ensure_parent_dirs(marker)
            await self._agfs.write(marker, b"1", auto_pathlock=False)
            self._known_accounts.add(account_id)
