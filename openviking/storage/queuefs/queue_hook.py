# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Composable middleware and explicit results for persistent queues."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional


class QueueEnqueueRejected(Exception):
    """A middleware rejected an enqueue and supplied its queue-level result."""

    def __init__(self, result: str) -> None:
        super().__init__(result)
        self.result = result


class EnqueueKind(enum.Enum):
    """Why a message is being enqueued."""

    NEW = "new"
    RETRY = "retry"


class DiscardReason(enum.Enum):
    """Why a claimed message is being permanently discarded."""

    USER_CANCELLED = "user_cancelled"


class ProcessOutcome(enum.Enum):
    """A handler's explicit disposition for one queue message."""

    SUCCESS = "success"
    FAILED = "failed"
    REQUEUE = "requeue"
    CANCELLED = "cancelled"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class ProcessResult:
    """Typed handler result interpreted and persisted by the queue."""

    outcome: ProcessOutcome
    value: Any = None
    error: Optional[str] = None
    retry_payload: Any = None
    retry_delay: float = 0.0
    max_attempts: Optional[int] = None

    @classmethod
    def success(cls, value: Any = None) -> "ProcessResult":
        return cls(ProcessOutcome.SUCCESS, value=value)

    @classmethod
    def failed(cls, error: object) -> "ProcessResult":
        return cls(ProcessOutcome.FAILED, error=str(error))

    @classmethod
    def requeue(
        cls,
        payload: Any,
        *,
        error: Optional[str] = None,
        delay: float = 0.0,
        max_attempts: Optional[int] = None,
    ) -> "ProcessResult":
        return cls(
            ProcessOutcome.REQUEUE,
            error=error,
            retry_payload=payload,
            retry_delay=max(0.0, float(delay)),
            max_attempts=max_attempts,
        )

    @classmethod
    def cancelled(cls, value: Any = None) -> "ProcessResult":
        return cls(ProcessOutcome.CANCELLED, value=value)

    @classmethod
    def duplicate(cls) -> "ProcessResult":
        return cls(ProcessOutcome.DUPLICATE)


@dataclass
class EnqueueContext:
    """Mutable state shared by one enqueue middleware chain."""

    queue: str
    payload: Any
    kind: EnqueueKind = EnqueueKind.NEW
    committed_msg_id: Optional[str] = None

    @property
    def committed(self) -> bool:
        return self.committed_msg_id is not None


DiscardCallback = Callable[[DiscardReason, bool], Awaitable[ProcessResult]]


@dataclass(frozen=True)
class ProcessContext:
    """One claimed message as seen by process middleware."""

    queue: str
    message: Dict[str, Any]
    _discard: DiscardCallback

    async def discard(
        self,
        reason: DiscardReason,
        *,
        handler_started: bool,
    ) -> ProcessResult:
        """Ask the queue-owned handler to clean up a discarded message."""
        return await self._discard(reason, handler_started)


@dataclass(frozen=True)
class AckContext:
    """One physical QueueFS acknowledgement."""

    queue: str
    message: Dict[str, Any]
    msg_id: str


EnqueueNext = Callable[[EnqueueContext], Awaitable[str]]
ProcessNext = Callable[[ProcessContext], Awaitable[ProcessResult]]
AckNext = Callable[[AckContext], Awaitable[None]]


class QueueMiddleware:
    """Onion middleware around enqueue, process, and acknowledgement."""

    async def enqueue(self, ctx: EnqueueContext, call_next: EnqueueNext) -> str:
        return await call_next(ctx)

    async def process(self, ctx: ProcessContext, call_next: ProcessNext) -> ProcessResult:
        return await call_next(ctx)

    async def ack(self, ctx: AckContext, call_next: AckNext) -> None:
        await call_next(ctx)


# Compatibility name for integrations that still register a "hook".
QueueHook = QueueMiddleware


__all__ = [
    "AckContext",
    "AckNext",
    "DiscardReason",
    "EnqueueContext",
    "EnqueueKind",
    "EnqueueNext",
    "ProcessContext",
    "ProcessNext",
    "ProcessOutcome",
    "ProcessResult",
    "QueueEnqueueRejected",
    "QueueHook",
    "QueueMiddleware",
]
