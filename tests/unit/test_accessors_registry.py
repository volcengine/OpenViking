# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for AccessorRegistry."""

from pathlib import Path
from typing import Union

import pytest

from openviking.parse.accessors.base import DataAccessor, LocalResource
from openviking.parse.accessors.registry import AccessorRegistry, get_accessor_registry


class TestAccessor(DataAccessor):
    """Test accessor implementation."""

    def __init__(self, name: str, prefix: str, priority: int = 50):
        self.name = name
        self.prefix = prefix
        self._priority = priority

    def can_handle(self, source: Union[str, Path]) -> bool:
        return str(source).startswith(self.prefix)

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        return LocalResource(
            path=Path(f"/tmp/{self.name}"),
            source_type=self.name,
            original_source=str(source),
            meta={"accessor": self.name, **kwargs},
        )

    @property
    def priority(self) -> int:
        return self._priority


class TestAccessorRegistry:
    """Tests for AccessorRegistry."""

    @pytest.fixture
    def registry(self) -> AccessorRegistry:
        """Create a fresh registry (without default accessors)."""
        return AccessorRegistry(register_default=False)

    def test_get_accessor(self, registry: AccessorRegistry) -> None:
        """Get an accessor that can handle a source."""
        accessor1 = TestAccessor("test1", "test1:", 50)
        accessor2 = TestAccessor("test2", "test2:", 50)
        registry.register(accessor1)
        registry.register(accessor2)

        result = registry.get_accessor("test1:source")
        assert result is accessor1

        result = registry.get_accessor("test2:source")
        assert result is accessor2

        result = registry.get_accessor("unknown:source")
        assert result is None

    @pytest.mark.asyncio
    async def test_access_fallback_to_local(
        self, registry: AccessorRegistry, tmp_path: Path
    ) -> None:
        """access() falls back to local file when no accessor matches."""
        from openviking.parse.accessors.local_accessor import LocalAccessor

        # Register LocalAccessor as fallback
        registry.register(LocalAccessor())

        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        result = await registry.access(str(test_file))

        assert result.source_type == "local"
        assert result.path == test_file
        assert result.is_temporary is False

    @pytest.mark.asyncio
    async def test_access_with_accessor(self, registry: AccessorRegistry) -> None:
        """access() uses the matching accessor."""
        accessor = TestAccessor("test", "test:", 50)
        registry.register(accessor)

        result = await registry.access("test:source", extra="value")

        assert result.source_type == "test"
        assert result.meta == {"accessor": "test", "extra": "value"}


class TestGlobalRegistry:
    """Tests for the global registry."""

    def test_get_accessor_registry(self) -> None:
        """get_accessor_registry returns a singleton."""
        r1 = get_accessor_registry()
        r2 = get_accessor_registry()
        assert r1 is r2
