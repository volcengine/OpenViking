# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""TTL identity adapter for scheduled tasks.

Object metadata remains authoritative for visibility and expiry.  This index is
a projection in PersistentTaskStore. Future and retry work use its bounded due
index; execution continues through QueueFS and TaskTracker.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Mapping, Optional

from openviking.pyagfs import AsyncAGFSClient
from openviking.server.error_mapping import is_storage_not_found
from openviking.server.identity import RequestContext
from openviking.service.task_store import PersistentTaskStore
from openviking.utils.time_utils import format_iso8601, parse_iso_datetime
from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_ROOT = "/local"
_TASK_KIND = "ttl_cleanup"
_MARKER = "_system/ttl/.enabled"


@dataclass(frozen=True)
class TTLRecord:
    object_uri: str
    object_type: str
    account_id: str
    user_id: str
    expires_at: str
    generation: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TTLRecord":
        return cls(
            object_uri=str(value["object_uri"]),
            object_type=str(value["object_type"]),
            account_id=str(value["account_id"]),
            user_id=str(value.get("user_id") or ""),
            expires_at=str(value["expires_at"]),
            generation=str(value["generation"]),
        )


def cleanup_not_before(record: TTLRecord, jitter_seconds: float | None = None) -> str:
    """Spread physical cleanup without changing the source visibility deadline.

    The offset is stable across processes/restarts and includes the expiry
    revision, so retries do not keep postponing the same object.
    """
    if jitter_seconds is None:
        jitter_seconds = get_openviking_config().ttl_cleanup.cleanup_jitter_seconds
    if not jitter_seconds:
        return record.expires_at
    identity = json.dumps(
        [record.account_id, record.object_uri, record.generation, record.expires_at],
        separators=(",", ":"),
    )
    fraction = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big") / 2**64
    return format_iso8601(
        parse_iso_datetime(record.expires_at) + timedelta(seconds=fraction * jitter_seconds)
    )


class TTLRegistry:
    """Map TTL object identity to the shared task persistence and due index."""

    def __init__(self, agfs: AsyncAGFSClient):
        self._agfs = agfs
        self._tasks = PersistentTaskStore(agfs)
        self._known_accounts: set[str] = set()

    @staticmethod
    def _key(account_id: str, uri: str) -> str:
        return json.dumps([account_id, uri], separators=(",", ":"))

    @classmethod
    def record_path(cls, account_id: str, uri: str) -> str:
        return PersistentTaskStore.schedule_path(_TASK_KIND, cls._key(account_id, uri))

    @staticmethod
    def marker_path(account_id: str) -> str:
        return f"{_ROOT}/{account_id}/{_MARKER}"

    async def account_may_have_records(self, account_id: str) -> bool:
        """Cache marker presence only; another worker can publish the first record."""
        if account_id in self._known_accounts:
            return True
        try:
            # Avoid plugin-local stat caches as well as process-local misses.
            await self._agfs.stat(self.marker_path(account_id), bypass_cache=True)
        except Exception as exc:
            if not is_storage_not_found(exc):
                # Fail open for reads: if registry state cannot be inspected,
                # object metadata is still able to enforce its own deadline.
                return True
            return False
        self._known_accounts.add(account_id)
        return True

    async def upsert(self, record: TTLRecord) -> None:
        """Register/revise expiry using the existing task store's delayed index."""
        marker = self.marker_path(record.account_id)
        await self._agfs.ensure_parent_dirs(marker)
        await self._agfs.write(marker, b"1")
        fields = asdict(record)
        await self._tasks.schedule(
            _TASK_KIND,
            self._key(record.account_id, record.object_uri),
            payload={"record": fields, "retry_count": 0},
            run_at=cleanup_not_before(record),
            # Ordinary writes must not erase a claim lease or retry backoff.
            preserve=lambda item: item["payload"]["record"] == fields,
        )
        self._known_accounts.add(record.account_id)

    async def get(self, account_id: str, uri: str) -> Optional[TTLRecord]:
        item = await self.get_scheduled(account_id, uri)
        if item is None:
            return None
        record = TTLRecord.from_dict(item["payload"]["record"])
        if record.account_id != account_id or record.object_uri != uri:
            raise ValueError(f"Invalid TTL registry record for {uri}")
        return record

    async def get_scheduled(self, account_id: str, uri: str) -> Optional[dict]:
        return await self._tasks.get_scheduled(_TASK_KIND, self._key(account_id, uri))

    async def remove_if_generation(self, account_id: str, uri: str, generation: str) -> bool:
        return await self._tasks.cancel_scheduled(
            _TASK_KIND,
            self._key(account_id, uri),
            condition=lambda item: item["payload"]["record"]["generation"] == generation,
        )

    async def defer_retry(
        self,
        record: TTLRecord,
        *,
        retry_count: int,
        task_id: str,
        next_retry_at: str,
        verify_only: bool = False,
        expected_retry_count: Optional[int] = None,
    ) -> bool:
        fields = asdict(record)
        return await self._tasks.schedule(
            _TASK_KIND,
            self._key(record.account_id, record.object_uri),
            payload={
                "record": fields,
                "retry_count": retry_count,
                "task_id": task_id,
                "verify_only": verify_only,
            },
            run_at=next_retry_at,
            condition=lambda item: (
                item is not None
                and item["payload"]["record"] == fields
                and (
                    expected_retry_count is None
                    or item["payload"].get("retry_count", 0) == expected_retry_count
                )
            ),
        )

    async def claim_due(self, **kwargs):
        async for payload in self._tasks.claim_due(_TASK_KIND, **kwargs):
            yield TTLRecord.from_dict(payload["record"]), payload


def record_from_fields(
    *,
    uri: str,
    object_type: str,
    fields: Mapping[str, Any],
    ctx: RequestContext,
) -> Optional[TTLRecord]:
    expires_at = str(fields.get("expires_at") or "")
    generation = str(fields.get("ttl_generation") or "")
    if not expires_at or not generation:
        return None
    return TTLRecord(
        object_uri=uri,
        object_type=object_type,
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        expires_at=expires_at,
        generation=generation,
    )
