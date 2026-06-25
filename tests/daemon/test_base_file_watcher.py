"""Tests for BaseFileWatcher abstraction."""
import os
import time
from typing import Dict, List, Optional
from unittest.mock import MagicMock

from openviking.daemon.watchers.base_file_watcher import BaseFileWatcher


class ConcreteWatcher(BaseFileWatcher):
    """Concrete implementation for testing."""

    @property
    def tool_name(self) -> str:
        return "test_tool"

    def parse_line(self, line: str) -> Optional[Dict]:
        import json
        try:
            return json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        role = raw_event.get("role")
        content = raw_event.get("content", "")
        if role not in ("user", "assistant"):
            return None
        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": raw_event.get("timestamp"),
        }


class FakeCursorManager:
    def __init__(self):
        self.cursors = {}

    def get_cursor(self, file_path):
        from openviking.daemon.models import FileCursor
        return self.cursors.get(file_path, FileCursor(file_path=file_path))

    def update_cursor(self, file_path, position):
        from openviking.daemon.models import FileCursor
        self.cursors[file_path] = FileCursor(
            file_path=file_path,
            last_position=position,
            last_read_time=time.time(),
        )


def _make_watcher(tmp_path, batch_trigger_lines=50, batch_trigger_seconds=300):
    batches = []
    cursor_mgr = FakeCursorManager()
    watcher = ConcreteWatcher(
        watch_dir=str(tmp_path),
        cursor_manager=cursor_mgr,
        batch_callback=lambda events: batches.append(events),
        file_pattern="*.jsonl",
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
    )
    return watcher, batches, cursor_mgr


