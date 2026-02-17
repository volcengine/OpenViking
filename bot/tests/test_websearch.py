"""Tests for websearch tool with multiple backends."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vikingbot.agent.tools.websearch import WebSearchTool
from vikingbot.agent.tools.websearch.base import WebSearchBackend
from vikingbot.agent.tools.websearch.registry import registry, register_backend


# Test backend for testing
class TestBackend(WebSearchBackend):
    """Test backend that always returns fixed results."""
    
    name = "test"
    
    def __init__(self, available: bool = True):
        self._available = available
    
    @property
    def is_available(self) -> bool:
        return self._available
    
    async def search(self, query: str, count: int, **kwargs) -> str:
        lines = [f"Results for: {query}\n"]
        for i in range(1, count + 1):
            lines.append(f"{i}. Result {i}\n   https://example.com/{i}\n   Snippet {i}")
        return "\n".join(lines)


# Another test backend for priority testing
class TestBackend2(WebSearchBackend):
    """Another test backend."""
    
    name = "test2"
    
    @property
    def is_available(self) -> bool:
        return True
    
    async def search(self, query: str, count: int, **kwargs) -> str:
        return f"TestBackend2: {query}"


def test_registry_register_and_get() -> None:
    """Test registry can register and retrieve backends."""
    # Clear registry for test
    original_backends = registry._backends.copy()
    registry._backends.clear()
    
    try:
        @register_backend
        class TempBackend(WebSearchBackend):
            name = "temp"
            
            @property
            def is_available(self) -> bool:
                return True
            
            async def search(self, query: str, count: int, **kwargs) -> str:
                return ""
        
        assert registry.get("temp") is not None
        assert "temp" in registry.list_names()
    finally:
        registry._backends = original_backends


def test_registry_create() -> None:
    """Test registry can create backend instances."""
    # Clear registry for test
    original_backends = registry._backends.copy()
    registry._backends.clear()
    
    try:
        @register_backend
        class TempBackend(WebSearchBackend):
            name = "temp"
            
            def __init__(self, api_key: str | None = None):
                self.api_key = api_key
            
            @property
            def is_available(self) -> bool:
                return bool(self.api_key)
            
            async def search(self, query: str, count: int, **kwargs) -> str:
                return ""
        
        # Test create with brave backend pattern
        backend = registry.create("temp", brave_api_key="test-key")
        assert backend is not None
        assert backend.api_key == "test-key"  # type: ignore
        
        # Test unknown backend
        assert registry.create("nonexistent") is None
    finally:
        registry._backends = original_backends


def test_websearchtool_init_with_backend_name() -> None:
    """Test WebSearchTool can be initialized with backend name."""
    # Clear registry for test
    original_backends = registry._backends.copy()
    registry._backends.clear()
    
    try:
        @register_backend
        class TempBackend(WebSearchBackend):
            name = "temp"
            
            @property
            def is_available(self) -> bool:
                return True
            
            async def search(self, query: str, count: int, **kwargs) -> str:
                return ""
        
        tool = WebSearchTool(backend="temp")
        assert tool.backend.name == "temp"
    finally:
        registry._backends = original_backends


def test_websearchtool_init_with_backend_instance() -> None:
    """Test WebSearchTool can be initialized with backend instance."""
    backend = TestBackend()
    tool = WebSearchTool(backend=backend)
    assert tool.backend is backend


def test_websearchtool_init_unknown_backend() -> None:
    """Test WebSearchTool raises error for unknown backend."""
    with pytest.raises(ValueError, match="Unknown backend"):
        WebSearchTool(backend="nonexistent")


@pytest.mark.asyncio
async def test_websearchtool_execute() -> None:
    """Test WebSearchTool can execute search."""
    backend = TestBackend()
    tool = WebSearchTool(backend=backend)
    
    result = await tool.execute("test query", count=3)
    assert "Results for: test query" in result
    assert "Result 1" in result
    assert "Result 2" in result
    assert "Result 3" in result


def test_websearchtool_backend_property() -> None:
    """Test backend property returns the active backend."""
    backend = TestBackend()
    tool = WebSearchTool(backend=backend)
    assert tool.backend is backend


def test_brave_backend_is_available() -> None:
    """Test BraveBackend availability check."""
    from vikingbot.agent.tools.websearch.brave import BraveBackend
    
    # Test with no API key
    backend = BraveBackend(api_key=None)
    assert backend.is_available is False
    
    # Test with API key
    backend = BraveBackend(api_key="test-key")
    assert backend.is_available is True
    
    # Test with environment variable
    original_env = os.environ.get("BRAVE_API_KEY")
    os.environ["BRAVE_API_KEY"] = "env-key"
    try:
        backend = BraveBackend(api_key=None)
        assert backend.is_available is True
        assert backend.api_key == "env-key"
    finally:
        if original_env:
            os.environ["BRAVE_API_KEY"] = original_env
        else:
            os.environ.pop("BRAVE_API_KEY", None)


def test_exa_backend_is_available() -> None:
    """Test ExaBackend availability check."""
    from vikingbot.agent.tools.websearch.exa import ExaBackend
    
    # Test with no API key
    backend = ExaBackend(api_key=None)
    assert backend.is_available is False
    
    # Test with API key
    backend = ExaBackend(api_key="test-key")
    assert backend.is_available is True
    
    # Test with environment variable
    original_env = os.environ.get("EXA_API_KEY")
    os.environ["EXA_API_KEY"] = "env-key"
    try:
        backend = ExaBackend(api_key=None)
        assert backend.is_available is True
        assert backend.api_key == "env-key"
    finally:
        if original_env:
            os.environ["EXA_API_KEY"] = original_env
        else:
            os.environ.pop("EXA_API_KEY", None)


def test_ddgs_backend_is_available() -> None:
    """Test DDGSBackend availability check."""
    from vikingbot.agent.tools.websearch.ddgs import DDGSBackend
    
    # This depends on whether ddgs is installed
    backend = DDGSBackend()
    # Just test that the method doesn't crash
    assert isinstance(backend.is_available, bool)


def test_registry_select_auto_priority() -> None:
    """Test auto-select picks backends in priority order."""
    # Clear registry for test
    original_backends = registry._backends.copy()
    registry._backends.clear()
    
    try:
        # Register test backends in non-priority order
        @register_backend
        class LowPriorityBackend(WebSearchBackend):
            name = "ddgs"
            
            @property
            def is_available(self) -> bool:
                return True
            
            async def search(self, query: str, count: int, **kwargs) -> str:
                return "ddgs"
        
        @register_backend
        class MediumPriorityBackend(WebSearchBackend):
            name = "brave"
            
            def __init__(self, api_key: str | None = None):
                self.api_key = api_key
            
            @property
            def is_available(self) -> bool:
                return bool(self.api_key)
            
            async def search(self, query: str, count: int, **kwargs) -> str:
                return "brave"
        
        @register_backend
        class HighPriorityBackend(WebSearchBackend):
            name = "exa"
            
            def __init__(self, api_key: str | None = None):
                self.api_key = api_key
            
            @property
            def is_available(self) -> bool:
                return bool(self.api_key)
            
            async def search(self, query: str, count: int, **kwargs) -> str:
                return "exa"
        
        # Test: only ddgs available
        backend = registry.select_auto(brave_api_key=None, exa_api_key=None)
        assert backend.name == "ddgs"
        
        # Test: brave available
        backend = registry.select_auto(brave_api_key="brave-key", exa_api_key=None)
        assert backend.name == "brave"
        
        # Test: exa available (highest priority)
        backend = registry.select_auto(brave_api_key="brave-key", exa_api_key="exa-key")
        assert backend.name == "exa"
    finally:
        registry._backends = original_backends


def test_websearchtool_parameters_schema() -> None:
    """Test WebSearchTool has correct parameters schema."""
    tool = WebSearchTool(backend=TestBackend())
    
    # Check required fields
    assert "query" in tool.parameters["required"]
    
    # Check properties
    props = tool.parameters["properties"]
    assert "query" in props
    assert "count" in props
    assert "type" in props
    assert "livecrawl" in props
    
    # Check count range
    assert props["count"]["minimum"] == 1
    assert props["count"]["maximum"] == 20
