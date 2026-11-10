# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Internal models and pure validation helpers for expiry scheduling."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Literal, Protocol, TypeVar

from .api import ExpiryError, ExpiryMode, ExpiryRegistration, ItemIdentity

T = TypeVar("T")
MINUTE_MS = 60_000
MIN_METADATA_TTL_MS = 86_400_000
SCORE_TIME_BASE = 1 << 40
MAX_ATTEMPT = 255
MAX_EXACT_INTEGER = (1 << 53) - 1
_ITEM_TYPE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class StoreOperationError(ExpiryError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(code, message, retryable=retryable)
        self.outcome_unknown = outcome_unknown


class OwnerRole(str, Enum):
    SCHEDULER = "scheduler"
    CALLBACK = "callback"


@dataclass(frozen=True)
class ItemMetadata:
    identity: ItemIdentity


@dataclass(frozen=True)
class NamespaceSpec:
    shard_count: int
    hash_name: str
    hash_seed: int
    identity_encoding: str


@dataclass(frozen=True)
class OwnerToken:
    role: OwnerRole
    shard_id: int
    token: str
    expires_at_ms: int
    item_type: str | None = None


@dataclass
class ShardLease:
    owner: OwnerToken
    lost: asyncio.Event
    valid_until_monotonic: float
    revoked: asyncio.Event = field(default_factory=asyncio.Event)

    def ensure_active(self) -> None:
        if self.lost.is_set() or time.monotonic() >= self.valid_until_monotonic:
            self.lost.set()
            raise StoreOperationError("owner_lost", "Shard lease is no longer valid")
        if self.revoked.is_set():
            raise StoreOperationError(
                "owner_lost", "Shard lease is no longer valid (execution revoked)"
            )


@dataclass(frozen=True)
class ShardWindow:
    progress_ms: int
    upper_ms: int
    redis_now_ms: int


@dataclass(frozen=True)
class SourceCursor:
    due_at_ms: int
    item_id: str


@dataclass(frozen=True)
class SourceItem:
    item_id: str
    due_at_ms: int


@dataclass(frozen=True)
class SourcePage:
    items: tuple[SourceItem, ...]
    next_cursor: SourceCursor | None


@dataclass(frozen=True)
class BucketRef:
    item_type: str
    shard_id: int
    minute_ms: int


@dataclass(frozen=True)
class RegistryCursor:
    score_ms: int
    member: str


@dataclass(frozen=True)
class RegistryPage:
    buckets: tuple[BucketRef, ...]
    next_cursor: RegistryCursor | None


@dataclass(frozen=True)
class PublishResult:
    status: Literal["published", "already_published"]
    progress_ms: int


@dataclass(frozen=True)
class Claim:
    bucket: BucketRef
    item_id: str
    claim_score: int


@dataclass(frozen=True)
class BucketItem:
    item_id: str
    score: int


@dataclass(frozen=True)
class BucketPage:
    items: tuple[BucketItem, ...]


class AsyncExecutor(Protocol):
    async def run(self, operation: Callable[[], Awaitable[T]]) -> T: ...


def task_is_cancelling(task: asyncio.Task | None) -> bool:
    if task is None:
        return False
    cancelling = getattr(task, "cancelling", None)
    return bool(cancelling()) if callable(cancelling) else task.cancelled()


def validate_item_type(item_type: str) -> None:
    if not isinstance(item_type, str) or _ITEM_TYPE_RE.fullmatch(item_type) is None:
        raise ExpiryError("invalid_identity", "Invalid resource item type")


def validate_identity(identity: ItemIdentity) -> None:
    if not isinstance(identity, ItemIdentity):
        raise ExpiryError("invalid_identity", "Invalid resource identity")
    validate_item_type(identity.item_type)
    for name in ("account_id", "user_id", "local_id"):
        value = getattr(identity, name)
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ExpiryError("invalid_identity", f"Invalid resource identity field: {name}")


def validate_report(task: ExpiryRegistration) -> None:
    if not isinstance(task, ExpiryRegistration):
        raise ExpiryError("invalid_report", "Invalid resource report")
    validate_identity(task.identity)
    if task.mode is not ExpiryMode.IDLE_ONESHOT:
        raise ExpiryError("unsupported_mode", f"Unsupported timer mode: {task.mode}")
    if type(task.due_at_ms) is not int or not 0 <= task.due_at_ms <= MAX_EXACT_INTEGER:
        raise ExpiryError("invalid_report", "Due time is outside the supported range")
    metadata_ttl_ms(task.timeout_seconds)
    bucket_minute_ms(task.due_at_ms)


def minute_start_ms(timestamp_ms: int) -> int:
    return timestamp_ms // MINUTE_MS * MINUTE_MS


def scan_cutoff_ms(now_ms: int) -> int:
    return minute_start_ms(now_ms)


def bucket_minute_ms(due_at_ms: int) -> int:
    if type(due_at_ms) is not int or not 0 <= due_at_ms <= MAX_EXACT_INTEGER:
        raise ExpiryError("invalid_report", "Due time is outside the supported range")
    minute = ((due_at_ms + MINUTE_MS - 1) // MINUTE_MS) * MINUTE_MS
    if minute > MAX_EXACT_INTEGER:
        raise ExpiryError("invalid_report", "Due time is outside the supported range")
    return minute


def metadata_ttl_ms(timeout_seconds: int) -> int:
    if type(timeout_seconds) is not int or not 0 < timeout_seconds <= MAX_EXACT_INTEGER // 2000:
        raise ExpiryError("invalid_report", "Timeout is outside the supported range")
    return max(timeout_seconds * 2000, MIN_METADATA_TTL_MS)


def encode_claim_score(attempt: int, started_at_seconds: int) -> int:
    if type(attempt) is not int or not 0 <= attempt <= MAX_ATTEMPT:
        raise ValueError("attempt out of range")
    if type(started_at_seconds) is not int or not 0 < started_at_seconds < SCORE_TIME_BASE:
        raise ValueError("claim timestamp out of range")
    return attempt * SCORE_TIME_BASE + started_at_seconds


def decode_claim_score(score: int) -> tuple[int, int]:
    if type(score) is not int or not 0 < score < (MAX_ATTEMPT + 1) * SCORE_TIME_BASE:
        raise ValueError("invalid claim score")
    attempt, started_at = divmod(score, SCORE_TIME_BASE)
    if started_at == 0:
        raise ValueError("invalid claim timestamp")
    return attempt, started_at


def claim_layer_bounds(attempt: int) -> tuple[int, int]:
    if type(attempt) is not int or not 0 <= attempt <= MAX_ATTEMPT:
        raise ValueError("attempt out of range")
    lower = 0 if attempt == 0 else attempt * SCORE_TIME_BASE + 1
    return lower, (attempt + 1) * SCORE_TIME_BASE - 1


def encode_ready_claim_score(attempt: int) -> int:
    return encode_claim_score(attempt, 1)
