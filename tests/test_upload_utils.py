# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for shared upload utilities."""

from pathlib import Path
from typing import Dict, List

import pytest

from openviking.parse.parsers.upload_utils import (
    detect_and_convert_encoding,
    is_text_file,
    should_skip_file,
    upload_directory,
)
from openviking.utils.path_safety import sanitize_relative_viking_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
        # .jsonl is treated as text (matching .json) so upload-time encoding
        # normalization applies, mirroring its inclusion in the vectorization
        # text-extension set (#2745); otherwise a legacy-encoded .jsonl skips
        # UTF-8 normalization while .json does not (#2744/#2770).
        assert is_text_file("data.jsonl") is True

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

    def test_sqlite_extensions_are_ignored(self, tmp_path: Path) -> None:
        for name in ["cache.sqlite", "cache.sqlite3"]:
            f = tmp_path / name
            f.write_bytes(b"SQLite format 3\x00")
            skip, reason = should_skip_file(f)
            assert skip is True
            assert f.suffix in reason

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


# ---------------------------------------------------------------------------
# upload_text_files
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# upload_directory
# ---------------------------------------------------------------------------


class _RecordingStore:
    """Minimal parse output store recording writes/mkdirs by relative path."""

    def __init__(self) -> None:
        self.writes: Dict[str, bytes] = {}
        self.dirs: List[str] = []

    async def mkdir(self, ref, rel_path: str = "") -> None:
        self.dirs.append(rel_path)

    async def write_bytes(self, ref, rel_path: str, content: bytes) -> None:
        self.writes[rel_path] = content

    async def read_bytes(self, ref, rel_path: str) -> bytes:
        return self.writes[rel_path]


class TestUploadDirectory:
    @pytest.mark.asyncio
    async def test_basic_upload(self, tmp_dir: Path) -> None:
        store = _RecordingStore()
        count, warnings = await upload_directory(
            tmp_dir, "repo", store=store, artifact_ref=object()
        )

        # Should upload: hello.py, readme.md, config.yaml, src/main.go
        # Should skip: .hidden, image.png, empty.txt, __pycache__/mod.pyc
        assert count == 4
        assert "repo/hello.py" in store.writes
        assert "repo/readme.md" in store.writes
        assert "repo/config.yaml" in store.writes
        assert "repo/src/main.go" in store.writes

    @pytest.mark.asyncio
    async def test_skips_hidden_files(self, tmp_dir: Path) -> None:
        store = _RecordingStore()
        await upload_directory(tmp_dir, "repo", store=store, artifact_ref=object())
        assert all(".hidden" not in rel for rel in store.writes)

    @pytest.mark.asyncio
    async def test_skips_ignored_dirs(self, tmp_dir: Path) -> None:
        store = _RecordingStore()
        await upload_directory(tmp_dir, "repo", store=store, artifact_ref=object())
        assert all("__pycache__" not in rel for rel in store.writes)

    @pytest.mark.asyncio
    async def test_skips_ignored_extensions(self, tmp_dir: Path) -> None:
        store = _RecordingStore()
        await upload_directory(tmp_dir, "repo", store=store, artifact_ref=object())
        assert all(".png" not in rel for rel in store.writes)

    @pytest.mark.asyncio
    async def test_skips_empty_files(self, tmp_dir: Path) -> None:
        (tmp_dir / "application.properties").write_bytes(b"\n")
        (tmp_dir / "blank.txt").write_bytes(b" \t\r\n")
        store = _RecordingStore()
        await upload_directory(tmp_dir, "repo", store=store, artifact_ref=object())
        assert all("empty.txt" not in rel for rel in store.writes)
        assert "repo/application.properties" not in store.writes
        assert "repo/blank.txt" not in store.writes

    @pytest.mark.asyncio
    async def test_precreates_subdirectories(self, tmp_dir: Path) -> None:
        store = _RecordingStore()
        await upload_directory(tmp_dir, "repo", store=store, artifact_ref=object())
        # The nested src/ dir is pre-created before its files are written.
        assert "repo/src" in store.dirs

    @pytest.mark.asyncio
    async def test_custom_ignore_dirs(self, tmp_dir: Path) -> None:
        store = _RecordingStore()
        count, _ = await upload_directory(
            tmp_dir, "repo", store=store, artifact_ref=object(), ignore_dirs={"src"}
        )
        assert all("src/" not in rel for rel in store.writes)
        # Positive assertion: non-ignored files should still be uploaded
        assert count > 0
        assert "repo/hello.py" in store.writes

    @pytest.mark.asyncio
    async def test_custom_max_file_size(self, tmp_dir: Path) -> None:
        store = _RecordingStore()
        count, _ = await upload_directory(
            tmp_dir, "repo", store=store, artifact_ref=object(), max_file_size=5
        )
        # Most files are > 5 bytes, so fewer uploads
        assert count < 4

    @pytest.mark.asyncio
    async def test_respects_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        (tmp_path / "keep.txt").write_text("ok", encoding="utf-8")
        (tmp_path / "skip.tmp").write_text("no", encoding="utf-8")

        store = _RecordingStore()
        count, _ = await upload_directory(tmp_path, "gi", store=store, artifact_ref=object())

        assert count == 1
        assert "gi/keep.txt" in store.writes
        assert "gi/skip.tmp" not in store.writes


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
        # Arbitrary bytes should be handled gracefully even when no text
        # encoding can be selected with confidence.
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


