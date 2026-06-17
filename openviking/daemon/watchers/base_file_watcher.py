"""
Abstract base class for file-append based watchers.
Handles watchdog Observer lifecycle, cursor management, and batch buffering.
Subclasses only need to implement parse_line() and normalize_event().
"""
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Dict, List, Optional

from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from watchdog.observers import Observer

from openviking.daemon.models import BatchBuffer, FileCursor
from openviking.daemon.cursor_manager import CursorManager
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class _FileHandler(FileSystemEventHandler):
    """Internal watchdog handler that delegates to BaseFileWatcher methods."""

    def __init__(self, watcher: "BaseFileWatcher"):
        super().__init__()
        self._watcher = watcher

    def on_modified(self, event):
        if event.is_directory:
            return
        file_path = event.src_path
        if not self._watcher.matches_file_pattern(file_path):
            return
        self._watcher._process_file(file_path)


class BaseFileWatcher(ABC):
    """
    Abstract base for file-append based watchers.

    Subclasses must implement:
    - tool_name (property): Return tool identifier string
    - parse_line(line): Parse a raw text line into a raw event dict (or None)
    - normalize_event(raw_event): Convert raw event to normalized dict (or None to skip)

    Optional overrides:
    - filter_event(event): Additional filtering. Return True to keep, False to skip.
    - matches_file_pattern(path): Custom file matching logic.
    """

    def __init__(
        self,
        watch_dir: str,
        cursor_manager: CursorManager,
        batch_callback: Callable[[List[Dict]], None],
        file_pattern: str = "*.jsonl",
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
    ):
        self.watch_dir = os.path.expanduser(watch_dir)
        self.cursor_manager = cursor_manager
        self.batch_callback = batch_callback
        self.file_pattern = file_pattern
        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds

        self._buffer = BatchBuffer()
        self._observer: Optional[Observer] = None
        self._handler: Optional[_FileHandler] = None

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Return tool identifier (e.g. 'claude_code', 'aider')."""
        ...

    @abstractmethod
    def parse_line(self, line: str) -> Optional[Dict]:
        """Parse a raw text line into a raw event dict. Return None to skip."""
        ...

    @abstractmethod
    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """
        Convert a raw event dict to normalized format.
        Normalized format must have at minimum: role, content, type, tool_name.
        Return None to skip this event.
        """
        ...

    def filter_event(self, event: Dict) -> bool:
        """Additional filtering. Override for tool-specific rules. Default: keep all."""
        return True

    def matches_file_pattern(self, file_path: str) -> bool:
        """Check if file matches the watcher's file pattern."""
        filename = os.path.basename(file_path)
        if self.file_pattern.startswith("*."):
            return filename.endswith(self.file_pattern[1:])
        elif self.file_pattern.startswith("."):
            return filename.startswith(self.file_pattern) or filename == self.file_pattern.lstrip(".")
        return filename == self.file_pattern

    def start(self) -> None:
        """Start the watchdog Observer."""
        self._handler = _FileHandler(self)
        self._observer = Observer()
        self._observer.schedule(self._handler, self.watch_dir, recursive=True)
        self._observer.daemon = True
        self._observer.start()
        logger.info("[%s] Watcher started on %s", self.tool_name, self.watch_dir)

    def stop(self) -> None:
        """Stop the watchdog Observer."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        logger.info("[%s] Watcher stopped", self.tool_name)

    def flush(self) -> None:
        """Force flush the buffer."""
        self._flush_buffer()

    def _process_file(self, file_path: str):
        """Read new content from file using cursor, parse, normalize, buffer."""
        try:
            cursor = self.cursor_manager.get_cursor(file_path)
            file_size = os.path.getsize(file_path)

            if file_size <= cursor.last_position:
                return

            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(cursor.last_position)
                new_content = f.read()

            new_position = cursor.last_position + len(new_content.encode("utf-8"))

            for line in new_content.splitlines():
                line = line.strip()
                if not line:
                    continue

                raw_event = self.parse_line(line)
                if raw_event is None:
                    continue

                normalized = self.normalize_event(raw_event)
                if normalized is None:
                    continue

                if not self.filter_event(normalized):
                    continue

                # Ensure tool_name is set
                normalized["tool_name"] = self.tool_name

                byte_size = len(line.encode("utf-8"))
                self._buffer.add_line(normalized, byte_size)

            self.cursor_manager.update_cursor(file_path, new_position)

            self._check_batch_trigger()

        except Exception as e:
            logger.error("[%s] Error processing file %s: %s", self.tool_name, file_path, e)

    def _check_batch_trigger(self):
        """Check if batch trigger conditions are met."""
        if self._buffer.is_empty():
            return

        line_count = len(self._buffer.lines)
        age = time.time() - self._buffer.created_at if self._buffer.created_at > 0 else 0

        if line_count >= self.batch_trigger_lines or age >= self.batch_trigger_seconds:
            self._flush_buffer()

    def _flush_buffer(self):
        """Flush buffered events via batch_callback."""
        if self._buffer.is_empty():
            return

        events = self._buffer.lines.copy()
        self._buffer.clear()

        logger.info("[%s] Flushing %d events", self.tool_name, len(events))
        try:
            self.batch_callback(events)
        except Exception as e:
            logger.error("[%s] Batch callback failed: %s", self.tool_name, e)
