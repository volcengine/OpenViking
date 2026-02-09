# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for CodeRepositoryParser._extract_zip Zip Slip protection."""

import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

from openviking.parse.parsers.code.code import CodeRepositoryParser


def _make_zip(entries: dict[str, str], target_path: str) -> None:
    """Create a zip file with the given filename->content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    Path(target_path).write_bytes(buf.getvalue())


def _make_zip_with_symlink(target_path: str) -> None:
    """Create a zip containing a symlink entry via raw external_attr."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("evil_link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    Path(target_path).write_bytes(buf.getvalue())


def _assert_no_escape(tmp_path: Path, target_dir: str) -> None:
    """Assert no files were written outside target_dir within tmp_path."""
    target = Path(target_dir).resolve()
    for f in tmp_path.rglob("*"):
        resolved = f.resolve()
        if resolved == target or resolved.is_relative_to(target):
            continue
        if f.suffix == ".zip":
            continue
        raise AssertionError(f"File escaped target_dir: {resolved}")


@pytest.fixture
def parser():
    return CodeRepositoryParser()


@pytest.fixture
def workspace(tmp_path):
    """Provide a temp workspace with zip_path, target_dir, and tmp_path."""
    zip_path = str(tmp_path / "test.zip")
    target_dir = str(tmp_path / "extracted")
    os.makedirs(target_dir)
    return tmp_path, zip_path, target_dir


class TestExtractZipNormal:
    """Verify normal zip extraction still works."""

    async def test_extracts_files_correctly(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip(
            {"src/main.py": "print('hello')", "README.md": "# Test"},
            zip_path,
        )
        name = await parser._extract_zip(zip_path, target_dir)
        assert name == "test"
        assert (Path(target_dir) / "src" / "main.py").read_text() == "print('hello')"
        assert (Path(target_dir) / "README.md").read_text() == "# Test"

    async def test_returns_stem_as_name(self, parser, tmp_path):
        zip_path = str(tmp_path / "my-repo.zip")
        target_dir = str(tmp_path / "out")
        os.makedirs(target_dir)
        _make_zip({"a.txt": "content"}, zip_path)
        name = await parser._extract_zip(zip_path, target_dir)
        assert name == "my-repo"


class TestExtractZipPathTraversal:
    """Verify Zip Slip path traversal raises ValueError."""

    async def test_rejects_dot_dot_traversal(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip({"../../evil.txt": "pwned"}, zip_path)
        with pytest.raises(ValueError, match="Zip Slip detected"):
            await parser._extract_zip(zip_path, target_dir)
        _assert_no_escape(tmp_path, target_dir)

    async def test_rejects_absolute_path(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip({"/etc/passwd": "root:x:0:0"}, zip_path)
        with pytest.raises(ValueError, match="Zip Slip detected"):
            await parser._extract_zip(zip_path, target_dir)
        _assert_no_escape(tmp_path, target_dir)

    async def test_rejects_nested_traversal(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip({"foo/../../evil.txt": "pwned"}, zip_path)
        with pytest.raises(ValueError, match="Zip Slip detected"):
            await parser._extract_zip(zip_path, target_dir)
        _assert_no_escape(tmp_path, target_dir)

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")
    async def test_rejects_windows_drive_path(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip({"C:\\evil.txt": "pwned"}, zip_path)
        with pytest.raises(ValueError, match="Zip Slip detected"):
            await parser._extract_zip(zip_path, target_dir)
        _assert_no_escape(tmp_path, target_dir)

    async def test_rejects_backslash_traversal(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip({"..\\..\\evil.txt": "pwned"}, zip_path)
        with pytest.raises(ValueError, match="Zip Slip detected"):
            await parser._extract_zip(zip_path, target_dir)
        _assert_no_escape(tmp_path, target_dir)

    async def test_rejects_unc_path(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip({"\\\\server\\share\\evil.txt": "pwned"}, zip_path)
        with pytest.raises(ValueError, match="Zip Slip detected"):
            await parser._extract_zip(zip_path, target_dir)
        _assert_no_escape(tmp_path, target_dir)


class TestExtractZipSymlink:
    """Verify symlink entries are skipped."""

    async def test_skips_symlink_entry(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        _make_zip_with_symlink(zip_path)
        await parser._extract_zip(zip_path, target_dir)
        extracted_files = list(Path(target_dir).rglob("*"))
        assert len(extracted_files) == 0


class TestExtractZipEmptyNormalization:
    """Verify entries containing '..' are rejected even if they normalize safely."""

    async def test_rejects_dot_dot_entry(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            # "./.." contains ".." and must be rejected
            info = zipfile.ZipInfo("./..")
            info.external_attr = 0
            zf.writestr(info, "should be rejected")
            zf.writestr("src/main.py", "print('ok')")
        Path(zip_path).write_bytes(buf.getvalue())
        with pytest.raises(ValueError, match="Zip Slip detected"):
            await parser._extract_zip(zip_path, target_dir)


class TestExtractZipDirectoryEntry:
    """Verify explicit directory entries are skipped without error."""

    async def test_skips_directory_entries(self, parser, workspace):
        tmp_path, zip_path, target_dir = workspace
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mydir/", "")
            zf.writestr("mydir/file.txt", "content")
        Path(zip_path).write_bytes(buf.getvalue())
        await parser._extract_zip(zip_path, target_dir)
        assert (Path(target_dir) / "mydir" / "file.txt").read_text() == "content"