# ---------------------------------------------------------------------------
# sanitize_relative_viking_path (path traversal protection)
# ---------------------------------------------------------------------------


class TestSanitizeRelPath:
    def test_normal_path(self) -> None:
        assert sanitize_relative_viking_path("src/main.py") == "src/main.py"

    def test_rejects_parent_traversal(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            sanitize_relative_viking_path("../etc/passwd")

    def test_rejects_absolute_path(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            sanitize_relative_viking_path("/etc/passwd")

    def test_rejects_windows_drive_absolute(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            sanitize_relative_viking_path("C:\\Windows\\System32")

    def test_rejects_windows_drive_relative(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            sanitize_relative_viking_path("C:Windows\\System32")

    def test_rejects_nested_traversal(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            sanitize_relative_viking_path("foo/../../bar")

    def test_normalizes_backslashes(self) -> None:
        result = sanitize_relative_viking_path("src\\main.py")
        assert result == "src/main.py"


# ---------------------------------------------------------------------------
# upload_text_files (additional edge cases)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# upload_directory (failure handling + md5 manifest)
# ---------------------------------------------------------------------------


class TestUploadDirectoryEdgeCases:
    @pytest.mark.asyncio
    async def test_writes_content_md5_manifest(self, tmp_dir: Path) -> None:
        import hashlib
        import json

        from openviking.parse.parsers.upload_utils import ARTIFACT_MANIFEST_NAME

        store = _RecordingStore()

        await upload_directory(tmp_dir, "repo", store=store, artifact_ref=object())

        # A manifest of final-byte md5 keyed by artifact-relative path is written
        # so the incremental diff can compare fingerprints without re-reading.
        assert ARTIFACT_MANIFEST_NAME in store.writes
        manifest = json.loads(store.writes[ARTIFACT_MANIFEST_NAME].decode("utf-8"))
        assert manifest["repo/hello.py"] == hashlib.md5(b"print('hello')").hexdigest()
        # Every uploaded business file has an md5; the manifest itself is excluded.
        assert "repo/src/main.go" in manifest
        assert ARTIFACT_MANIFEST_NAME not in manifest

    @pytest.mark.asyncio
    async def test_store_write_failure_produces_warning_and_skips_manifest(
        self, tmp_path: Path
    ) -> None:
        from openviking.parse.parsers.upload_utils import ARTIFACT_MANIFEST_NAME

        class FailingStore:
            async def mkdir(self, ref, rel_path: str = "") -> None:
                pass

            async def write_bytes(self, ref, rel_path: str, content: bytes) -> None:
                if rel_path == ARTIFACT_MANIFEST_NAME:
                    raise AssertionError("manifest must not be written after a failed upload")
                raise IOError("store write error")

        (tmp_path / "ok.py").write_text("print(1)", encoding="utf-8")

        count, warnings = await upload_directory(
            tmp_path, "repo", store=FailingStore(), artifact_ref=object()
        )

        assert count == 0
        assert len(warnings) == 1
        assert "store write error" in warnings[0]


# ---------------------------------------------------------------------------
# sanitize_relative_viking_path (additional edge cases)
# ---------------------------------------------------------------------------


class TestSanitizeRelPathEdgeCases:
    def test_rejects_empty_path(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            sanitize_relative_viking_path("")

    def test_rejects_backslash_absolute(self) -> None:
        with pytest.raises(ValueError, match="Unsafe"):
            sanitize_relative_viking_path("\\Windows\\System32")
