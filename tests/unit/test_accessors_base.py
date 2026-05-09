# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for DataAccessor base classes and LocalResource."""

from pathlib import Path
from typing import Union

import pytest

from openviking.parse.accessors.base import DataAccessor, LocalResource


class TestLocalResource:
    """Tests for LocalResource dataclass."""

    def test_create_local_resource(self, tmp_path: Path) -> None:
        """Basic LocalResource creation."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        resource = LocalResource(
            path=test_file,
            source_type="local",
            original_source="/original/path",
            meta={"key": "value"},
            is_temporary=False,
        )

        assert resource.path == test_file
        assert resource.source_type == "local"
        assert resource.original_source == "/original/path"
        assert resource.meta == {"key": "value"}
        assert resource.is_temporary is False

    def test_local_resource_defaults(self, tmp_path: Path) -> None:
        """LocalResource with default values."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        resource = LocalResource(
            path=test_file,
            source_type="local",
            original_source="/original/path",
        )

        assert resource.meta == {}
        assert resource.is_temporary is True

    def test_local_resource_cleanup_temporary(self, tmp_path: Path) -> None:
        """cleanup() removes temporary resources."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("content")

        resource = LocalResource(
            path=test_dir,
            source_type="test",
            original_source="test",
            is_temporary=True,
        )

        assert test_dir.exists()
        resource.cleanup()
        assert not test_dir.exists()

    def test_local_resource_cleanup_non_temporary(self, tmp_path: Path) -> None:
        """cleanup() does not remove non-temporary resources."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        resource = LocalResource(
            path=test_file,
            source_type="local",
            original_source="test",
            is_temporary=False,
        )

        assert test_file.exists()
        resource.cleanup()
        assert test_file.exists()

    def test_local_resource_cleanup_missing(self, tmp_path: Path) -> None:
        """cleanup() handles missing paths gracefully."""
        missing_path = tmp_path / "missing"

        resource = LocalResource(
            path=missing_path,
            source_type="test",
            original_source="test",
            is_temporary=True,
        )

        # Should not raise
        resource.cleanup()


class TestDataAccessor:
    """Tests for DataAccessor abstract base class."""

    def test_cannot_instantiate_abstract(self) -> None:
        """Cannot directly instantiate DataAccessor."""
        with pytest.raises(TypeError):
            DataAccessor()  # type: ignore

    def test_subclass_must_implement_methods(self) -> None:
        """Subclasses must implement all abstract methods."""

        class IncompleteAccessor(DataAccessor):
            pass

        with pytest.raises(TypeError):
            IncompleteAccessor()

    def test_complete_subclass(self) -> None:
        """A complete subclass can be instantiated."""

        class TestAccessor(DataAccessor):
            def can_handle(self, source: Union[str, Path]) -> bool:
                return str(source).startswith("test:")

            async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
                return LocalResource(
                    path=Path("/tmp/test"),
                    source_type="test",
                    original_source=str(source),
                )

            @property
            def priority(self) -> int:
                return 50

        accessor = TestAccessor()
        assert accessor.can_handle("test:something")
        assert not accessor.can_handle("other:something")
        assert accessor.priority == 50