def test_matches_file_pattern_jsonl(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    assert w.matches_file_pattern("/foo/bar.jsonl")
    assert not w.matches_file_pattern("/foo/bar.txt")


def test_matches_file_pattern_exact(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    w.file_pattern = "history.md"
    assert w.matches_file_pattern("/foo/history.md")
    assert not w.matches_file_pattern("/foo/other.md")


def test_process_file_parses_and_normalizes(tmp_path):
    w, batches, _ = _make_watcher(tmp_path, batch_trigger_lines=2)

    # Create a test file
    test_file = tmp_path / "test.jsonl"
    test_file.write_text(
        '{"role": "user", "content": "hello"}\n'
        '{"role": "assistant", "content": "hi there"}\n'
    )

    # Process it
    w._process_file(str(test_file))

    # Should have flushed (2 lines >= batch_trigger_lines=2)
    assert len(batches) == 1
    assert len(batches[0]) == 2
    assert batches[0][0]["role"] == "user"
    assert batches[0][0]["tool_name"] == "test_tool"
    assert batches[0][1]["role"] == "assistant"


def test_process_file_skips_invalid_lines(tmp_path):
    w, batches, _ = _make_watcher(tmp_path, batch_trigger_lines=100)

    test_file = tmp_path / "test.jsonl"
    test_file.write_text(
        '{"role": "user", "content": "hello"}\n'
        'not valid json\n'
        '{"role": "tool", "content": "skipped"}\n'
    )

    w._process_file(str(test_file))
    w.flush()

    assert len(batches) == 1
    assert len(batches[0]) == 1  # only user message, tool role is filtered
    assert batches[0][0]["role"] == "user"


def test_incremental_read_via_cursor(tmp_path):
    w, batches, cm = _make_watcher(tmp_path, batch_trigger_lines=100)

    test_file = tmp_path / "test.jsonl"
    test_file.write_text('{"role": "user", "content": "first"}\n')
    w._process_file(str(test_file))

    # Append more content
    with open(str(test_file), "a", encoding="utf-8") as f:
        f.write('{"role": "assistant", "content": "second"}\n')
    w._process_file(str(test_file))

    w.flush()
    assert len(batches) == 1
    assert len(batches[0]) == 2


def test_filter_event_override(tmp_path):
    class FilteredWatcher(ConcreteWatcher):
        def filter_event(self, event):
            return "skip" not in event.get("content", "")

    batches = []
    w = FilteredWatcher(
        watch_dir=str(tmp_path),
        cursor_manager=FakeCursorManager(),
        batch_callback=lambda events: batches.append(events),
        file_pattern="*.jsonl",
        batch_trigger_lines=100,
        batch_trigger_seconds=300,
    )

    test_file = tmp_path / "test.jsonl"
    test_file.write_text(
        '{"role": "user", "content": "keep this"}\n'
        '{"role": "user", "content": "skip this please"}\n'
    )

    w._process_file(str(test_file))
    w.flush()

    assert len(batches) == 1
    assert len(batches[0]) == 1
    assert batches[0][0]["content"] == "keep this"


def test_crlf_byte_offset_accuracy(tmp_path):
    """Binary mode read ensures cursor byte offset is exact even with CRLF line endings."""
    w, batches, cm = _make_watcher(tmp_path, batch_trigger_lines=100)

    test_file = tmp_path / "test.jsonl"
    # Write CRLF-terminated lines (simulating Windows line endings)
    with open(str(test_file), "wb") as f:
        f.write(b'{"role": "user", "content": "hello"}\r\n')
        f.write(b'{"role": "assistant", "content": "hi"}\r\n')

    w._process_file(str(test_file))

    # Cursor should point to exact end of file (including \r\n bytes)
    cursor = cm.get_cursor(str(test_file))
    actual_size = os.path.getsize(str(test_file))
    assert cursor.last_position == actual_size, (
        f"Cursor {cursor.last_position} != file size {actual_size} (CRLF drift)"
    )

    # Append more and verify incremental read still works
    with open(str(test_file), "ab") as f:
        f.write(b'{"role": "user", "content": "second"}\r\n')
    w._process_file(str(test_file))

    cursor2 = cm.get_cursor(str(test_file))
    assert cursor2.last_position == os.path.getsize(str(test_file))

    w.flush()
    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_file_truncation_resets_cursor(tmp_path):
    """When a file is truncated/rotated (size < cursor), cursor resets to 0."""
    w, batches, cm = _make_watcher(tmp_path, batch_trigger_lines=100)

    test_file = tmp_path / "test.jsonl"
    test_file.write_text(
        '{"role": "user", "content": "first line"}\n'
        '{"role": "assistant", "content": "first response"}\n'
    )
    w._process_file(str(test_file))

    # Cursor should be at end of file
    cursor = cm.get_cursor(str(test_file))
    assert cursor.last_position > 0

    # Flush to clear buffer from first read
    w.flush()
    batches.clear()

    # Simulate file truncation/rotation: rewrite with shorter content
    test_file.write_text('{"role": "user", "content": "new"}\n')

    # First call detects truncation and resets cursor to 0
    w._process_file(str(test_file))
    cursor_reset = cm.get_cursor(str(test_file))
    assert cursor_reset.last_position == 0

    # Second call reads from beginning
    w._process_file(str(test_file))
    cursor2 = cm.get_cursor(str(test_file))
    assert cursor2.last_position == os.path.getsize(str(test_file))

    w.flush()
    assert len(batches) == 1
    assert batches[0][0]["content"] == "new"


def test_periodic_flush_on_quiet_session(tmp_path):
    """Periodic flush thread should auto-flush buffered events after timeout."""
    w, batches, _ = _make_watcher(
        tmp_path, batch_trigger_lines=100, batch_trigger_seconds=1
    )

    test_file = tmp_path / "test.jsonl"
    test_file.write_text('{"role": "user", "content": "lonely message"}\n')
    w._process_file(str(test_file))

    # Not flushed yet (line threshold not reached)
    assert len(batches) == 0

    # Start the watcher (launches periodic flush thread)
    w.start()
    try:
        # Wait for the periodic flush (1 second trigger + buffer)
        time.sleep(2.5)
    finally:
        w.stop()

    # The periodic flush thread should have flushed the buffer
    assert len(batches) == 1
    assert batches[0][0]["content"] == "lonely message"
