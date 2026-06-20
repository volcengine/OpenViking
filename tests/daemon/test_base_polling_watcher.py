"""
Unit tests for BasePollingWatcher.
Tests poll loop, buffer management, batch trigger, and BaseWatcher Protocol compliance.
"""
import time
import pytest
from typing import Dict, List, Optional
from unittest.mock import MagicMock

from openviking.daemon.watchers.base_polling_watcher import BasePollingWatcher
from openviking.daemon.watchers import BaseWatcher
from openviking.daemon.models import FileCursor


class FakeCursorManager:
    """In-memory cursor manager for testing."""

    def __init__(self):
        self.cursors = {}

    def get_cursor(self, file_path):
        return self.cursors.get(file_path, FileCursor(file_path=file_path))

    def update_cursor(self, file_path, position):
        self.cursors[file_path] = FileCursor(
            file_path=file_path,
            last_position=position,
            last_read_time=time.time(),
        )


class ConcretePollingWatcher(BasePollingWatcher):
    """Concrete implementation for testing."""

    def __init__(self, mock_events=None, **kwargs):
        super().__init__(**kwargs)
        self._mock_events = mock_events or []
        self._query_count = 0

    def query_new_events(self, last_cursor: int) -> List[Dict]:
        self._query_count += 1
        return [e for e in self._mock_events if e.get("_cursor_position", 0) > last_cursor]

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        role = raw_event.get("role")
        content = raw_event.get("content", "")
        if not role or not content:
            return None
        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": raw_event.get("timestamp"),
            "session_id": raw_event.get("session_id"),
        }


# --- Protocol Compliance ---

def test_implements_base_watcher_protocol():
    """BasePollingWatcher must satisfy BaseWatcher Protocol."""
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="test",
        watch_dir="/tmp/test",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    assert isinstance(w, BaseWatcher)


def test_tool_name_property():
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="my_tool",
        watch_dir="/tmp",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    assert w.tool_name == "my_tool"


# --- Buffer and Batch Trigger ---

def test_flush_empty_buffer_no_callback():
    """Flushing empty buffer should not call batch_callback."""
    batches = []
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="test",
        watch_dir="/tmp",
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
    )
    w.flush()
    assert len(batches) == 0


def test_flush_nonempty_buffer_calls_callback():
    """Flushing non-empty buffer should call batch_callback with events."""
    batches = []
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="test",
        watch_dir="/tmp",
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
    )
    w._buffer.add_line({"role": "user", "content": "hello"}, byte_size=0)
    w.flush()

    assert len(batches) == 1
    assert batches[0][0]["role"] == "user"
    assert w._buffer.is_empty()


def test_batch_trigger_by_line_count():
    """Buffer should flush when line count reaches trigger."""
    batches = []
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="test",
        watch_dir="/tmp",
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        batch_trigger_lines=3,
    )

    for i in range(3):
        w._buffer.add_line({"role": "user", "content": f"msg {i}"}, byte_size=0)

    w._check_batch_trigger()
    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_batch_trigger_by_time():
    """Buffer should flush when age exceeds trigger seconds."""
    batches = []
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="test",
        watch_dir="/tmp",
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        batch_trigger_lines=100,  # high line trigger
        batch_trigger_seconds=1,  # low time trigger
    )

    w._buffer.add_line({"role": "user", "content": "old msg"}, byte_size=0)
    # Manually age the buffer
    w._buffer.created_at = time.time() - 5

    w._check_batch_trigger()
    assert len(batches) == 1


def test_no_trigger_below_thresholds():
    """Buffer should NOT flush when below both thresholds."""
    batches = []
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="test",
        watch_dir="/tmp",
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        batch_trigger_lines=100,
        batch_trigger_seconds=300,
    )

    w._buffer.add_line({"role": "user", "content": "msg"}, byte_size=0)
    w._check_batch_trigger()
    assert len(batches) == 0


# --- Poll Loop ---

def test_poll_loop_processes_events(tmp_path):
    """Poll loop should query, normalize, buffer, and trigger batch."""
    batches = []
    cm = FakeCursorManager()

    events = [
        {"role": "user", "content": "Hello", "_cursor_position": 1},
        {"role": "assistant", "content": "Hi there", "_cursor_position": 2},
    ]

    w = ConcretePollingWatcher(
        mock_events=events,
        tool_name="test",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        poll_interval=1,
        batch_trigger_lines=2,
    )
    # Override resolve_db_path to return a valid path
    w.resolve_db_path = lambda: str(tmp_path / "fake.db")

    w.start()
    time.sleep(2.5)  # Wait for at least 1 poll cycle
    w.stop()

    assert len(batches) >= 1
    assert all(e["tool_name"] == "test" for e in batches[0])
    assert batches[0][0]["role"] == "user"
    assert batches[0][1]["role"] == "assistant"


def test_poll_loop_updates_cursor(tmp_path):
    """Poll loop should update cursor after processing events."""
    cm = FakeCursorManager()
    events = [
        {"role": "user", "content": "msg", "_cursor_position": 42},
    ]

    w = ConcretePollingWatcher(
        mock_events=events,
        tool_name="test",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: None,
        poll_interval=1,
        batch_trigger_lines=100,
    )
    w.resolve_db_path = lambda: str(tmp_path / "fake.db")

    w.start()
    time.sleep(1.5)
    w.stop()

    cursor = cm.get_cursor(str(tmp_path))
    assert cursor.last_position == 42


def test_poll_loop_skips_when_db_not_found(tmp_path):
    """Poll loop should gracefully skip when DB doesn't exist."""
    cm = FakeCursorManager()
    w = ConcretePollingWatcher(
        tool_name="test",
        watch_dir=str(tmp_path / "nonexistent"),
        cursor_manager=cm,
        batch_callback=lambda e: None,
        poll_interval=1,
    )
    # Default resolve_db_path returns None for nonexistent dir

    w.start()
    time.sleep(1.5)
    w.stop()
    # Should not raise — just skip gracefully


def test_filter_event_skips_unwanted(tmp_path):
    """filter_event returning False should skip the event."""
    batches = []
    cm = FakeCursorManager()

    events = [
        {"role": "user", "content": "keep this", "_cursor_position": 1},
        {"role": "user", "content": "skip", "_cursor_position": 2},
    ]

    class FilteringWatcher(ConcretePollingWatcher):
        def filter_event(self, event):
            return event["content"] != "skip"

    w = FilteringWatcher(
        mock_events=events,
        tool_name="test",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        poll_interval=1,
        batch_trigger_lines=10,
    )
    w.resolve_db_path = lambda: str(tmp_path / "fake.db")

    w.start()
    time.sleep(1.5)
    w.stop()
    w.flush()

    all_events = [e for batch in batches for e in batch]
    assert len(all_events) == 1
    assert all_events[0]["content"] == "keep this"


# --- Callback failure resilience ---

def test_callback_failure_does_not_crash(tmp_path):
    """batch_callback failure should be caught, not crash the watcher."""
    cm = FakeCursorManager()
    events = [{"role": "user", "content": "msg", "_cursor_position": 1}]

    def failing_callback(e):
        raise RuntimeError("simulated failure")

    w = ConcretePollingWatcher(
        mock_events=events,
        tool_name="test",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=failing_callback,
        poll_interval=1,
        batch_trigger_lines=1,
    )
    w.resolve_db_path = lambda: str(tmp_path / "fake.db")

    w.start()
    time.sleep(2)
    w.stop()
    # Should not raise — error is logged and caught
