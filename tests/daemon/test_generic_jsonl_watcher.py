"""Tests for GenericJSONLWatcher with default and custom field mappings."""
import json
import os
import tempfile

from openviking.daemon.watchers.generic_jsonl_watcher import GenericJSONLWatcher


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


def _make_watcher(extra=None, batch_trigger_lines=50, batch_trigger_seconds=300):
    batches = []
    cursor_mgr = FakeCursorManager()
    watcher = GenericJSONLWatcher(
        watch_dir=tempfile.gettempdir(),
        cursor_manager=cursor_mgr,
        batch_callback=lambda lines: batches.append(lines),
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
        extra=extra,
    )
    return watcher, batches, cursor_mgr


# -- parse_line tests --

def test_parse_valid_jsonl():
    watcher, _, _ = _make_watcher()
    line = '{"role": "user", "content": "Hello"}'
    result = watcher.parse_line(line)
    assert result is not None
    assert result["role"] == "user"


def test_parse_invalid_jsonl():
    watcher, _, _ = _make_watcher()
    assert watcher.parse_line("not json") is None
    assert watcher.parse_line("") is None


# -- normalize_event with default mapping --

def test_normalize_event_default_user():
    watcher, _, _ = _make_watcher()
    raw = {"role": "user", "content": "Hello", "type": "message"}
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "user"
    assert result["content"] == "Hello"
    assert result["type"] == "message"


def test_normalize_event_default_assistant():
    watcher, _, _ = _make_watcher()
    raw = {"role": "assistant", "content": "Response", "type": "message"}
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "assistant"


def test_normalize_event_excludes_unknown_role():
    watcher, _, _ = _make_watcher()
    raw = {"role": "system", "content": "System msg", "type": "message"}
    assert watcher.normalize_event(raw) is None


def test_normalize_event_excludes_empty_content():
    watcher, _, _ = _make_watcher()
    raw = {"role": "user", "content": "", "type": "message"}
    assert watcher.normalize_event(raw) is None


def test_normalize_event_excludes_wrong_type():
    watcher, _, _ = _make_watcher()
    raw = {"role": "user", "content": "Hello", "type": "tool_call"}
    assert watcher.normalize_event(raw) is None


def test_normalize_event_passes_when_no_type_field():
    """When the type field is absent, the type check is skipped."""
    watcher, _, _ = _make_watcher()
    raw = {"role": "user", "content": "Hello"}
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "user"


# -- normalize_event with custom field mapping --

def test_normalize_event_custom_role_field():
    extra = {"role_field": "speaker", "user_role_value": "human", "assistant_role_value": "bot"}
    watcher, _, _ = _make_watcher(extra=extra)
    raw = {"speaker": "human", "content": "Hi", "type": "message"}
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "user"


def test_normalize_event_custom_content_field():
    extra = {"content_field": "text"}
    watcher, _, _ = _make_watcher(extra=extra)
    raw = {"role": "user", "text": "Custom content", "type": "message"}
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["content"] == "Custom content"


def test_normalize_event_custom_type_field():
    extra = {"type_field": "event_type", "message_type_value": "chat"}
    watcher, _, _ = _make_watcher(extra=extra)
    raw = {"role": "user", "content": "Hello", "event_type": "chat"}
    result = watcher.normalize_event(raw)
    assert result is not None

    raw2 = {"role": "user", "content": "Hello", "event_type": "tool_use"}
    assert watcher.normalize_event(raw2) is None


# -- _process_file integration --

def test_process_file():
    watcher, batches, cursor_mgr = _make_watcher(batch_trigger_lines=2)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "content": "Hello", "type": "message"}) + "\n")
        f.write(json.dumps({"role": "assistant", "content": "Hi", "type": "message"}) + "\n")
        f.write(json.dumps({"role": "system", "content": "ignored", "type": "message"}) + "\n")
        tmp_path = f.name

    try:
        watcher._process_file(tmp_path)
        assert len(batches) == 1
        assert len(batches[0]) == 2
        assert batches[0][0]["role"] == "user"
        assert batches[0][1]["role"] == "assistant"
        assert all(e["tool_name"] == "generic_jsonl" for e in batches[0])
        assert len(cursor_mgr.updates) == 1
    finally:
        os.unlink(tmp_path)


def test_tool_name():
    watcher, _, _ = _make_watcher()
    assert watcher.tool_name == "generic_jsonl"
