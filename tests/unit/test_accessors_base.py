# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for DataAccessor base classes and LocalResource."""

from pathlib import Path

from openviking.parse.accessors.base import LocalResource, SourceType


class TestLocalResource:
    """Tests for LocalResource dataclass."""

    def test_local_resource_cleanup_temporary(self, tmp_path: Path) -> None:
        """cleanup() removes temporary resources."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("content")

        resource = LocalResource(
            path=test_dir,
            source_type=SourceType.GIT,
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
            source_type=SourceType.LOCAL,
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
            source_type=SourceType.HTTP,
            original_source="test",
            is_temporary=True,
        )

        # Should not raise
        resource.cleanup()
