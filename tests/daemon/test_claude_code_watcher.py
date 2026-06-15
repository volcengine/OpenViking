"""Tests for ClaudeCodeWatcher parsing and filtering logic."""
from openviking.daemon.watchers.claude_code_watcher import ClaudeCodeLogHandler


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


def _make_handler(batch_trigger_lines=50, batch_trigger_seconds=300):
    batches = []
    cursor_mgr = FakeCursorManager()
    handler = ClaudeCodeLogHandler(
        cursor_manager=cursor_mgr,
        batch_callback=lambda lines: batches.append(lines),
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
    )
    return handler, batches


def test_parse_valid_jsonl_line():
    handler, _ = _make_handler()
    line = '{"timestamp": "2026-06-15T10:30:00Z", "role": "user", "content": "Hello", "type": "message"}'
    event = handler._parse_line(line)
    assert event is not None
    assert event["role"] == "user"
    assert event["content"] == "Hello"


def test_parse_invalid_line():
    handler, _ = _make_handler()
    assert handler._parse_line("not valid json") is None
    assert handler._parse_line("") is None


def test_filter_keeps_user_and_assistant_messages():
    handler, _ = _make_handler()
    events = [
        {"role": "user", "type": "message", "content": "User question"},
        {"role": "assistant", "type": "message", "content": "AI answer"},
        {"role": "assistant", "type": "tool_call", "content": "Tool call"},
        {"role": "system", "type": "message", "content": "System msg"},
    ]
    filtered = handler._filter_events(events)
    assert len(filtered) == 2
    assert all(e["type"] == "message" for e in filtered)


def test_filter_excludes_tool_calls():
    handler, _ = _make_handler()
    events = [
        {"role": "assistant", "type": "tool_call", "content": "call"},
        {"role": "assistant", "type": "tool_result", "content": "result"},
    ]
    filtered = handler._filter_events(events)
    assert len(filtered) == 0


def test_buffer_add_and_flush():
    handler, batches = _make_handler(batch_trigger_lines=2)
    handler.buffer.add_line({"role": "user", "content": "a"}, 10)
    handler.buffer.add_line({"role": "assistant", "content": "b"}, 10)
    handler._check_batch_trigger()
    assert len(batches) == 1
    assert len(batches[0]) == 2


def test_force_flush_empty_buffer():
    handler, batches = _make_handler()
    handler.force_flush()
    assert len(batches) == 0


def test_force_flush_with_data():
    handler, batches = _make_handler()
    handler.buffer.add_line({"role": "user", "content": "test"}, 10)
    handler.force_flush()
    assert len(batches) == 1
