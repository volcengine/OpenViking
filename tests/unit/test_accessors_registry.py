# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for AccessorRegistry."""

from pathlib import Path
from typing import Union

import pytest

from openviking.parse.accessors.base import DataAccessor, LocalResource
from openviking.parse.accessors.registry import AccessorRegistry


class _TestAccessor(DataAccessor):
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

    def test_higher_priority_accessor_wins(self, registry: AccessorRegistry) -> None:
        """Choose the highest-priority match regardless of registration order."""
        accessor1 = _TestAccessor("test1", "test:", 10)
        accessor2 = _TestAccessor("test2", "test:", 50)
        registry.register(accessor1)
        registry.register(accessor2)

        result = registry.get_accessor("test:source")
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
        result.cleanup()
        assert test_file.read_text() == "content"
