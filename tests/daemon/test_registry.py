"""Tests for watcher registry."""
import pytest
from openviking.daemon.watchers.registry import (
    create_watcher,
    list_available_watchers,
    _WATCHER_REGISTRY,
    register_watcher,
)


def test_list_available_includes_claude_code():
    assert "claude_code" in list_available_watchers()


def test_create_watcher_unknown_raises():
    with pytest.raises(ValueError, match="Unknown watcher tool"):
        create_watcher("nonexistent_tool", watch_dir="/tmp", cursor_manager=None,
                       batch_callback=lambda x: None)


def test_register_watcher_decorator():
    class FakeWatcher:
        pass

    @register_watcher("test_tool_xyz")
    class Decorated:
        pass

    assert "test_tool_xyz" in _WATCHER_REGISTRY
    # cleanup
    del _WATCHER_REGISTRY["test_tool_xyz"]
