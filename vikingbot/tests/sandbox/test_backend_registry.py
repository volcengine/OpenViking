"""Tests for sandbox backend registry."""

import pytest
from vikingbot.sandbox.backends import register_backend, get_backend, list_backends
from vikingbot.sandbox.base import SandboxBackend


class MockBackend(SandboxBackend):
    """Mock backend for testing."""

    async def start(self) -> None:
        pass

    async def execute(self, command, timeout=60, **kwargs) -> str:
        return f"Mock: {command}"

    async def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        return False

    @property
    def workspace(self):
        from pathlib import Path
        return Path("/tmp/mock")


def test_register_backend():
    """Test backend registration decorator."""

    @register_backend("mock")
    class TestBackend(SandboxBackend):
        async def start(self) -> None:
            pass

        async def execute(self, command, timeout=60, **kwargs) -> str:
            return ""

        async def stop(self) -> None:
            pass

        def is_running(self) -> bool:
            return False

        @property
        def workspace(self):
            from pathlib import Path
            return Path("/tmp/test")

    backend_cls = get_backend("mock")
    assert backend_cls is TestBackend


def test_get_backend():
    """Test getting registered backend."""
    backend_cls = get_backend("srt")
    assert backend_cls is not None


def test_get_backend_nonexistent():
    """Test getting non-existent backend."""
    backend_cls = get_backend("nonexistent")
    assert backend_cls is None


def test_list_backends():
    """Test listing all registered backends."""
    backends = list_backends()
    assert "srt" in backends
    assert len(backends) >= 1


def test_multiple_backends():
    """Test registering multiple backends."""

    @register_backend("mock1")
    class MockBackend1(SandboxBackend):
        async def start(self) -> None:
            pass

        async def execute(self, command, timeout=60, **kwargs) -> str:
            return ""

        async def stop(self) -> None:
            pass

        def is_running(self) -> bool:
            return False

        @property
        def workspace(self):
            from pathlib import Path
            return Path("/tmp/mock1")

    @register_backend("mock2")
    class MockBackend2(SandboxBackend):
        async def start(self) -> None:
            pass

        async def execute(self, command, timeout=60, **kwargs) -> str:
            return ""

        async def stop(self) -> None:
            pass

        def is_running(self) -> bool:
            return False

        @property
        def workspace(self):
            from pathlib import Path
            return Path("/tmp/mock2")

    assert get_backend("mock1") is MockBackend1
    assert get_backend("mock2") is MockBackend2
