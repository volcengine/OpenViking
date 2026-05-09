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

    def test_register_and_list(self, registry: AccessorRegistry) -> None:
        """Register an accessor and list it."""
        accessor = TestAccessor("test", "test:", 50)
        registry.register(accessor)

        accessors = registry.list_accessors()
        assert len(accessors) == 1
        assert accessors[0] == accessor

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

    def test_priority_ordering(self, registry: AccessorRegistry) -> None:
        """Accessors are ordered by priority (descending)."""
        low_prio = TestAccessor("low", "common:", 10)
        mid_prio = TestAccessor("mid", "common:", 50)
        high_prio = TestAccessor("high", "common:", 100)

        # Register in random order
        registry.register(low_prio)
        registry.register(high_prio)
        registry.register(mid_prio)

        accessors = registry.list_accessors()
        assert [a.name for a in accessors] == ["high", "mid", "low"]

        # Highest priority should be selected
        result = registry.get_accessor("common:source")
        assert result is high_prio

    def test_list_accessors_with_filter(self, registry: AccessorRegistry) -> None:
        """list_accessors filters by source capability."""
        accessor1 = TestAccessor("a", "a:", 50)
        accessor2 = TestAccessor("b", "b:", 50)
        accessor3 = TestAccessor("both", "a:", 50)  # also handles "a:"
        registry.register(accessor1)
        registry.register(accessor2)
        registry.register(accessor3)

        filtered = registry.list_accessors("a:source")
        assert len(filtered) == 2
        names = {a.name for a in filtered}
        assert names == {"a", "both"}

    def test_unregister(self, registry: AccessorRegistry) -> None:
        """Unregister removes accessor by name."""
        accessor = TestAccessor("test", "test:", 50)
        registry.register(accessor)

        assert registry.unregister("TestAccessor") is True
        assert len(registry.list_accessors()) == 0

        assert registry.unregister("NotFound") is False

    def test_clear(self, registry: AccessorRegistry) -> None:
        """Clear removes all accessors."""
        registry.register(TestAccessor("a", "a:", 50))
        registry.register(TestAccessor("b", "b:", 50))

        assert len(registry.list_accessors()) == 2
        registry.clear()
        assert len(registry.list_accessors()) == 0

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
