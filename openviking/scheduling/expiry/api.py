# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public contracts for distributed expiry scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

SESSION_ITEM_TYPE = "session"
SUPPORTED_ITEM_TYPES: frozenset[str] = frozenset({SESSION_ITEM_TYPE})


class ExpiryMode(str, Enum):
    IDLE_ONESHOT = "idle_oneshot"
    FIXED = "fixed"


class CallbackStatus(str, Enum):
    COMPLETED = "completed"
    RETRY = "retry"


class ExpiryError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ItemIdentity:
    item_type: str
    account_id: str
    user_id: str
    local_id: str


@dataclass(frozen=True)
class ExpiryRegistration:
    identity: ItemIdentity
    due_at_ms: int
    timeout_seconds: int
    mode: ExpiryMode = ExpiryMode.IDLE_ONESHOT


@dataclass(frozen=True)
class ExpiryRegistrationResult:
    accepted_due_at_ms: int


@dataclass(frozen=True)
class CallbackRequest:
    identity: ItemIdentity


@dataclass(frozen=True)
class CallbackResult:
    status: CallbackStatus
    reason: str


@dataclass(frozen=True)
class HandlerOptions:
    concurrency: int = 4
    max_attempts: int = 3
    metadata_enabled: bool = True


class ExpiryHandler(Protocol):
    async def handle(self, request: CallbackRequest) -> CallbackResult: ...


class ExpiryScheduler(Protocol):
    async def report(self, task: ExpiryRegistration) -> ExpiryRegistrationResult: ...

    async def unregister(self, identity: ItemIdentity) -> None: ...

    def register_handler(
        self,
        item_type: str,
        handler: ExpiryHandler,
        *,
        options: HandlerOptions | None = None,
    ) -> None: ...
