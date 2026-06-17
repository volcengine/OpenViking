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
