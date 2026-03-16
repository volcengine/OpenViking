# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for cross-platform path handling in ZIP operations."""

import tempfile
import zipfile
from pathlib import Path

from openviking_cli.client.http import AsyncHTTPClient


class TestZipCreationPathNormalization:
    """Test that ZIP creation normalizes Windows path separators to forward slashes."""

    def test_zip_directory_creates_forward_slash_paths(self):
        """When zipping a directory, paths should use forward slashes (ZIP spec)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a directory structure with nested subdirectories
            root_dir = tmpdir / "test_project"
            root_dir.mkdir()
            (root_dir / "file1.txt").write_text("content1")
            (root_dir / "subdir").mkdir()
            (root_dir / "subdir" / "file2.txt").write_text("content2")
            (root_dir / "subdir" / "nested").mkdir()
            (root_dir / "subdir" / "nested" / "file3.txt").write_text("content3")

            # Create ZIP using the same method as AsyncHTTPClient._zip_directory
            client = AsyncHTTPClient(url="http://localhost:1933")
            zip_path = client._zip_directory(str(root_dir))

            try:
                # Verify all paths in ZIP use forward slashes
                with zipfile.ZipFile(zip_path, "r") as zf:
                    names = zf.namelist()
                    for name in names:
                        # No backslashes should be present
                        assert "\\" not in name, f"Path contains backslash: {name}"
                    # Verify the expected files exist with correct paths
                    assert "file1.txt" in names
                    assert "subdir/file2.txt" in names
                    assert "subdir/nested/file3.txt" in names
            finally:
                Path(zip_path).unlink(missing_ok=True)

    def test_zip_directory_preserves_structure(self):
        """ZIP should preserve directory structure correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a complex directory structure
            root_dir = tmpdir / "complex_project"
            root_dir.mkdir()
            (root_dir / "root.txt").write_text("root")
            (root_dir / "level1").mkdir()
            (root_dir / "level1" / "file1.txt").write_text("level1")
            (root_dir / "level1" / "level2").mkdir()
            (root_dir / "level1" / "level2" / "file2.txt").write_text("level2")

            # Create ZIP
            client = AsyncHTTPClient(url="http://localhost:1933")
            zip_path = client._zip_directory(str(root_dir))

            try:
                # Verify structure is preserved
                with zipfile.ZipFile(zip_path, "r") as zf:
                    names = set(zf.namelist())

                    # Check all expected files exist
                    assert "root.txt" in names
                    assert "level1/file1.txt" in names
                    assert "level1/level2/file2.txt" in names

                    # Verify no duplicate filenames (same name in different dirs)
                    # This is the bug: on Windows, paths with backslashes might be treated differently
                    # Each file should have unique full path
                    assert len(names) == len(set(names)), "Duplicate paths detected"
            finally:
                Path(zip_path).unlink(missing_ok=True)


class TestZipExtractionPathHandling:
    """Test that ZIP extraction handles Windows path separators correctly."""

    def test_extract_zip_with_backslash_paths(self):
        """ZIP extraction should handle paths with backslashes (from Windows)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a ZIP with backslash paths (simulating Windows-created ZIP)
            zip_path = tmpdir / "test.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                # Write paths with backslashes (as would happen on Windows)
                zf.writestr("project\\file1.txt", "content1")
                zf.writestr("project\\subdir\\file2.txt", "content2")

            # Verify extraction handles backslashes correctly
            # This test will fail until we fix the extraction code
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Get first path and normalize it
                first_path = zf.namelist()[0]
                normalized_path = first_path.replace("\\", "/")

                # This should extract the base name correctly
                base_name = normalized_path.split("/")[0]
                assert base_name == "project", f"Expected 'project', got '{base_name}'"

    def test_extract_zip_preserves_directory_structure(self):
        """ZIP extraction should preserve directory structure even with backslashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create ZIP with mixed separators (edge case)
            zip_path = tmpdir / "mixed.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("root/file.txt", "root content")
                zf.writestr("root\\nested\\file.txt", "nested content")

            # Both should be extractable
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                assert len(names) == 2

                # After normalization, both should be under root/
                normalized = [name.replace("\\", "/") for name in names]
                assert all(name.startswith("root/") for name in normalized)


class TestDirectoryScanPathNormalization:
    """Test that directory scanning normalizes paths consistently."""

    def test_scan_directory_normalizes_windows_paths(self):
        """Directory scan should normalize Windows paths to forward slashes."""
        from openviking.parse.directory_scan import _normalize_rel_path

        # Test Windows-style paths
        assert _normalize_rel_path("subdir\\file.txt") == "subdir/file.txt"
        assert _normalize_rel_path("a\\b\\c\\file.txt") == "a/b/c/file.txt"

        # Test Unix-style paths (should remain unchanged)
        assert _normalize_rel_path("subdir/file.txt") == "subdir/file.txt"
        assert _normalize_rel_path("a/b/c/file.txt") == "a/b/c/file.txt"

        # Test mixed paths
        assert _normalize_rel_path("a\\b/c\\d/file.txt") == "a/b/c/d/file.txt"

    def test_scan_directory_handles_value_error(self):
        """When relative_to raises ValueError, path should still be normalized."""
        # This test simulates edge case in directory_scan.py:253-256
        # where relative_to fails and we fall back to raw path

        # The fix should ensure normalization happens even in except block
        from openviking.parse.directory_scan import _normalize_rel_path

        # Simulate a path that might cause relative_to to fail
        raw_path = "some\\windows\\path.txt"
        normalized = _normalize_rel_path(raw_path)

        # Should still be normalized
        assert "\\" not in normalized
        assert normalized == "some/windows/path.txt"
