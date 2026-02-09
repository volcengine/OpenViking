# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for shared upload utilities."""

from pathlib import Path
from typing import Dict, List

import pytest

from openviking.parse.parsers.upload_utils import (
    _sanitize_rel_path,
    detect_and_convert_encoding,
    is_text_file,
    should_skip_directory,
    should_skip_file,
    upload_directory,
    upload_text_files,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeVikingFS:
    """Minimal VikingFS mock for testing upload functions."""

    def __init__(self) -> None:
        self.files: Dict[str, bytes] = {}
        self.dirs: List[str] = []

    async def write_file_bytes(self, uri: str, content: bytes) -> None:
        self.files[uri] = content

    async def mkdir(self, uri: str, exist_ok: bool = False) -> None:
        self.dirs.append(uri)


@pytest.fixture
def viking_fs() -> FakeVikingFS:
    return FakeVikingFS()


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with sample files for testing."""
    # Text files
    (tmp_path / "hello.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# README", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("key: value", encoding="utf-8")

    # Hidden file
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")

    # Binary-extension file
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")

    # Empty file
    (tmp_path / "empty.txt").write_bytes(b"")

    # Subdirectory
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "main.go").write_text("package main", encoding="utf-8")

    # Ignored directory
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.pyc").write_bytes(b"\x00\x00")

    return tmp_path


# ---------------------------------------------------------------------------
# is_text_file
# ---------------------------------------------------------------------------


class TestIsTextFile:
    def test_code_extensions(self) -> None:
        assert is_text_file("main.py") is True
        assert is_text_file("app.js") is True
        assert is_text_file("lib.go") is True

    def test_documentation_extensions(self) -> None:
        assert is_text_file("README.md") is True
        assert is_text_file("notes.txt") is True
        assert is_text_file("guide.rst") is True

    def test_additional_text_extensions(self) -> None:
        assert is_text_file("settings.ini") is True
        assert is_text_file("data.csv") is True

    def test_non_text_extensions(self) -> None:
        assert is_text_file("photo.png") is False
        assert is_text_file("video.mp4") is False
        assert is_text_file("archive.zip") is False
        assert is_text_file("program.exe") is False

    def test_no_extension_known_names(self) -> None:
        assert is_text_file("Makefile") is True
        assert is_text_file("LICENSE") is True
        assert is_text_file("Dockerfile") is True

    def test_no_extension_unknown_names(self) -> None:
        assert is_text_file("randomfile") is False

    def test_no_extension_case_insensitive(self) -> None:
        assert is_text_file("makefile") is True
        assert is_text_file("license") is True
        assert is_text_file("dockerfile") is True

    def test_case_insensitive(self) -> None:
        assert is_text_file("MAIN.PY") is True
        assert is_text_file("README.MD") is True


# ---------------------------------------------------------------------------
# detect_and_convert_encoding
# ---------------------------------------------------------------------------


class TestDetectAndConvertEncoding:
    def test_utf8_passthrough(self) -> None:
        content = "hello world".encode("utf-8")
        result = detect_and_convert_encoding(content, "test.py")
        assert result == content

    def test_gbk_to_utf8(self) -> None:
        text = "你好世界"
        content = text.encode("gbk")
        result = detect_and_convert_encoding(content, "test.py")
        assert result.decode("utf-8") == text

    def test_non_text_file_passthrough(self) -> None:
        content = b"\x89PNG\r\n\x1a\n"
        result = detect_and_convert_encoding(content, "image.png")
        assert result == content

    def test_empty_file_path(self) -> None:
        content = b"hello"
        result = detect_and_convert_encoding(content, "")
        # Empty path has no extension, so is_text_file returns False
        assert result == content

    def test_latin1_to_utf8(self) -> None:
        text = "café"
        content = text.encode("latin-1")
        result = detect_and_convert_encoding(content, "test.txt")
        assert "caf" in result.decode("utf-8")


# ---------------------------------------------------------------------------
# should_skip_file
# ---------------------------------------------------------------------------


class TestShouldSkipFile:
    def test_hidden_file(self, tmp_path: Path) -> None:
        f = tmp_path / ".gitignore"
        f.write_text("node_modules", encoding="utf-8")
        skip, reason = should_skip_file(f)
        assert skip is True
        assert "hidden" in reason

    def test_ignored_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        skip, reason = should_skip_file(f)
        assert skip is True
        assert ".jpg" in reason

    def test_large_file(self, tmp_path: Path) -> None:
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 100)
        skip, reason = should_skip_file(f, max_file_size=50)
        assert skip is True
        assert "too large" in reason

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_bytes(b"")
        skip, reason = should_skip_file(f)
        assert skip is True
        assert "empty" in reason

    def test_normal_file(self, tmp_path: Path) -> None:
        f = tmp_path / "main.py"
        f.write_text("print(1)", encoding="utf-8")
        skip, reason = should_skip_file(f)
        assert skip is False
        assert reason == ""

    def test_custom_ignore_extensions(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("a,b,c", encoding="utf-8")
        skip, _ = should_skip_file(f, ignore_extensions={".csv"})
        assert skip is True

    def test_symlink(self, tmp_path: Path) -> None:
        target = tmp_path / "real.txt"
        target.write_text("content", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")
        skip, reason = should_skip_file(link)
        assert skip is True
        assert "symbolic" in reason


# ---------------------------------------------------------------------------
# should_skip_directory
# ---------------------------------------------------------------------------


class TestShouldSkipDirectory:
    def test_ignored_dirs(self) -> None:
        assert should_skip_directory(".git") is True
        assert should_skip_directory("__pycache__") is True
        assert should_skip_directory("node_modules") is True

    def test_hidden_dirs(self) -> None:
        assert should_skip_directory(".vscode") is True
        assert should_skip_directory(".idea") is True

    def test_normal_dirs(self) -> None:
        assert should_skip_directory("src") is False
        assert should_skip_directory("tests") is False
        assert should_skip_directory("docs") is False


# ---------------------------------------------------------------------------
# upload_text_files
# ---------------------------------------------------------------------------


class TestUploadTextFiles:
    @pytest.mark.asyncio
    async def test_upload_success(self, tmp_path: Path, viking_fs: FakeVikingFS) -> None:
        f = tmp_path / "hello.py"
        f.write_text("print('hi')", encoding="utf-8")
        file_paths = [(f, "hello.py")]

        count, warnings = await upload_text_files(file_paths, "viking://temp/abc", viking_fs)

        assert count == 1
        assert len(warnings) == 0
        assert "viking://temp/abc/hello.py" in viking_fs.files

    @pytest.mark.asyncio
    async def test_upload_multiple(self, tmp_path: Path, viking_fs: FakeVikingFS) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "b.md"
        f2.write_text("b", encoding="utf-8")
        file_paths = [(f1, "a.py"), (f2, "b.md")]

        count, warnings = await upload_text_files(file_paths, "viking://temp/x", viking_fs)

        assert count == 2
        assert len(warnings) == 0

    @pytest.mark.asyncio
    async def test_upload_with_encoding_conversion(
        self, tmp_path: Path, viking_fs: FakeVikingFS
    ) -> None:
        f = tmp_path / "chinese.py"
        f.write_bytes("你好".encode("gbk"))
        file_paths = [(f, "chinese.py")]

        count, warnings = await upload_text_files(file_paths, "viking://temp/enc", viking_fs)

        assert count == 1
        uploaded = viking_fs.files["viking://temp/enc/chinese.py"]
        assert uploaded.decode("utf-8") == "你好"

    @pytest.mark.asyncio
    async def test_upload_nonexistent_file(self, tmp_path: Path, viking_fs: FakeVikingFS) -> None:
        fake = tmp_path / "nonexistent.py"
        file_paths = [(fake, "nonexistent.py")]

        count, warnings = await upload_text_files(file_paths, "viking://temp/err", viking_fs)

        assert count == 0
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# upload_directory
# ---------------------------------------------------------------------------


class TestUploadDirectory:
    @pytest.mark.asyncio
    async def test_basic_upload(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        count, warnings = await upload_directory(tmp_dir, "viking://temp/test", viking_fs)

        # Should upload: hello.py, readme.md, config.yaml, src/main.go
        # Should skip: .hidden, image.png, empty.txt, __pycache__/mod.pyc
        assert count == 4
        assert "viking://temp/test/hello.py" in viking_fs.files
        assert "viking://temp/test/readme.md" in viking_fs.files
        assert "viking://temp/test/config.yaml" in viking_fs.files
        assert "viking://temp/test/src/main.go" in viking_fs.files

    @pytest.mark.asyncio
    async def test_skips_hidden_files(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        await upload_directory(tmp_dir, "viking://temp/test", viking_fs)
        assert all(".hidden" not in uri for uri in viking_fs.files)

    @pytest.mark.asyncio
    async def test_skips_ignored_dirs(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        await upload_directory(tmp_dir, "viking://temp/test", viking_fs)
        assert all("__pycache__" not in uri for uri in viking_fs.files)

    @pytest.mark.asyncio
    async def test_skips_ignored_extensions(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        await upload_directory(tmp_dir, "viking://temp/test", viking_fs)
        assert all(".png" not in uri for uri in viking_fs.files)

    @pytest.mark.asyncio
    async def test_skips_empty_files(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        await upload_directory(tmp_dir, "viking://temp/test", viking_fs)
        assert all("empty.txt" not in uri for uri in viking_fs.files)

    @pytest.mark.asyncio
    async def test_creates_root_dir(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        await upload_directory(tmp_dir, "viking://temp/root", viking_fs)
        assert "viking://temp/root" in viking_fs.dirs

    @pytest.mark.asyncio
    async def test_custom_ignore_dirs(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        count, _ = await upload_directory(
            tmp_dir, "viking://temp/test", viking_fs, ignore_dirs={"src"}
        )
        assert all("src/" not in uri for uri in viking_fs.files)
        # Positive assertion: non-ignored files should still be uploaded
        assert count > 0
        assert "viking://temp/test/hello.py" in viking_fs.files

    @pytest.mark.asyncio
    async def test_custom_max_file_size(self, tmp_dir: Path, viking_fs: FakeVikingFS) -> None:
        count, _ = await upload_directory(tmp_dir, "viking://temp/test", viking_fs, max_file_size=5)
        # Most files are > 5 bytes, so fewer uploads
        assert count < 4


# ---------------------------------------------------------------------------
# detect_and_convert_encoding (additional edge cases)
# ---------------------------------------------------------------------------


class TestDetectAndConvertEncodingEdgeCases:
    def test_extensionless_text_file_encoding(self) -> None:
        text = "你好世界"
        content = text.encode("gbk")
        result = detect_and_convert_encoding(content, "LICENSE")
        # LICENSE is now recognized as text, so encoding conversion should happen
        assert result.decode("utf-8") == text

    def test_undecodable_content(self) -> None:
        # Note: TEXT_ENCODINGS includes iso-8859-1 which can decode any byte sequence,
        # so the "no matching encoding" branch is effectively unreachable.
        # This test verifies that arbitrary bytes are handled gracefully regardless.
        content = bytes(range(128, 256)) * 10
        result = detect_and_convert_encoding(content, "test.py")
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# should_skip_file (additional edge cases)
# ---------------------------------------------------------------------------


class TestShouldSkipFileEdgeCases:
    def test_oserror_on_stat(self, tmp_path: Path) -> None:
        f = tmp_path / "ghost.py"
        # File doesn't exist, stat() will raise OSError
        skip, reason = should_skip_file(f)
        assert skip is True
        assert "os error" in reason


# ---------------------------------------------------------------------------
# should_skip_directory (custom ignore_dirs)
# ---------------------------------------------------------------------------


class TestShouldSkipDirectoryCustom:
    def test_custom_ignore_dirs(self) -> None:
        assert should_skip_directory("vendor", ignore_dirs={"vendor"}) is True
        assert should_skip_directory("src", ignore_dirs={"vendor"}) is False

    def test_hidden_dir_with_custom_ignore(self) -> None:
        # Hidden dirs should still be skipped even with custom ignore set
        assert should_skip_directory(".secret", ignore_dirs={"vendor"}) is True


# ---------------------------------------------------------------------------
# _sanitize_rel_path (path traversal protection)
# ---------------------------------------------------------------------------


class TestSanitizeRelPath:
    def test_normal_path(self) -> None:
        assert _sanitize_rel_path("src/main.py") == "src/main.py"

    def test_rejects_parent_traversal(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            _sanitize_rel_path("../etc/passwd")

    def test_rejects_absolute_path(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            _sanitize_rel_path("/etc/passwd")

    def test_rejects_windows_drive_absolute(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            _sanitize_rel_path("C:\\Windows\\System32")

    def test_rejects_windows_drive_relative(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            _sanitize_rel_path("C:Windows\\System32")

    def test_rejects_nested_traversal(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            _sanitize_rel_path("foo/../../bar")

    def test_normalizes_backslashes(self) -> None:
        result = _sanitize_rel_path("src\\main.py")
        assert result == "src/main.py"


# ---------------------------------------------------------------------------
# upload_text_files (additional edge cases)
# ---------------------------------------------------------------------------


class TestUploadTextFilesEdgeCases:
    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, tmp_path: Path, viking_fs: FakeVikingFS) -> None:
        f = tmp_path / "evil.py"
        f.write_text("hack", encoding="utf-8")
        file_paths = [(f, "../../../etc/passwd")]

        count, warnings = await upload_text_files(file_paths, "viking://temp/safe", viking_fs)

        assert count == 0
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_upload_failure_produces_warning(self, tmp_path: Path) -> None:
        class FailingFS:
            async def write_file_bytes(self, uri: str, content: bytes) -> None:
                raise IOError("disk full")

            async def mkdir(self, uri: str, exist_ok: bool = False) -> None:
                pass

        f = tmp_path / "ok.py"
        f.write_text("print(1)", encoding="utf-8")
        file_paths = [(f, "ok.py")]

        count, warnings = await upload_text_files(file_paths, "viking://temp/fail", FailingFS())

        assert count == 0
        assert len(warnings) == 1
        assert "disk full" in warnings[0]


# ---------------------------------------------------------------------------
# upload_directory (additional edge cases)
# ---------------------------------------------------------------------------


class TestUploadDirectoryEdgeCases:
    @pytest.mark.asyncio
    async def test_write_failure_produces_warning(self, tmp_path: Path) -> None:
        class FailingWriteFS:
            async def write_file_bytes(self, uri: str, content: bytes) -> None:
                raise IOError("write error")

            async def mkdir(self, uri: str, exist_ok: bool = False) -> None:
                pass

        (tmp_path / "ok.py").write_text("print(1)", encoding="utf-8")

        count, warnings = await upload_directory(tmp_path, "viking://temp/fail", FailingWriteFS())

        assert count == 0
        assert len(warnings) == 1
        assert "write error" in warnings[0]


# ---------------------------------------------------------------------------
# _sanitize_rel_path (additional edge cases)
# ---------------------------------------------------------------------------


class TestSanitizeRelPathEdgeCases:
    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            _sanitize_rel_path("")

    def test_rejects_backslash_absolute(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            _sanitize_rel_path("\\Windows\\System32")
