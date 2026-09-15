# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Queue lifecycle extensions, independent of task tracking and storage backends."""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from .process_result import ProcessResult

Payload = Union[str, Dict[str, Any]]
Message = Dict[str, Any]


@dataclass
class EnqueueContext:
    queue: str
    payload: Payload
    committed: bool = False


@dataclass
class ProcessContext:
    queue: str
    message: Message
    cancel: Callable[[], Awaitable[ProcessResult]]


@dataclass
class AckContext:
    queue: str
    message_id: str
    message: Optional[Message] = None
    committed: bool = False


@dataclass
class ClearContext:
    queue: str
    messages: List[Message]
    committed: bool = False


EnqueueNext = Callable[[EnqueueContext], Awaitable[str]]
ProcessNext = Callable[[ProcessContext], Awaitable[ProcessResult]]
AckNext = Callable[[AckContext], Awaitable[None]]
ClearNext = Callable[[ClearContext], Awaitable[None]]


class QueueMiddleware:
    """Wrap operations in constructor-supplied order (first item is outermost).

    Queues copy the sequence to a tuple at construction; there is no runtime
    registration or reconfiguration.

    Override only the operations needed. Call ``call_next(ctx)`` once with the same
    context to continue, or return/raise to short-circuit. Exceptions unwind before
    the queue applies its existing error policy. Do not change message ownership
    after an outer middleware has registered it.
    Process must return a ProcessResult even when short-circuiting; the queue
    owns all delivery statistics and ACKs settled results.

    Contexts are invocation-local; middleware instances may be shared by queues
    and worker threads and must not store per-message state on themselves.
    Only the transport sets ``committed``. An outer middleware can compensate
    uncommitted operations, including a normal return from a short-circuit.
    It must not compensate an operation that already committed. Clear receives
    a pre-operation snapshot; it does not promise isolation from concurrent enqueue.
    """

    async def enqueue(self, ctx: EnqueueContext, call_next: EnqueueNext) -> str:
        return await call_next(ctx)

    async def process(self, ctx: ProcessContext, call_next: ProcessNext) -> ProcessResult:
        return await call_next(ctx)

    async def ack(self, ctx: AckContext, call_next: AckNext) -> None:
        await call_next(ctx)

    async def clear(self, ctx: ClearContext, call_next: ClearNext) -> None:
        await call_next(ctx)
