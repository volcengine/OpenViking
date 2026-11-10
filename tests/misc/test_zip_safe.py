# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for zip_safe Zip Slip protection and filename normalization."""

from __future__ import annotations

import io
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from openviking.utils.zip_safe import (
    _contains_cjk,
    _contains_common_mojibake,
    normalize_zip_filenames,
    safe_extract_zip,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_zip_bytes(entries: dict[str, str]) -> bytes:
    """Create an in-memory zip with the given filename->content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_zip_with_raw_info(entries: list[tuple[zipfile.ZipInfo, str]]) -> bytes:
    """Create an in-memory zip using raw ZipInfo objects."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for info, content in entries:
            zf.writestr(info, content)
    return buf.getvalue()


def _clear_utf8_filename_flags(data: bytes) -> bytes:
    """Clear bit 11 in every local and central-directory file header."""
    patched = bytearray(data)
    header_specs = (
        (b"PK\x03\x04", 6),
        (b"PK\x01\x02", 8),
    )
    for signature, flag_offset in header_specs:
        cursor = 0
        matched = 0
        while True:
            header_offset = data.find(signature, cursor)
            if header_offset < 0:
                break
            flags = struct.unpack_from("<H", patched, header_offset + flag_offset)[0]
            struct.pack_into("<H", patched, header_offset + flag_offset, flags & ~0x800)
            matched += 1
            cursor = header_offset + len(signature)
        assert matched > 0, f"ZIP header not found: {signature!r}"
    return bytes(patched)


def _assert_no_escape(tmp_path: Path, dest_dir: Path) -> None:
    """Assert no extracted files escaped dest_dir."""
    for f in tmp_path.rglob("*"):
        resolved = f.resolve()
        if resolved == dest_dir.resolve() or resolved.is_relative_to(dest_dir.resolve()):
            continue
        if f.suffix == ".zip":
            continue
        raise AssertionError(f"File escaped dest_dir: {resolved}")


# ── _contains_cjk ───────────────────────────────────────────────────────────


class TestContainsCJK:
    """Verify CJK character detection."""

    def test_detects_cjk_unified_ideographs(self) -> None:
        assert _contains_cjk("文件.txt") is True

    def test_detects_cjk_extension_a(self) -> None:
        assert _contains_cjk("\u3400test") is True

    def test_detects_cjk_punctuation(self) -> None:
        assert _contains_cjk("test\u3001test") is True  # ideographic comma

    def test_detects_fullwidth_forms(self) -> None:
        assert _contains_cjk("\uff10") is True  # fullwidth digit zero

    def test_rejects_ascii_only(self) -> None:
        assert _contains_cjk("hello.txt") is False

    def test_rejects_empty_string(self) -> None:
        assert _contains_cjk("") is False

    def test_rejects_latin_extended(self) -> None:
        assert _contains_cjk("café.txt") is False


# ── _contains_common_mojibake ────────────────────────────────────────────────


class TestContainsCommonMojibake:
    """Verify mojibake pattern detection."""

    def test_detects_greek_chars(self) -> None:
        mojibake = "αβ".encode("utf-8").decode("cp437")
        assert _contains_common_mojibake(mojibake) is True

    def test_detects_cp437_micro_sign_from_cjk_utf8(self) -> None:
        mojibake = "怀".encode("utf-8").decode("cp437")
        assert mojibake == "µÇÇ"
        assert _contains_common_mojibake(mojibake) is True

    def test_detects_math_operators(self) -> None:
        assert _contains_common_mojibake("\u2200x") is True  # for-all

    def test_detects_box_drawing(self) -> None:
        assert _contains_common_mojibake("\u2500") is True

    def test_rejects_ascii_only(self) -> None:
        assert _contains_common_mojibake("normal.txt") is False

    def test_rejects_cjk_chars(self) -> None:
        assert _contains_common_mojibake("文件.txt") is False

    def test_rejects_empty_string(self) -> None:
        assert _contains_common_mojibake("") is False


# ── normalize_zip_filenames ──────────────────────────────────────────────────


class TestNormalizeZipFilenames:
    """Verify UTF-8 filename repair logic."""

    def test_skips_entries_with_utf8_flag_set(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("test.txt")
            info.flag_bits = 0x800  # UTF-8 flag
            zf.writestr(info, "content")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            original_name = zf.infolist()[0].filename
            normalize_zip_filenames(zf)
            assert zf.infolist()[0].filename == original_name

    def test_does_not_alter_pure_ascii_entries(self) -> None:
        data = _make_zip_bytes({"readme.txt": "hello"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            normalize_zip_filenames(zf)
            assert zf.infolist()[0].filename == "readme.txt"

    @pytest.mark.parametrize("cjk_name", ["测试文件.txt", "怀.txt"])
    def test_repairs_cjk_filename_from_cp437_mojibake(
        self,
        cjk_name: str,
        tmp_path: Path,
    ) -> None:
        """Simulate a CJK filename stored without UTF-8 flag.

        The zip module reads it via cp437 producing mojibake.
        normalize_zip_filenames should re-decode from cp437 -> UTF-8.
        """
        # Python correctly sets bit 11 for non-ASCII names. Patch both copies
        # of the flag to reproduce an archive that stored UTF-8 bytes but
        # forgot to declare their encoding.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(cjk_name, "content")
        malformed_zip = _clear_utf8_filename_flags(buf.getvalue())
        cp437_name = cjk_name.encode("utf-8").decode("cp437")

        with zipfile.ZipFile(io.BytesIO(malformed_zip), "r") as zf:
            member = zf.infolist()[0]
            assert member.flag_bits & 0x800 == 0
            assert member.filename == cp437_name
            normalize_zip_filenames(zf)
            repaired = zf.infolist()[0]
            assert repaired.filename == cjk_name, (
                f"Expected filename to be repaired to {cjk_name!r}, got {repaired.filename!r}"
            )
            assert zf.read(repaired) == b"content"

        extract_dir = tmp_path / "repaired"
        with zipfile.ZipFile(io.BytesIO(malformed_zip), "r") as zf:
            safe_extract_zip(zf, extract_dir)
        assert (extract_dir / cjk_name).read_bytes() == b"content"


# ── safe_extract_zip ─────────────────────────────────────────────────────────


class TestSafeExtractZipNormal:
    """Verify normal zip extraction works correctly."""

    def test_extracts_single_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"hello.txt": "world"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            safe_extract_zip(zf, dest)
        assert (dest / "hello.txt").read_text() == "world"

    def test_extracts_nested_directories(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes(
            {
                "src/main.py": "print('hello')",
                "src/utils/helper.py": "pass",
                "README.md": "# Test",
            }
        )
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            safe_extract_zip(zf, dest)
        assert (dest / "src" / "main.py").read_text() == "print('hello')"
        assert (dest / "src" / "utils" / "helper.py").read_text() == "pass"
        assert (dest / "README.md").read_text() == "# Test"

    def test_extracts_empty_zip(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            pass  # empty
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            safe_extract_zip(zf, dest)
        assert list(dest.iterdir()) == []

    def test_extracts_directory_entries(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mydir/", "")
            zf.writestr("mydir/file.txt", "content")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            safe_extract_zip(zf, dest)
        assert (dest / "mydir" / "file.txt").read_text() == "content"

    def test_handles_special_characters_in_filenames(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes(
            {
                "spaces in name.txt": "content",
                "file (1).txt": "content",
                "file-with-dashes.txt": "content",
                "file_with_underscores.txt": "content",
            }
        )
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            safe_extract_zip(zf, dest)
        assert (dest / "spaces in name.txt").exists()
        assert (dest / "file (1).txt").exists()


class TestSafeExtractZipSlipPrevention:
    """Verify Zip Slip path traversal attacks are rejected."""

    def test_rejects_dot_dot_traversal(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"../../evil.txt": "pwned"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)
        _assert_no_escape(tmp_path, dest)

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"/etc/passwd": "root:x:0:0"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)
        _assert_no_escape(tmp_path, dest)

    def test_rejects_nested_traversal(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"foo/../../evil.txt": "pwned"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)
        _assert_no_escape(tmp_path, dest)

    def test_rejects_deeply_nested_traversal(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"a/b/c/../../../../evil.txt": "pwned"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)
        _assert_no_escape(tmp_path, dest)

    def test_rejects_windows_drive_path(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"C:\\evil.txt": "pwned"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)

    def test_rejects_backslash_traversal(self, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"..\\..\\evil.txt": "pwned"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)
        _assert_no_escape(tmp_path, dest)

    def test_allows_safe_dot_in_filename(self, tmp_path: Path) -> None:
        """Filenames with dots (not traversal) should be allowed."""
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes(
            {
                ".gitignore": "*.pyc",
                "src/.env.example": "KEY=val",
                "dir/file.tar.gz": "data",
            }
        )
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            safe_extract_zip(zf, dest)
        assert (dest / ".gitignore").read_text() == "*.pyc"
        assert (dest / "src" / ".env.example").read_text() == "KEY=val"

    def test_allows_current_dir_prefix(self, tmp_path: Path) -> None:
        """Paths starting with ./ are safe and should be allowed."""
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes({"./safe.txt": "content"})
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            safe_extract_zip(zf, dest)
        assert (dest / "safe.txt").exists()

    def test_normalizes_windows_separators(self, tmp_path: Path) -> None:
        """Benign Windows-style member paths should extract as directories."""
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes(
            {
                "project\\file1.txt": "content1",
                "project\\subdir\\file2.txt": "content2",
            }
        )
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            safe_extract_zip(zf, dest)

        assert (dest / "project" / "file1.txt").read_text() == "content1"
        assert (dest / "project" / "subdir" / "file2.txt").read_text() == "content2"

    def test_first_malicious_entry_prevents_all_extraction(self, tmp_path: Path) -> None:
        """If the first entry is malicious, no files should be extracted."""
        dest = tmp_path / "out"
        dest.mkdir()
        data = _make_zip_bytes(
            {
                "../../evil.txt": "pwned",
                "safe.txt": "this should not be extracted",
            }
        )
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)
        assert not (dest / "safe.txt").exists()

    def test_malicious_entry_after_safe_entries(self, tmp_path: Path) -> None:
        """If a later entry is malicious, earlier entries may be extracted but error is raised."""
        dest = tmp_path / "out"
        dest.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("safe.txt", "ok")
            zf.writestr("../../evil.txt", "pwned")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(ValueError, match="Zip Slip"):
                safe_extract_zip(zf, dest)
        _assert_no_escape(tmp_path, dest)


class TestSafeExtractZipSymlink:
    """Verify behavior with symlink entries in zip archives."""

    def test_symlink_entry_does_not_create_symlink_outside_dest(self, tmp_path: Path) -> None:
        """Symlink entries pointing outside dest_dir should not escape."""
        dest = tmp_path / "out"
        dest.mkdir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("evil_link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            safe_extract_zip(zf, dest)
        # Verify no symlink was created pointing outside
        for item in dest.rglob("*"):
            if item.is_symlink():
                target = item.resolve()
                assert target.is_relative_to(dest.resolve()), (
                    f"Symlink {item} points outside dest: {target}"
                )
