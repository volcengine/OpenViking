"""Tests for ClaudeCodeWatcher parsing and filtering logic."""
import json
import os
import tempfile

from openviking.daemon.watchers.claude_code_watcher import ClaudeCodeWatcher


class FakeCursorManager:
    """Minimal stub for testing."""
    def __init__(self):
        self.cursors = {}
        self.updates = []

    def get_cursor(self, file_path):
        from openviking.daemon.models import FileCursor
        return self.cursors.get(file_path, FileCursor(file_path=file_path))

    def update_cursor(self, file_path, position):
        self.updates.append((file_path, position))


def _make_watcher(batch_trigger_lines=50, batch_trigger_seconds=300):
    batches = []
    cursor_mgr = FakeCursorManager()
    watcher = ClaudeCodeWatcher(
        watch_dir=tempfile.gettempdir(),
        cursor_manager=cursor_mgr,
        batch_callback=lambda lines: batches.append(lines),
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
    )
    return watcher, batches, cursor_mgr


def test_tool_name():
    watcher, _, _ = _make_watcher()
    assert watcher.tool_name == "claude_code"


def test_parse_valid_jsonl_line():
    watcher, _, _ = _make_watcher()
    line = '{"timestamp": "2026-06-15T10:30:00Z", "role": "user", "content": "Hello", "type": "message"}'
    event = watcher.parse_line(line)
    assert event is not None
    assert event["role"] == "user"
    assert event["content"] == "Hello"


def test_parse_invalid_line():
    watcher, _, _ = _make_watcher()
    assert watcher.parse_line("not valid json") is None
    assert watcher.parse_line("") is None


def test_normalize_event_user_message():
    watcher, _, _ = _make_watcher()
    raw = {"role": "user", "type": "message", "content": "Hello", "timestamp": "2026-06-15T10:30:00Z"}
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "user"
    assert result["type"] == "message"
    assert result["content"] == "Hello"


def test_normalize_event_assistant_message():
    watcher, _, _ = _make_watcher()
    raw = {"role": "assistant", "type": "message", "content": "AI answer"}
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "assistant"
    assert result["content"] == "AI answer"


def test_normalize_event_excludes_system_role():
    watcher, _, _ = _make_watcher()
    raw = {"role": "system", "type": "message", "content": "System msg"}
    assert watcher.normalize_event(raw) is None


def test_normalize_event_excludes_tool_call():
    watcher, _, _ = _make_watcher()
    raw = {"role": "assistant", "type": "tool_call", "content": "call"}
    assert watcher.normalize_event(raw) is None


def test_normalize_event_excludes_tool_result():
    watcher, _, _ = _make_watcher()
    raw = {"role": "assistant", "type": "tool_result", "content": "result"}
    assert watcher.normalize_event(raw) is None


def test_filter_event_keeps_messages():
    watcher, _, _ = _make_watcher()
    event = {"role": "user", "type": "message", "content": "Hello", "tool_name": "claude_code"}
    assert watcher.filter_event(event) is True


def test_process_file():
    """Test that _process_file reads, parses, normalizes, and buffers events."""
    watcher, batches, cursor_mgr = _make_watcher(batch_trigger_lines=2)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "type": "message", "content": "Hello"}) + "\n")
        f.write(json.dumps({"role": "assistant", "type": "message", "content": "Hi"}) + "\n")
        f.write(json.dumps({"role": "system", "type": "message", "content": "ignored"}) + "\n")
        tmp_path = f.name

    try:
        watcher._process_file(tmp_path)
        # batch_trigger_lines=2, so 2 valid events should trigger flush
        assert len(batches) == 1
        assert len(batches[0]) == 2
        assert batches[0][0]["role"] == "user"
        assert batches[0][1]["role"] == "assistant"
        assert all(e["tool_name"] == "claude_code" for e in batches[0])
        # Cursor should have been updated
        assert len(cursor_mgr.updates) == 1
        assert cursor_mgr.updates[0][0] == tmp_path
    finally:
        os.unlink(tmp_path)


def test_force_flush_empty_buffer():
    watcher, batches, _ = _make_watcher()
    watcher.flush()
    assert len(batches) == 0


def test_force_flush_with_data():
    watcher, batches, _ = _make_watcher()
    watcher._buffer.add_line({"role": "user", "content": "test", "tool_name": "claude_code"}, 10)
    watcher.flush()
    assert len(batches) == 1
