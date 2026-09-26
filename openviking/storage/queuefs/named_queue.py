# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import abc
import asyncio
import json
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, TypeVar, Union

from openviking.pyagfs import AGFSSyncClientProtocol, AsyncAGFSClient
from openviking.pyagfs.exceptions import AGFSAlreadyExistsError, AGFSNotFoundError
from openviking.storage.queuefs.process_result import ProcessOutcome, ProcessResult
from openviking.storage.queuefs.queue_middleware import (
    AckContext,
    ClearContext,
    EnqueueContext,
    ProcessContext,
    QueueMiddleware,
)
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
_ContextT = TypeVar("_ContextT")
_ResultT = TypeVar("_ResultT")


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
    """Return a delivery outcome; queue statistics are never updated by handlers."""

    async def on_cancelled(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        """Discard task work cancelled before its handler starts."""
        return ProcessResult.cancelled()

    @abc.abstractmethod
    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> ProcessResult:
        """Complete this delivery, or raise to leave it unacknowledged."""
        raise NotImplementedError


class NamedQueue:
    """NamedQueue: Operation class for specific named queue, supports status tracking."""

    MAX_ERRORS = 100

    def __init__(
        self,
        agfs: AGFSSyncClientProtocol,
        mount_point: str,
        name: str,
        dequeue_handler: Optional[DequeueHandlerBase] = None,
        middlewares: Sequence[QueueMiddleware] = (),
    ):
        self.name = name
        self.path = f"{mount_point}/{name}"
        self._agfs = agfs
        self._async_agfs = AsyncAGFSClient(agfs)
        self._dequeue_handler = dequeue_handler
        self._middlewares = tuple(middlewares)
        self._initialized = False

        # Status tracking
        self._lock = threading.Lock()
        self._processed = 0
        self._requeue_count = 0
        self._error_count = 0
        self._errors: List[QueueError] = []

    async def _run_middleware(
        self,
        operation: str,
        ctx: _ContextT,
        terminal: Callable[[_ContextT], Awaitable[_ResultT]],
    ) -> _ResultT:
        call_next = terminal
        for middleware in reversed(self._middlewares):
            call_next = partial(getattr(middleware, operation), call_next=call_next)
        return await call_next(ctx)

    def set_dequeue_handler(self, handler: DequeueHandlerBase) -> None:
        """Bind the consumer after its runtime dependencies are initialized."""
        self._dequeue_handler = handler

    def _record_result(self, result: ProcessResult, data: Dict[str, Any]) -> None:
        if result.outcome is ProcessOutcome.FAILED:
            self._record_error(result.error, data)
            return
        with self._lock:
            # Keep the existing processed count: settled non-error deliveries,
            # including cancellations and successful re-enqueues.
            self._processed += 1
            if result.outcome is ProcessOutcome.REQUEUED:
                self._requeue_count += 1

    def _record_error(self, error_msg: str, data: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._error_count += 1
            self._errors.append(
                QueueError(
                    timestamp=datetime.now(),
                    message=error_msg,
                    data=data,
                )
            )
            if len(self._errors) > self.MAX_ERRORS:
                self._errors = self._errors[-self.MAX_ERRORS :]

    async def get_status(self) -> QueueStatus:
        """Get queue status."""
        backend_status = await self._read_backend_status()
        with self._lock:
            return QueueStatus(
                pending=backend_status["pending"],
                in_progress=backend_status["processing"],
                processed=self._processed,
                requeue_count=self._requeue_count,
                error_count=self._error_count,
                errors=list(self._errors),
            )

    def reset_status(self) -> None:
        """Reset status counters."""
        with self._lock:
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

    async def enqueue(self, data: Union[str, Dict[str, Any]]) -> str:
        """Send message to queue (enqueue)."""
        await self._ensure_initialized()
        return await self._run_middleware("enqueue", EnqueueContext(self.name, data), self._enqueue)

    async def _enqueue(self, ctx: EnqueueContext) -> str:
        data = json.dumps(ctx.payload) if isinstance(ctx.payload, dict) else ctx.payload
        msg_id = await self._async_agfs.write(f"{self.path}/enqueue", data.encode("utf-8"))
        ctx.committed = True
        return msg_id if isinstance(msg_id, str) else str(msg_id)

    async def ack(self, msg_id: str, message: Optional[Dict[str, Any]] = None) -> None:
        """Acknowledge successful processing of a message (deletes it from persistent storage).

        Must be called after the dequeue handler finishes processing a message.
        Middleware can provisionally settle application state before deletion.
        If not called (e.g. process crashes), the message will be automatically
        re-queued on the next startup via RecoverStale.
        """
        if not msg_id:
            return
        try:
            await self._run_middleware("ack", AckContext(self.name, msg_id, message), self._ack)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[NamedQueue] Ack failed for {self.name} msg_id={msg_id}: {e}")

    async def _ack(self, ctx: AckContext) -> None:
        await self._async_agfs.write(f"{self.path}/ack", ctx.message_id.encode("utf-8"))
        ctx.committed = True

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

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """Dequeue a message, process it, then ack to confirm deletion.

        Flow (at-least-once delivery):
          1. Read from /dequeue  → backend marks message as 'processing' (not deleted yet)
          2. Call on_dequeue()   → actual processing
          3. Call ack()          → backend deletes the message permanently

        If the process crashes between steps 1 and 3, the backend's RecoverStale
        on the next startup resets the message back to 'pending' for retry.
        """
        await self._ensure_initialized()
        try:
            data = await self._read_queue_message()
            if data is None:
                return None
            msg_id = data.get("id", "") if isinstance(data, dict) else ""
            raw_data = data
            if self._dequeue_handler:
                result = await self.process_dequeued(raw_data)
                data = result.value
            # Ack unconditionally after handler returns (success or handled error).
            # If on_dequeue raises, the exception propagates and ack is skipped —
            # the message will be recovered on next startup.
            await self.ack(msg_id, raw_data)
            return data
        except Exception as e:
            logger.debug(f"[NamedQueue] Dequeue failed for {self.name}: {e}")
            return None

    async def dequeue_raw(self) -> Optional[Dict[str, Any]]:
        """Get and remove message from queue without invoking the handler."""
        await self._ensure_initialized()
        try:
            return await self._read_queue_message()
        except Exception as e:
            logger.debug(f"[NamedQueue] Dequeue raw failed for {self.name}: {e}")
            return None

    async def process_dequeued(self, data: Dict[str, Any]) -> ProcessResult:
        """Process one fetched delivery and settle local status exactly once."""
        handler = self._dequeue_handler
        if handler is None:
            return ProcessResult.success(data)
        process_started = time.perf_counter()

        async def process(ctx: ProcessContext) -> ProcessResult:
            return self._validate_result(await handler.on_dequeue(ctx.message))

        async def cancel() -> ProcessResult:
            return self._validate_result(await handler.on_cancelled(ctx.message))

        ctx = ProcessContext(self.name, data, cancel=cancel)
        try:
            result = self._validate_result(await self._run_middleware("process", ctx, process))
        except asyncio.CancelledError:
            self._publish_duration(data, process_started, "exception")
            raise
        except Exception as exc:
            self._record_error(str(exc), data)
            self._publish_duration(data, process_started, "exception")
            raise
        self._record_result(result, data)
        self._publish_duration(data, process_started, result.outcome.value)
        return result

    def _publish_duration(self, data: Dict[str, Any], process_started: float, outcome: str) -> None:
        """Publish best-effort processing and end-to-end timings for one delivery."""
        from openviking.observability.events import try_publish_event

        payload: Dict[str, Any] = {
            "queue": self.name,
            "outcome": str(outcome),
            "process_duration_seconds": max(0.0, time.perf_counter() - process_started),
        }
        enqueued_at = self._extract_enqueued_at(data)
        if enqueued_at is not None:
            end_to_end = time.time() - enqueued_at
            if end_to_end >= 0:
                payload["end_to_end_duration_seconds"] = end_to_end
        try_publish_event("queue.processed", payload)

    @staticmethod
    def _extract_enqueued_at(data: Dict[str, Any]) -> Optional[float]:
        """Read the QueueFS enqueue timestamp from a dequeued message envelope."""
        if not isinstance(data, dict) or "id" not in data or "data" not in data:
            return None
        try:
            value = float(data.get("timestamp"))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value >= 0 else None

    @staticmethod
    def _validate_result(result: ProcessResult) -> ProcessResult:
        if not isinstance(result, ProcessResult):
            raise TypeError("queue handlers and process middleware must return ProcessResult")
        return result

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

    async def _read_backend_status(self) -> Dict[str, int]:
        await self._ensure_initialized()
        content = await self._async_agfs.read(f"{self.path}/status")
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        elif hasattr(content, "content") and content.content is not None:
            content = content.content.decode("utf-8")
        status = json.loads(content)
        return {
            "pending": int(status["pending"]),
            "processing": int(status["processing"]),
        }

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
        """Clear queue."""
        await self._ensure_initialized()
        messages = await self.snapshot() if self._middlewares else []
        ctx = ClearContext(self.name, messages)
        try:
            await self._run_middleware("clear", ctx, self._clear)
            return ctx.committed
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[NamedQueue] Clear failed for {self.name}: {e}")
            return False

    async def _clear(self, ctx: ClearContext) -> None:
        await self._async_agfs.write(f"{self.path}/clear", b"")
        ctx.committed = True
