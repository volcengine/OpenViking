# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import abc
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

from openviking_cli.utils.logger import get_logger

if TYPE_CHECKING:
    from pyagfs import AGFSClient

logger = get_logger(__name__)


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
    error_count: int = 0
    errors: List[QueueError] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    @property
    def is_complete(self) -> bool:
        return self.pending == 0 and self.in_progress == 0


class EnqueueHookBase(abc.ABC):
    """Enqueue hook base class.

    All custom enqueue logic should inherit from this base class.
    Provides on_enqueue method for custom processing before message enqueue.
    """

    @abc.abstractmethod
    async def on_enqueue(self, data: Union[str, Dict[str, Any]]) -> Union[str, Dict[str, Any]]:
        """Called before message enqueue. Can modify data or perform validation."""
        return data


class DequeueHandlerBase(abc.ABC):
    """Dequeue handler base class, supports callback mechanism to report processing results."""

    _success_callback: Optional[Callable[[], None]] = None
    _error_callback: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None

    def set_callbacks(
        self,
        on_success: Callable[[], None],
        on_error: Callable[[str, Optional[Dict[str, Any]]], None],
    ) -> None:
        """Set callback functions."""
        self._success_callback = on_success
        self._error_callback = on_error

    def report_success(self) -> None:
        """Report processing success."""
        if self._success_callback:
            self._success_callback()

    def report_error(self, error_msg: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Report processing error."""
        if self._error_callback:
            self._error_callback(error_msg, data)

    @abc.abstractmethod
    async def on_dequeue(self, data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Called after message dequeue. Returns None to discard message."""
        if not data:
            return None
        return data


class NamedQueue:
    """NamedQueue: Operation class for specific named queue, supports status tracking."""

    MAX_ERRORS = 100

    def __init__(
        self,
        agfs: "AGFSClient",
        mount_point: str,
        name: str,
        enqueue_hook: Optional[EnqueueHookBase] = None,
        dequeue_handler: Optional[DequeueHandlerBase] = None,
    ):
        self.name = name
        self.path = f"{mount_point}/{name}"
        self._agfs = agfs
        self._enqueue_hook = enqueue_hook
        self._dequeue_handler = dequeue_handler
        self._initialized = False

        # Status tracking
        self._lock = threading.Lock()
        self._in_progress = 0
        self._processed = 0
        self._error_count = 0
        self._errors: List[QueueError] = []

        # Inject callbacks to handler
        if self._dequeue_handler:
            self._dequeue_handler.set_callbacks(
                on_success=self._on_process_success,
                on_error=self._on_process_error,
            )

    def _on_dequeue_start(self) -> None:
        """Called on dequeue."""
        with self._lock:
            self._in_progress += 1

    def _on_process_success(self) -> None:
        """Called on processing success."""
        with self._lock:
            self._in_progress -= 1
            self._processed += 1

    def _on_process_error(self, error_msg: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Called on processing failure."""
        with self._lock:
            self._in_progress -= 1
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
        pending = await self.size()
        with self._lock:
            return QueueStatus(
                pending=pending,
                in_progress=self._in_progress,
                processed=self._processed,
                error_count=self._error_count,
                errors=list(self._errors),
            )

    def reset_status(self) -> None:
        """Reset status counters."""
        with self._lock:
            self._in_progress = 0
            self._processed = 0
            self._error_count = 0
            self._errors = []

    def has_dequeue_handler(self) -> bool:
        """Check if dequeue handler exists."""
        return self._dequeue_handler is not None

    async def _ensure_initialized(self):
        """Ensure queue directory is created in AGFS."""
        if not self._initialized:
            try:
                self._agfs.mkdir(self.path)
            except Exception as e:
                if "exist" not in str(e).lower():
                    logger.warning(f"[NamedQueue] Failed to ensure queue {self.name}: {e}")
            self._initialized = True

    async def enqueue(self, data: Union[str, Dict[str, Any]]) -> str:
        """Send message to queue (enqueue)."""
        await self._ensure_initialized()
        enqueue_file = f"{self.path}/enqueue"

        # Execute enqueue hook
        if self._enqueue_hook:
            data = await self._enqueue_hook.on_enqueue(data)

        if isinstance(data, dict):
            data = json.dumps(data)

        msg_id = self._agfs.write(enqueue_file, data.encode("utf-8"))
        return msg_id if isinstance(msg_id, str) else str(msg_id)

    def _read_queue_message(self) -> Optional[Dict[str, Any]]:
        """Read and remove one message from the AGFS queue; return parsed dict or None.

        Normalises the various return types AGFSClient.read() may produce.
        """
        content = self._agfs.read(f"{self.path}/dequeue")
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
        """Get and remove message from queue, then invoke the dequeue handler."""
        await self._ensure_initialized()
        try:
            data = self._read_queue_message()
            if data is None:
                return None
            if self._dequeue_handler:
                self._on_dequeue_start()
                data = await self._dequeue_handler.on_dequeue(data)
            return data
        except Exception as e:
            logger.debug(f"[NamedQueue] Dequeue failed for {self.name}: {e}")
            return None

    async def dequeue_raw(self) -> Optional[Dict[str, Any]]:
        """Get and remove message from queue without invoking the handler."""
        await self._ensure_initialized()
        try:
            return self._read_queue_message()
        except Exception as e:
            logger.debug(f"[NamedQueue] Dequeue raw failed for {self.name}: {e}")
            return None

    async def process_dequeued(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Invoke the dequeue handler on already-fetched raw data.

        NOTE: caller must call _on_dequeue_start() before invoking this method
        so that in_progress is incremented atomically with the dequeue.
        """
        if self._dequeue_handler:
            return await self._dequeue_handler.on_dequeue(data)
        return data

    async def peek(self) -> Optional[Dict[str, Any]]:
        """Peek at head message without removing."""
        await self._ensure_initialized()
        peek_file = f"{self.path}/peek"

        try:
            content = self._agfs.read(peek_file)
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
            content = self._agfs.read(size_file)
            if not content:
                return 0
            if isinstance(content, bytes):
                return int(content.decode("utf-8").strip())
            elif isinstance(content, str):
                return int(content.strip())
            else:
                return 0
        except Exception as e:
            logger.debug(f"[NamedQueue] Get size failed for {self.name}: {e}")
            return 0

    async def clear(self) -> bool:
        """Clear queue."""
        await self._ensure_initialized()
        clear_file = f"{self.path}/clear"

        try:
            self._agfs.write(clear_file, b"")
            return True
        except Exception as e:
            logger.error(f"[NamedQueue] Clear failed for {self.name}: {e}")
            return False
