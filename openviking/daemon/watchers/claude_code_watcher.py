"""
Claude Code JSONL file watcher using watchdog for incremental monitoring.
"""
import json
import os
import time
from typing import Callable, Dict, List, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from openviking.daemon.models import BatchBuffer
from openviking.daemon.cursor_manager import CursorManager
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class ClaudeCodeLogHandler(FileSystemEventHandler):
    """Handles file system events for Claude Code JSONL files."""

    def __init__(
        self,
        cursor_manager: CursorManager,
        batch_callback: Callable[[List[Dict]], None],
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
    ):
        super().__init__()
        self.cursor_manager = cursor_manager
        self.batch_callback = batch_callback
        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds
        self.buffer = BatchBuffer()
        self.last_batch_time = time.time()

    def on_modified(self, event):
        """Handle file modification events."""
        if event.is_directory or not event.src_path.endswith(".jsonl"):
            return
        try:
            self._process_file(event.src_path)
        except Exception as e:
            logger.error("Error processing %s: %s", event.src_path, e)

    def _process_file(self, file_path: str):
        """Incrementally read new lines from a file and add to buffer."""
        cursor = self.cursor_manager.get_cursor(file_path)
        current_size = os.path.getsize(file_path)

        if current_size <= cursor.last_position:
            return

        with open(file_path, "r", encoding="utf-8") as f:
            f.seek(cursor.last_position)
            new_lines = f.readlines()
            new_position = f.tell()

        parsed_events = []
        for line in new_lines:
            event = self._parse_line(line)
            if event:
                parsed_events.append(event)

        filtered_events = self._filter_events(parsed_events)

        for event in filtered_events:
            self.buffer.add_line(event, len(json.dumps(event)))

        self.cursor_manager.update_cursor(file_path, new_position)
        self._check_batch_trigger()

    @staticmethod
    def _parse_line(line: str) -> Optional[Dict]:
        """Parse a single JSONL line. Returns None on failure."""
        try:
            return json.loads(line.strip())
        except (json.JSONDecodeError, AttributeError):
            return None

    @staticmethod
    def _filter_events(events: List[Dict]) -> List[Dict]:
        """Filter to only user/assistant message events."""
        return [
            e for e in events
            if e.get("role") in ("user", "assistant") and e.get("type") == "message"
        ]

    def _check_batch_trigger(self):
        """Check if batch processing should be triggered."""
        should_trigger = False

        if time.time() - self.last_batch_time >= self.batch_trigger_seconds:
            should_trigger = True

        if len(self.buffer.lines) >= self.batch_trigger_lines:
            should_trigger = True

        if should_trigger and not self.buffer.is_empty():
            self._flush_buffer()

    def _flush_buffer(self):
        """Send buffered events to the batch callback and reset buffer."""
        if self.buffer.is_empty():
            return

        logger.info("Flushing batch with %d events", len(self.buffer.lines))

        try:
            self.batch_callback(self.buffer.lines.copy())
        except Exception as e:
            logger.error("Error in batch callback: %s", e)

        self.buffer.clear()
        self.last_batch_time = time.time()

    def force_flush(self):
        """Force flush the buffer regardless of trigger conditions."""
        self._flush_buffer()


class ClaudeCodeWatcher:
    """Monitors Claude Code JSONL log files for new conversation data."""

    def __init__(
        self,
        watch_dir: str,
        cursor_manager: CursorManager,
        batch_callback: Callable[[List[Dict]], None],
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
    ):
        self.watch_dir = watch_dir
        self.cursor_manager = cursor_manager
        self.batch_callback = batch_callback
        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds
        self.observer: Optional[Observer] = None
        self.handler: Optional[ClaudeCodeLogHandler] = None

    def start(self):
        """Start watching for file changes."""
        self.handler = ClaudeCodeLogHandler(
            cursor_manager=self.cursor_manager,
            batch_callback=self.batch_callback,
            batch_trigger_lines=self.batch_trigger_lines,
            batch_trigger_seconds=self.batch_trigger_seconds,
        )

        self.observer = Observer()
        self.observer.schedule(self.handler, self.watch_dir, recursive=True)
        self.observer.start()
        logger.info("Claude Code watcher started on %s", self.watch_dir)

    def stop(self):
        """Stop watching for file changes."""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("Claude Code watcher stopped")

    def flush(self):
        """Force flush any buffered events."""
        if self.handler:
            self.handler.force_flush()
