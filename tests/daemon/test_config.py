"""Tests for WatcherConfig and DaemonConfig multi-watcher support."""
import pytest
from openviking.server.config import WatcherConfig, DaemonConfig


def test_watcher_config_defaults():
    wc = WatcherConfig(tool_name="test", watch_dir="/tmp/test")
    assert wc.file_pattern == "*.jsonl"
    assert wc.enabled is True
    assert wc.batch_trigger_lines == 50
    assert wc.extra == {}


def test_watcher_config_custom():
    wc = WatcherConfig(
        tool_name="aider",
        watch_dir="~/Projects",
        file_pattern=".aider.chat.history.md",
        batch_trigger_lines=100,
        extra={"key": "value"},
    )
    assert wc.tool_name == "aider"
    assert wc.file_pattern == ".aider.chat.history.md"
    assert wc.extra == {"key": "value"}


def test_watcher_config_forbid_extra():
    with pytest.raises(ValueError):
        WatcherConfig(tool_name="test", watch_dir="/tmp", unknown_field="x")


def test_daemon_config_get_effective_watchers_explicit():
    cfg = DaemonConfig(
        enabled=True,
        watchers=[
            WatcherConfig(tool_name="claude_code", watch_dir="/a"),
            WatcherConfig(tool_name="aider", watch_dir="/b"),
        ],
    )
    effective = cfg.get_effective_watchers()
    assert len(effective) == 2
    assert effective[0].tool_name == "claude_code"
    assert effective[1].tool_name == "aider"


def test_daemon_config_get_effective_watchers_disabled_filtered():
    cfg = DaemonConfig(
        enabled=True,
        watchers=[
            WatcherConfig(tool_name="claude_code", watch_dir="/a"),
            WatcherConfig(tool_name="aider", watch_dir="/b", enabled=False),
        ],
    )
    effective = cfg.get_effective_watchers()
    assert len(effective) == 1
    assert effective[0].tool_name == "claude_code"


def test_daemon_config_backward_compat_watch_dir():
    cfg = DaemonConfig(enabled=True, watch_dir="~/.claude/projects")
    effective = cfg.get_effective_watchers()
    assert len(effective) == 1
    assert effective[0].tool_name == "claude_code"
    assert effective[0].watch_dir == "~/.claude/projects"


def test_daemon_config_backward_compat_default():
    cfg = DaemonConfig(enabled=True)
    effective = cfg.get_effective_watchers()
    assert len(effective) == 1
    assert effective[0].tool_name == "claude_code"


def test_daemon_config_from_env_watchers():
    import os
    os.environ["OV_DAEMON_ENABLED"] = "true"
    os.environ["OV_DAEMON_WATCHERS"] = '[{"tool_name": "aider", "watch_dir": "/tmp"}]'
    try:
        cfg = DaemonConfig.from_env()
        assert cfg.enabled is True
        assert len(cfg.watchers) == 1
        assert cfg.watchers[0].tool_name == "aider"
    finally:
        os.environ.pop("OV_DAEMON_ENABLED", None)
        os.environ.pop("OV_DAEMON_WATCHERS", None)
