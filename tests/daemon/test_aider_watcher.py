"""Tests for AiderWatcher parsing logic."""
import time
from typing import Dict, Optional

from openviking.daemon.watchers.aider_watcher import AiderWatcher


class FakeCursorManager:
    """Minimal stub for testing."""
    def __init__(self):
        self.cursors = {}
        self.updates = []

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
        self.updates.append((file_path, position))


def _make_watcher(tmp_path, batch_trigger_lines=50, batch_trigger_seconds=300):
    batches = []
    cursor_mgr = FakeCursorManager()
    watcher = AiderWatcher(
        watch_dir=str(tmp_path),
        cursor_manager=cursor_mgr,
        batch_callback=lambda events: batches.append(events),
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
    )
    return watcher, batches, cursor_mgr


def test_matches_file_pattern(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    assert w.matches_file_pattern("/project/.aider.chat.history.md")
    assert not w.matches_file_pattern("/project/other.md")
    assert not w.matches_file_pattern("/project/aider.chat.history.md")


def test_parse_single_user_assistant_block(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    content = (
        "# aider chat started at 2024-01-15 10:30:00\n"
        "\n"
        "> /path/to/project\n"
        "\n"
        "#### user:\n"
        "How do I implement a REST API in Flask?\n"
        "\n"
        "#### assistant:\n"
        "Here's how to create a basic Flask REST API:\n"
        "Use Flask and add routes.\n"
    )
    events = w._parse_aider_content(content)
    assert len(events) == 2
    assert events[0]["role"] == "user"
    assert events[0]["content"] == "How do I implement a REST API in Flask?"
    assert events[1]["role"] == "assistant"
    assert "Flask REST API" in events[1]["content"]


def test_parse_multiple_blocks(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    content = (
        "# aider chat started at 2024-01-15 10:30:00\n"
        "> /my/project\n"
        "#### user:\n"
        "First question\n"
        "#### assistant:\n"
        "First answer\n"
        "#### user:\n"
        "Second question\n"
        "#### assistant:\n"
        "Second answer\n"
    )
    events = w._parse_aider_content(content)
    assert len(events) == 4
    assert events[0]["role"] == "user"
    assert events[0]["content"] == "First question"
    assert events[1]["role"] == "assistant"
    assert events[1]["content"] == "First answer"
    assert events[2]["role"] == "user"
    assert events[2]["content"] == "Second question"
    assert events[3]["role"] == "assistant"
    assert events[3]["content"] == "Second answer"


def test_timestamp_and_project_extraction(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    content = (
        "# aider chat started at 2024-01-15 10:30:00\n"
        "> /home/user/myproject\n"
        "#### user:\n"
        "Hello\n"
    )
    events = w._parse_aider_content(content)
    assert len(events) == 1
    assert events[0]["timestamp"] == "2024-01-15 10:30:00"
    assert events[0]["project_name"] == "/home/user/myproject"


def test_empty_content_handling(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    events = w._parse_aider_content("")
    assert events == []

    events = w._parse_aider_content("# aider chat started at 2024-01-15 10:30:00\n")
    assert events == []


def test_multiline_content(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    content = (
        "#### user:\n"
        "Line one\n"
        "Line two\n"
        "Line three\n"
    )
    events = w._parse_aider_content(content)
    assert len(events) == 1
    assert events[0]["content"] == "Line one\nLine two\nLine three"


def test_process_file_integration(tmp_path):
    w, batches, _ = _make_watcher(tmp_path, batch_trigger_lines=2)

    test_file = tmp_path / ".aider.chat.history.md"
    test_file.write_text(
        "# aider chat started at 2024-01-15 10:30:00\n"
        "> /project\n"
        "#### user:\n"
        "Hello\n"
        "#### assistant:\n"
        "Hi there\n",
        encoding="utf-8",
    )

    w._process_file(str(test_file))

    assert len(batches) == 1
    assert len(batches[0]) == 2
    assert batches[0][0]["role"] == "user"
    assert batches[0][0]["tool_name"] == "aider"
    assert batches[0][1]["role"] == "assistant"


def test_incremental_read(tmp_path):
    w, batches, _ = _make_watcher(tmp_path, batch_trigger_lines=100)

    test_file = tmp_path / ".aider.chat.history.md"
    test_file.write_text(
        "#### user:\n"
        "First message\n",
        encoding="utf-8",
    )
    w._process_file(str(test_file))

    # Append more content
    with open(str(test_file), "a", encoding="utf-8") as f:
        f.write(
            "#### assistant:\n"
            "Response\n"
        )
    w._process_file(str(test_file))

    w.flush()
    assert len(batches) == 1
    assert len(batches[0]) == 2
    assert batches[0][0]["role"] == "user"
    assert batches[0][1]["role"] == "assistant"


def test_tool_name(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    assert w.tool_name == "aider"
