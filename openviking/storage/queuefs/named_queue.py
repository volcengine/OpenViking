# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistent named-queue operations and handler lifecycle.

``NamedQueue`` owns QueueFS I/O, delivery counters, retry, and acknowledgement.
Business integrations wrap enqueue, process, and ACK through ordered
``QueueMiddleware`` instances.
"""

import abc
import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from openviking.pyagfs import AGFSSyncClientProtocol, AsyncAGFSClient
from openviking.pyagfs.exceptions import AGFSAlreadyExistsError, AGFSNotFoundError
from openviking.storage.queuefs.queue_hook import (
    AckContext,
    DiscardReason,
    EnqueueContext,
    EnqueueKind,
    ProcessContext,
    ProcessOutcome,
    ProcessResult,
    QueueEnqueueRejected,
    QueueMiddleware,
)
from openviking.utils.async_utils import run_to_completion
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
QUEUE_ATTEMPT_FIELD = "_queue_attempt"


@dataclass
class QueueError:
    """Error record."""

    timestamp: datetime
    message: str
    data: Optional[Dict[str, Any]] = None


@dataclass
class QueueStatus:
    """Queue status."""

    pending: int = 0
    in_progress: int = 0
    processed: int = 0
    requeue_count: int = 0
    error_count: int = 0
    errors: List[QueueError] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    @property
    def is_complete(self) -> bool:
        return self.pending == 0 and self.in_progress == 0


class DequeueHandlerBase(abc.ABC):
    """Business handler returning an explicit queue processing result."""

    async def on_discard(
        self,
        data: Optional[Dict[str, Any]],
        *,
        reason: DiscardReason,
        handler_started: bool,
    ) -> ProcessResult:
        """Clean up a message that will be permanently discarded."""
        del reason, handler_started
        return ProcessResult.cancelled()

    @abc.abstractmethod
    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        """Process one message and return its explicit disposition."""
        if not data:
            return ProcessResult.failed("Queue message is empty")
        return ProcessResult.success(data)


class NamedQueue:
    """QueueFS operations, middleware, and status for one named queue."""

    MAX_ERRORS = 100

    def __init__(
        self,
        agfs: AGFSSyncClientProtocol,
        mount_point: str,
        name: str,
        dequeue_handler: Optional[DequeueHandlerBase] = None,
        middlewares: Optional[List[QueueMiddleware]] = None,
    ):
        self.name = name
        self.path = f"{mount_point}/{name}"
        self._agfs = agfs
        self._async_agfs = AsyncAGFSClient(agfs)
        self._dequeue_handler = dequeue_handler
        self._middlewares: List[QueueMiddleware] = list(middlewares or [])
        self._initialized = False

        # Status tracking
        self._lock = threading.Lock()
        self._in_progress = 0
        self._processed = 0
        self._requeue_count = 0
        self._error_count = 0
        self._errors: List[QueueError] = []

    def add_middleware(self, middleware: QueueMiddleware) -> None:
        """Register middleware before queue workers start."""
        if any(existing is middleware for existing in self._middlewares):
            return
        self._middlewares.append(middleware)

    def set_dequeue_handler(self, handler: DequeueHandlerBase) -> None:
        """Bind the consumer after its runtime dependencies are initialized."""
        self._dequeue_handler = handler

    def _normalize_process_result(
        self,
        result: Any,
        *,
        method_name: str = "on_dequeue",
    ) -> ProcessResult:
        if not isinstance(result, ProcessResult):
            return ProcessResult.failed(
                f"{type(self._dequeue_handler).__name__}.{method_name}() must return ProcessResult"
            )
        if result.outcome is ProcessOutcome.REQUEUE and result.retry_payload is None:
            return ProcessResult.failed("REQUEUE result requires retry_payload")
        return result

    def _on_dequeue_start(self) -> None:
        """Called on dequeue."""
        with self._lock:
            self._in_progress += 1

    def _settle_process(self, result: ProcessResult, data: Dict[str, Any]) -> None:
        """Settle process-local counters exactly once for an explicit result."""
        with self._lock:
            self._in_progress -= 1
            if result.outcome is ProcessOutcome.FAILED:
                self._error_count += 1
                self._errors.append(
                    QueueError(
                        timestamp=datetime.now(),
                        message=result.error or "Queue handler failed",
                        data=data,
                    )
                )
                if len(self._errors) > self.MAX_ERRORS:
                    self._errors = self._errors[-self.MAX_ERRORS :]
                return
            if result.outcome is ProcessOutcome.DUPLICATE:
                return
            self._processed += 1
            if result.outcome is ProcessOutcome.REQUEUE:
                self._requeue_count += 1

    def _abandon_process(
        self,
        error: Optional[BaseException] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Release a local active slot while leaving the durable message unacknowledged."""
        with self._lock:
            self._in_progress -= 1
            if error is None:
                return
            self._error_count += 1
            self._errors.append(
                QueueError(
                    timestamp=datetime.now(),
                    message=str(error),
                    data=data,
                )
            )
            if len(self._errors) > self.MAX_ERRORS:
                self._errors = self._errors[-self.MAX_ERRORS :]

    async def get_status(self) -> QueueStatus:
        """Get queue status."""
        pending = await self.size()
        with self._lock:
            return QueueStatus(
                pending=pending,
                in_progress=self._in_progress,
                processed=self._processed,
                requeue_count=self._requeue_count,
                error_count=self._error_count,
                errors=list(self._errors),
            )

    def reset_status(self) -> None:
        """Reset status counters."""
        with self._lock:
            self._in_progress = 0
            self._processed = 0
            self._requeue_count = 0
            self._error_count = 0
            self._errors = []

    def has_dequeue_handler(self) -> bool:
        """Check if dequeue handler exists."""
        return self._dequeue_handler is not None

    async def _ensure_initialized(self):
        """Ensure queue directory is created in AGFS."""
        if not self._initialized:
            try:
                await self._async_agfs.mkdir(self.path)
            except (AGFSAlreadyExistsError, FileExistsError):
                pass
            self._initialized = True

    async def _run_chain(
        self,
        ctx: Any,
        step: Any,
        terminal: Any,
    ) -> Any:
        """Drive middlewares as an onion: enter in order, exit in reverse.

        ``step(middleware, ctx, call_next)`` invokes one middleware's operation;
        ``terminal(ctx)`` runs the queue's own core once all middlewares entered.
        """
        middlewares = tuple(self._middlewares)

        async def invoke(index: int, current: Any) -> Any:
            if index == len(middlewares):
                return await terminal(current)
            return await step(
                middlewares[index],
                current,
                lambda next_ctx: invoke(index + 1, next_ctx),
            )

        return await invoke(0, ctx)

    async def enqueue(self, data: Union[str, Dict[str, Any]]) -> str:
        """Persist a new message through the enqueue middleware chain."""
        return await self._enqueue(data, EnqueueKind.NEW)

    async def _enqueue(self, data: Any, kind: EnqueueKind) -> str:
        await self._ensure_initialized()
        ctx = EnqueueContext(queue=self.name, payload=data, kind=kind)
        try:
            return await self._run_chain(
                ctx,
                lambda mw, current, call_next: mw.enqueue(current, call_next),
                self._enqueue_core,
            )
        except QueueEnqueueRejected as rejected:
            return rejected.result

    async def _enqueue_core(self, ctx: EnqueueContext) -> str:
        body = json.dumps(ctx.payload) if isinstance(ctx.payload, dict) else ctx.payload

        async def write_message() -> Any:
            result = await self._async_agfs.write(
                f"{self.path}/enqueue",
                body.encode("utf-8"),
            )
            ctx.committed_msg_id = result if isinstance(result, str) else str(result)
            return result

        result = await run_to_completion(write_message)
        return result if isinstance(result, str) else str(result)

    async def enqueue_retry(self, data: Any, *, attempt: int = 1) -> str:
        """Persist a replacement with a fresh middleware-owned work identity."""
        if isinstance(data, dict):
            data = dict(data)
            data[QUEUE_ATTEMPT_FIELD] = max(1, int(attempt))
        return await self._enqueue(data, EnqueueKind.RETRY)

    async def schedule_retry(self, result: ProcessResult, *, attempt: int = 1) -> str:
        """Apply a handler retry result using this queue's transport policy."""
        if result.outcome is not ProcessOutcome.REQUEUE or result.retry_payload is None:
            raise ValueError("schedule_retry requires a REQUEUE result with retry_payload")
        if result.retry_delay > 0:
            await asyncio.sleep(result.retry_delay)
        return await self.enqueue_retry(result.retry_payload, attempt=attempt)

    async def ack(self, msg_id: str, message: Optional[Dict[str, Any]] = None) -> None:
        """Delete one message through the acknowledgement middleware chain."""
        if not msg_id:
            return
        ctx = AckContext(queue=self.name, message=message or {}, msg_id=msg_id)
        await self._run_chain(
            ctx,
            lambda mw, current, call_next: mw.ack(current, call_next),
            self._ack_core,
        )

    async def _ack_core(self, ctx: AckContext) -> None:
        async def write_ack() -> None:
            await self._async_agfs.write(
                f"{self.path}/ack",
                ctx.msg_id.encode("utf-8"),
            )

        await run_to_completion(write_ack)

    async def _read_queue_message(self) -> Optional[Dict[str, Any]]:
        """Read and remove one message from the AGFS queue; return parsed dict or None.

        Normalises the various return types AGFSClient.read() may produce.
        """
        content = await self._async_agfs.read(f"{self.path}/dequeue")
        if not content or content == b"{}":
            return None
        if isinstance(content, bytes):
            raw = content
        elif isinstance(content, str):
            raw = content.encode("utf-8")
        elif hasattr(content, "content") and content.content is not None:
            raw = content.content
        else:
            raw = str(content).encode("utf-8")
        return json.loads(raw.decode("utf-8"))

    async def consume_one(self) -> Optional[ProcessResult]:
        """Claim, process, and acknowledge one message."""
        await self._ensure_initialized()
        data = await self._read_queue_message()
        if data is None:
            return None

        msg_id = data.get("id", "") if isinstance(data, dict) else ""
        self._on_dequeue_start()
        try:
            result = await self._invoke_process(data)
        except asyncio.CancelledError:
            self._abandon_process()
            raise
        except BaseException as exc:
            self._abandon_process(exc, data)
            raise

        self._settle_process(result, data)
        try:
            await self.ack(msg_id, data)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Work is already terminal. A stale redelivery will skip the handler
            # and retry only the physical acknowledgement.
            logger.exception("[NamedQueue] Ack failed for %s msg_id=%s", self.name, msg_id)
        return result

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """Compatibility wrapper returning the handler value."""
        try:
            result = await self.consume_one()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[NamedQueue] Dequeue failed for %s: %s", self.name, exc)
            return None
        return None if result is None else result.value

    async def _invoke_process(self, data: Dict[str, Any]) -> ProcessResult:
        async def discard(reason: DiscardReason, handler_started: bool) -> ProcessResult:
            if self._dequeue_handler is None:
                return ProcessResult.cancelled()
            result = await self._dequeue_handler.on_discard(
                data,
                reason=reason,
                handler_started=handler_started,
            )
            return self._normalize_process_result(result, method_name="on_discard")

        ctx = ProcessContext(
            queue=self.name,
            message=data,
            _discard=discard,
        )
        return await self._run_chain(
            ctx,
            lambda mw, current, call_next: mw.process(current, call_next),
            self._process_core,
        )

    async def _process_core(self, ctx: ProcessContext) -> ProcessResult:
        if self._dequeue_handler is None:
            return ProcessResult.success(ctx.message)

        try:
            result = await self._dequeue_handler.on_dequeue(ctx.message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[NamedQueue] Handler failed for %s", self.name)
            result = ProcessResult.failed(exc)

        result = self._normalize_process_result(result)
        if result.outcome is ProcessOutcome.REQUEUE:
            attempt = self._delivery_attempt(ctx.message)
            if result.max_attempts is not None and attempt + 1 >= result.max_attempts:
                return ProcessResult.failed(
                    result.error
                    or f"Queue retry limit reached after {result.max_attempts} attempts"
                )
            await self.schedule_retry(result, attempt=attempt + 1)
        return result

    @staticmethod
    def _delivery_attempt(message: Dict[str, Any]) -> int:
        payload: Any = message.get("data", message)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return 0
        if not isinstance(payload, dict):
            return 0
        try:
            return max(0, int(payload.get(QUEUE_ATTEMPT_FIELD, 0) or 0))
        except (TypeError, ValueError):
            return 0

    async def peek(self) -> Optional[Dict[str, Any]]:
        """Peek at head message without removing."""
        await self._ensure_initialized()
        peek_file = f"{self.path}/peek"

        try:
            content = await self._async_agfs.read(peek_file)
            if not content or content == b"{}":
                return None
            if isinstance(content, bytes):
                return json.loads(content.decode("utf-8"))
            elif isinstance(content, str):
                return json.loads(content)
            else:
                return None
        except Exception as e:
            logger.debug(f"[NamedQueue] Peek failed for {self.name}: {e}")
            return None

    async def size(self) -> int:
        """Get queue size."""
        await self._ensure_initialized()
        size_file = f"{self.path}/size"

        try:
            content = await self._async_agfs.read(size_file)
            if content is None:
                return 0
            if isinstance(content, bytes):
                text = content.decode("utf-8")
            elif isinstance(content, str):
                text = content
            else:
                raise TypeError(f"Unexpected queue size response: {type(content).__name__}")
            text = text.strip()
            return int(text) if text else 0
        except (AGFSNotFoundError, FileNotFoundError):
            return 0

    async def snapshot(self) -> List[Dict[str, Any]]:
        """Return all unacknowledged messages without changing queue state."""
        await self._ensure_initialized()
        try:
            content = await self._async_agfs.read(f"{self.path}/messages")
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        if not content:
            return []
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        elif hasattr(content, "content") and content.content is not None:
            content = content.content.decode("utf-8")
        parsed = json.loads(content)
        return parsed if isinstance(parsed, list) else []

    async def clear(self) -> bool:
        """Clear all transport messages without invoking message handlers."""
        await self._ensure_initialized()
        clear_file = f"{self.path}/clear"

        try:

            async def write_clear() -> None:
                await self._async_agfs.write(clear_file, b"")

            await run_to_completion(write_clear)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[NamedQueue] Clear failed for {self.name}: {e}")
            return False
