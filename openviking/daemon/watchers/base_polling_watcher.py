"""
Abstract base class for database/API-based watchers that use periodic polling.
Unlike BaseFileWatcher (watchdog + file cursor), this uses Thread + Event.wait(interval).
Subclasses implement query_new_events() and normalize_event().
"""
import os
import time
import threading
from abc import ABC, abstractmethod
from threading import Thread
from typing import Callable, Dict, List, Optional

from openviking.daemon.models import BatchBuffer
from openviking.daemon.cursor_manager import CursorManager
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class BasePollingWatcher(ABC):
    """Base class for database/API-based watchers that use periodic polling.

    Implements BaseWatcher Protocol (tool_name, start, stop, flush) without
    inheriting BaseFileWatcher. Uses a daemon Thread for polling instead of
    watchdog Observer.

    Subclasses must implement:
    - query_new_events(last_cursor): Query data source for new events
    - normalize_event(raw_event): Convert raw event to normalized format

    Optional overrides:
    - filter_event(event): Additional filtering
    - resolve_db_path(): Custom DB file discovery logic
    """

    def __init__(
        self,
        tool_name: str,
        watch_dir: str,
        cursor_manager: CursorManager,
        batch_callback: Callable[[List[Dict]], None],
        poll_interval: int = 30,
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
        extra: Optional[Dict] = None,
        **kwargs,
    ):
        self._tool_name = tool_name
        self.watch_dir = os.path.expanduser(watch_dir)
        self.cursor_manager = cursor_manager
        self.batch_callback = batch_callback
        self.poll_interval = poll_interval
        self.extra = extra or {}

        self._buffer = BatchBuffer()
        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds
        self._poll_thread: Optional[Thread] = None
        self._stop_event = threading.Event()

    # --- BaseWatcher Protocol ---

    @property
    def tool_name(self) -> str:
        return self._tool_name

    def start(self) -> None:
        self._stop_event.clear()
        self._poll_thread = Thread(
            target=self._poll_loop, daemon=True, name=f"poll-{self._tool_name}"
        )
        self._poll_thread.start()
        logger.info(
            "[%s] Polling watcher started (interval=%ds, dir=%s)",
            self._tool_name, self.poll_interval, self.watch_dir,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        logger.info("[%s] Polling watcher stopped", self._tool_name)

    def flush(self) -> None:
        self._flush_buffer()

    # --- Subclasses must implement ---

    @abstractmethod
    def query_new_events(self, last_cursor: int) -> List[Dict]:
        """Query data source for events newer than last_cursor.

        Args:
            last_cursor: Last processed position (rowid/timestamp/offset)

        Returns:
            List of raw event dicts. Each MUST include '_cursor_position' field.
        """
        ...

    @abstractmethod
    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """Convert raw event to normalized format.

        Returns None to skip. Output must have at minimum:
        {role, content, type, timestamp, session_id}
        """
        ...

    # --- Optional overrides ---

    def filter_event(self, event: Dict) -> bool:
        """Additional filtering. Return True to keep, False to skip."""
        return True

    def resolve_db_path(self) -> Optional[str]:
        """Resolve DB file path. Default: watch_dir/extra['db_filename'].
        Subclasses can override for more complex discovery.
        """
        db_filename = self.extra.get("db_filename", "state.vscdb")
        candidate = os.path.join(self.watch_dir, db_filename)
        if os.path.exists(candidate):
            return candidate
        return None

    # --- Internal ---

    def _poll_loop(self):
        """Main polling loop. Runs in a daemon thread."""
        cursor_key = self.watch_dir

        while not self._stop_event.is_set():
            try:
                db_path = self.resolve_db_path()
                if db_path is None:
                    logger.debug("[%s] DB not found, retrying...", self._tool_name)
                    self._stop_event.wait(self.poll_interval)
                    continue

                cursor = self.cursor_manager.get_cursor(cursor_key)
                raw_events = self.query_new_events(cursor.last_position)

                if raw_events:
                    new_position = cursor.last_position
                    for raw in raw_events:
                        # Always advance cursor for every raw event seen,
                        # even if normalize/filter drops it. Otherwise filtered
                        # rows would be re-queried on every poll cycle.
                        pos = raw.get("_cursor_position", 0)
                        if pos > new_position:
                            new_position = pos

                        normalized = self.normalize_event(raw)
                        if normalized is None:
                            continue
                        if not self.filter_event(normalized):
                            continue

                        normalized["tool_name"] = self._tool_name
                        self._buffer.add_line(normalized, byte_size=0)

                    if new_position > cursor.last_position:
                        self.cursor_manager.update_cursor(cursor_key, new_position)

                self._check_batch_trigger()

            except Exception as e:
                logger.error(
                    "[%s] Poll error: %s", self._tool_name, e, exc_info=True
                )

            self._stop_event.wait(self.poll_interval)

    def _check_batch_trigger(self):
        """Check if batch trigger conditions are met."""
        if self._buffer.is_empty():
            return

        line_count = len(self._buffer.lines)
        age = (
            time.time() - self._buffer.created_at
            if self._buffer.created_at > 0
            else 0
        )

        if (
            line_count >= self.batch_trigger_lines
            or age >= self.batch_trigger_seconds
        ):
            self._flush_buffer()

    def _flush_buffer(self):
        """Flush buffered events via batch_callback."""
        if self._buffer.is_empty():
            return

        events = self._buffer.lines.copy()
        logger.info("[%s] Flushing %d events", self._tool_name, len(events))
        try:
            self.batch_callback(events)
            self._buffer.clear()
        except Exception as e:
            logger.error(
                "[%s] Batch callback failed: %s", self._tool_name, e, exc_info=True
            )
