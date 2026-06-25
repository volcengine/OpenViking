"""Tests for multi-watcher DaemonService."""
import pytest
from unittest.mock import MagicMock
from openviking.server.config import WatcherConfig


def test_service_stores_watcher_configs():
    """DaemonService should accept and store watcher_configs."""
    from openviking.daemon.service import DaemonService
    configs = [
        WatcherConfig(tool_name="claude_code", watch_dir="/tmp/cc"),
        WatcherConfig(tool_name="cursor_db", watch_dir="/tmp/cursor"),
    ]
    svc = DaemonService(
        resource_service=MagicMock(),
        watcher_configs=configs,
        db_path="/tmp/test.db",
    )
    assert len(svc._watcher_configs) == 2


def test_service_backward_compat_single_dir():
    """DaemonService should create single claude_code config from watch_dir."""
    from openviking.daemon.service import DaemonService
    svc = DaemonService(
        resource_service=MagicMock(),
        watch_dir="/tmp/cc",
        db_path="/tmp/test.db",
    )
    assert len(svc._watcher_configs) == 1
    assert svc._watcher_configs[0].tool_name == "claude_code"
