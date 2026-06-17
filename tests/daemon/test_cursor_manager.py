"""Tests for CursorManager."""
import os
import tempfile

import pytest

from openviking.daemon.cursor_manager import CursorManager


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_default_cursor(temp_db):
    manager = CursorManager(temp_db)
    cursor = manager.get_cursor("/path/to/file.jsonl")
    assert cursor.last_position == 0
    assert cursor.last_read_time == 0.0


def test_save_and_load_cursor(temp_db):
    manager = CursorManager(temp_db)
    manager.update_cursor("/path/to/file.jsonl", 1024)

    cursor = manager.get_cursor("/path/to/file.jsonl")
    assert cursor.last_position == 1024
    assert cursor.last_read_time > 0


def test_persist_across_instances(temp_db):
    manager1 = CursorManager(temp_db)
    manager1.update_cursor("/path/to/file.jsonl", 2048)

    manager2 = CursorManager(temp_db)
    cursor = manager2.get_cursor("/path/to/file.jsonl")
    assert cursor.last_position == 2048


def test_get_all_cursors(temp_db):
    manager = CursorManager(temp_db)
    manager.update_cursor("/path/file1.jsonl", 100)
    manager.update_cursor("/path/file2.jsonl", 200)

    cursors = manager.get_all_cursors()
    assert len(cursors) == 2
    assert cursors["/path/file1.jsonl"].last_position == 100
    assert cursors["/path/file2.jsonl"].last_position == 200


def test_update_existing_cursor(temp_db):
    manager = CursorManager(temp_db)
    manager.update_cursor("/path/file.jsonl", 100)
    manager.update_cursor("/path/file.jsonl", 500)

    cursor = manager.get_cursor("/path/file.jsonl")
    assert cursor.last_position == 500
