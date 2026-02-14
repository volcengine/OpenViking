"""Tests for sandbox manager."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from vikingbot.sandbox.manager import SandboxManager
from vikingbot.sandbox.base import SandboxDisabledError, UnsupportedBackendError


class MockBackend:
    """Mock sandbox backend for testing."""

    def __init__(self, config, session_key, workspace):
        self.config = config
        self.session_key = session_key
        self._workspace = workspace
        self._running = False

    async def start(self):
        self._running = True

    async def execute(self, command, timeout=60, **kwargs):
        return f"Mock: {command}"

    async def stop(self):
        self._running = False

    def is_running(self):
        return self._running

    @property
    def workspace(self):
        return self._workspace


def test_sandbox_manager_init():
    """Test SandboxManager initialization."""
    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.mode = "per-session"
    mock_config.backend = "mock"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, Path("/tmp/workspace"))
        assert manager.config is mock_config
        assert manager.workspace == Path("/tmp/workspace")


def test_sandbox_manager_unsupported_backend():
    """Test SandboxManager with unsupported backend."""
    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.mode = "per-session"
    mock_config.backend = "unsupported"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=None):
        with pytest.raises(UnsupportedBackendError):
            SandboxManager(mock_config, Path("/tmp/workspace"))


async def test_get_sandbox_disabled():
    """Test getting sandbox when disabled."""
    mock_config = MagicMock()
    mock_config.enabled = False
    mock_config.mode = "per-session"
    mock_config.backend = "mock"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, Path("/tmp/workspace"))

        with pytest.raises(SandboxDisabledError):
            await manager.get_sandbox("test_session")


async def test_get_sandbox_per_session():
    """Test getting per-session sandbox."""
    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.mode = "per-session"
    mock_config.backend = "mock"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, Path("/tmp/workspace"))

        sandbox1 = await manager.get_sandbox("session1")
        sandbox2 = await manager.get_sandbox("session2")

        assert sandbox1.session_key == "session1"
        assert sandbox2.session_key == "session2"
        assert sandbox1 is not sandbox2


async def test_get_sandbox_shared():
    """Test getting shared sandbox."""
    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.mode = "shared"
    mock_config.backend = "mock"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, Path("/tmp/workspace"))

        sandbox1 = await manager.get_sandbox("session1")
        sandbox2 = await manager.get_sandbox("session2")

        assert sandbox1 is sandbox2
        assert sandbox1.session_key == "shared"


async def test_cleanup_session():
    """Test cleaning up a session sandbox."""
    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.mode = "per-session"
    mock_config.backend = "mock"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, Path("/tmp/workspace"))

        sandbox = await manager.get_sandbox("test_session")
        assert sandbox.is_running()

        await manager.cleanup_session("test_session")
        assert not sandbox.is_running()


async def test_cleanup_all():
    """Test cleaning up all sandboxes."""
    mock_config = MagicMock()
    mock_config.enabled = True
    mock_config.mode = "per-session"
    mock_config.backend = "mock"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, Path("/tmp/workspace"))

        sandbox1 = await manager.get_sandbox("session1")
        sandbox2 = await manager.get_sandbox("session2")

        assert sandbox1.is_running()
        assert sandbox2.is_running()

        await manager.cleanup_all()

        assert not sandbox1.is_running()
        assert not sandbox2.is_running()
