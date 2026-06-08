# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for get_resource_content_type and _sniff_content_type."""

from types import SimpleNamespace

import pytest

import openviking.utils.embedding_utils as embedding_utils
from openviking.core.context import ResourceContentType
from openviking.utils.embedding_utils import _sniff_content_type, get_resource_content_type


class TestGetResourceContentTypeExtension:
    """Test extension-based type detection (no file_path, no I/O)."""

    @pytest.mark.parametrize(
        "file_name, expected",
        [
            ("test.py", ResourceContentType.TEXT),
            ("test.md", ResourceContentType.TEXT),
            ("test.json", ResourceContentType.TEXT),
            ("test.go", ResourceContentType.TEXT),
            ("test.rs", ResourceContentType.TEXT),
            ("test.java", ResourceContentType.TEXT),
            ("test.cpp", ResourceContentType.TEXT),
            ("test.c", ResourceContentType.TEXT),
            ("test.h", ResourceContentType.TEXT),
            ("test.js", ResourceContentType.TEXT),
            ("test.ts", ResourceContentType.TEXT),
            ("test.sh", ResourceContentType.TEXT),
            ("test.toml", ResourceContentType.TEXT),
            ("test.yaml", ResourceContentType.TEXT),
            ("test.yml", ResourceContentType.TEXT),
            ("test.xml", ResourceContentType.TEXT),
            ("test.csv", ResourceContentType.TEXT),
            ("test.ini", ResourceContentType.TEXT),
            ("test.cfg", ResourceContentType.TEXT),
            ("test.conf", ResourceContentType.TEXT),
            ("test.env", ResourceContentType.TEXT),
            ("test.properties", ResourceContentType.TEXT),
            ("test.rst", ResourceContentType.TEXT),
            ("test.tf", ResourceContentType.TEXT),
            ("test.proto", ResourceContentType.TEXT),
            ("test.gradle", ResourceContentType.TEXT),
            ("test.dart", ResourceContentType.TEXT),
            ("test.vue", ResourceContentType.TEXT),
            ("test.tsx", ResourceContentType.TEXT),
            ("test.jsx", ResourceContentType.TEXT),
            ("test.cs", ResourceContentType.TEXT),
            ("test.swift", ResourceContentType.TEXT),
            ("test.kt", ResourceContentType.TEXT),
            ("test.scala", ResourceContentType.TEXT),
            ("test.lua", ResourceContentType.TEXT),
            ("test.rb", ResourceContentType.TEXT),
            ("test.php", ResourceContentType.TEXT),
            ("test.sql", ResourceContentType.TEXT),
            ("test.r", ResourceContentType.TEXT),
            ("test.m", ResourceContentType.TEXT),
            ("test.pl", ResourceContentType.TEXT),
            ("test.erl", ResourceContentType.TEXT),
            ("test.ex", ResourceContentType.TEXT),
            ("test.exs", ResourceContentType.TEXT),
            ("test.jl", ResourceContentType.TEXT),
            ("test.groovy", ResourceContentType.TEXT),
            ("test.ps1", ResourceContentType.TEXT),
            ("test.bash", ResourceContentType.TEXT),
            ("test.zsh", ResourceContentType.TEXT),
            ("test.fish", ResourceContentType.TEXT),
            ("test.cc", ResourceContentType.TEXT),
            ("test.cxx", ResourceContentType.TEXT),
            ("test.hpp", ResourceContentType.TEXT),
            ("test.hh", ResourceContentType.TEXT),
            ("test.mm", ResourceContentType.TEXT),
        ],
    )
    @pytest.mark.asyncio
    async def test_text_extensions(self, file_name, expected):
        assert await get_resource_content_type(file_name) == expected

    @pytest.mark.parametrize(
        "file_name",
        [
            "test.png",
            "test.jpg",
            "test.jpeg",
            "test.gif",
            "test.bmp",
            "test.svg",
            "test.webp",
        ],
    )
    @pytest.mark.asyncio
    async def test_image_extensions(self, file_name):
        assert await get_resource_content_type(file_name) == ResourceContentType.IMAGE

    @pytest.mark.parametrize(
        "file_name",
        [
            "test.mp4",
            "test.avi",
            "test.mov",
            "test.wmv",
            "test.flv",
        ],
    )
    @pytest.mark.asyncio
    async def test_video_extensions(self, file_name):
        assert await get_resource_content_type(file_name) == ResourceContentType.VIDEO

    @pytest.mark.parametrize(
        "file_name",
        [
            "test.mp3",
            "test.wav",
            "test.aac",
            "test.flac",
        ],
    )
    @pytest.mark.asyncio
    async def test_audio_extensions(self, file_name):
        assert await get_resource_content_type(file_name) == ResourceContentType.AUDIO

    @pytest.mark.parametrize(
        "file_name",
        [
            "Makefile",
            "Dockerfile",
            "Cargo.lock",
            "yarn.lock",
            "Gemfile.lock",
            "foo.xyz",
            "unknown",
        ],
    )
    @pytest.mark.asyncio
    async def test_unknown_extension_returns_none(self, file_name):
        """Without file_path, unknown extensions return None."""
        assert await get_resource_content_type(file_name) is None

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        """Extension matching is case-insensitive."""
        assert await get_resource_content_type("TEST.PY") == ResourceContentType.TEXT
        assert await get_resource_content_type("IMAGE.PNG") == ResourceContentType.IMAGE
        assert await get_resource_content_type("VIDEO.MP4") == ResourceContentType.VIDEO
        assert await get_resource_content_type("AUDIO.MP3") == ResourceContentType.AUDIO


class TestGetResourceContentTypeSniffing:
    """Test async fallback sniffing path via VikingFS.read."""

    @staticmethod
    def _install_dummy_fs(monkeypatch, payload, calls=None):
        class DummyFS:
            async def read(self, file_path, offset=0, size=0, ctx=None):
                if calls is not None:
                    calls.append((file_path, offset, size, ctx))
                return payload

        monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS())

    @pytest.mark.asyncio
    async def test_unknown_extension_sniffed_as_text_via_read(self, monkeypatch):
        calls = []
        self._install_dummy_fs(monkeypatch, b"plain text content\nwith multiple lines\n", calls)

        result = await get_resource_content_type(
            "Makefile",
            file_path="viking://resources/project/Makefile",
        )

        assert result == ResourceContentType.TEXT
        assert calls == [("viking://resources/project/Makefile", 0, 1024, None)]

    @pytest.mark.asyncio
    async def test_known_extension_does_not_trigger_read(self, monkeypatch):
        class DummyFS:
            async def read(self, file_path, offset=0, size=0, ctx=None):
                raise AssertionError("read should not be called for known extensions")

        monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS())

        result = await get_resource_content_type(
            "known.py",
            file_path="viking://resources/project/known.py",
        )

        assert result == ResourceContentType.TEXT

    @pytest.mark.asyncio
    async def test_read_failure_returns_none(self, monkeypatch):
        class DummyFS:
            async def read(self, file_path, offset=0, size=0, ctx=None):
                raise RuntimeError("boom")

        monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: DummyFS())

        result = await get_resource_content_type(
            "unknown.bin",
            file_path="viking://resources/project/unknown.bin",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_read_receives_ctx(self, monkeypatch):
        seen_ctx = []

        ctx = SimpleNamespace(account_id="default")
        self._install_dummy_fs(monkeypatch, b"text from fs", seen_ctx)

        result = await get_resource_content_type(
            "Dockerfile",
            file_path="viking://resources/project/Dockerfile",
            ctx=ctx,
        )

        assert result == ResourceContentType.TEXT
        assert seen_ctx == [("viking://resources/project/Dockerfile", 0, 1024, ctx)]

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\n", None),
            (b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"a" * 20, None),
            (b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"a" * 20, None),
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, ResourceContentType.IMAGE),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 10, ResourceContentType.IMAGE),
            (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom", ResourceContentType.VIDEO),
            (b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 10, ResourceContentType.VIDEO),
            (b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 10, ResourceContentType.AUDIO),
        ],
    )
    @pytest.mark.asyncio
    async def test_unknown_extension_binary_payloads(self, monkeypatch, payload, expected):
        self._install_dummy_fs(monkeypatch, payload)

        result = await get_resource_content_type(
            "artifact.bin",
            file_path="viking://resources/project/artifact.bin",
        )

        assert result == expected

    @pytest.mark.parametrize(
        ("payload", "file_name"),
        [
            (b"\xef\xbb\xbfhello from utf8 bom\n", "README"),
            ("hello from utf16 le".encode("utf-16"), "notes.data"),
            ((b"\xfe\xff") + "hello from utf16 be".encode("utf-16-be"), "story.textblob"),
        ],
    )
    @pytest.mark.asyncio
    async def test_unknown_extension_text_encodings(self, monkeypatch, payload, file_name):
        self._install_dummy_fs(monkeypatch, payload)

        result = await get_resource_content_type(
            file_name,
            file_path=f"viking://resources/project/{file_name}",
        )

        assert result == ResourceContentType.TEXT

    @pytest.mark.asyncio
    async def test_unknown_extension_truncated_utf8_boundary_still_sniffs_as_text(
        self, monkeypatch
    ):
        payload = (b"a" * 1023) + "你".encode("utf-8")[:1]
        self._install_dummy_fs(monkeypatch, payload)

        result = await get_resource_content_type(
            "README",
            file_path="viking://resources/project/README",
        )

        assert result == ResourceContentType.TEXT

    @pytest.mark.asyncio
    async def test_unknown_extension_truncated_utf16_boundary_still_sniffs_as_text(
        self, monkeypatch
    ):
        payload = ("a" * 510 + "😀").encode("utf-16")[:1024]
        self._install_dummy_fs(monkeypatch, payload)

        result = await get_resource_content_type(
            "notes.data",
            file_path="viking://resources/project/notes.data",
        )

        assert result == ResourceContentType.TEXT

    @pytest.mark.asyncio
    async def test_unknown_extension_invalid_utf8_returns_none(self, monkeypatch):
        self._install_dummy_fs(monkeypatch, b"\xff\xfa\xf8\xf0")

        result = await get_resource_content_type(
            "corrupt.payload",
            file_path="viking://resources/project/corrupt.payload",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_extension_suspicious_control_bytes_returns_none(self, monkeypatch):
        payload = (b"\x01" * 30) + (b"a" * 970)
        self._install_dummy_fs(monkeypatch, payload)

        result = await get_resource_content_type(
            "opaque.data",
            file_path="viking://resources/project/opaque.data",
        )

        assert result is None


class TestSniffContentTypeText:
    """Test _sniff_content_type for text content."""

    def test_plain_text(self):
        assert _sniff_content_type(b"hello world") == ResourceContentType.TEXT

    def test_shell_script(self):
        assert _sniff_content_type(b"#!/bin/bash\necho hello") == ResourceContentType.TEXT

    def test_makefile_content(self):
        content = b"all:\n\tmake build\n\nclean:\n\trm -rf target\n"
        assert _sniff_content_type(content) == ResourceContentType.TEXT

    def test_cargo_lock_content(self):
        content = (
            b"# This file is automatically @generated by Cargo.\n"
            b"# It is not intended for manual editing.\n"
            b"version = 3\n\n"
            b"[[package]]\n"
            b'name = "addr2line"\n'
            b'version = "0.21.0"\n'
        )
        assert _sniff_content_type(content) == ResourceContentType.TEXT

    def test_json_content(self):
        assert _sniff_content_type(b'{"key": "value"}') == ResourceContentType.TEXT

    def test_xml_content(self):
        assert _sniff_content_type(b'<?xml version="1.0"?><root/>') == ResourceContentType.TEXT

    def test_utf8_text(self):
        assert _sniff_content_type("你好世界".encode("utf-8")) == ResourceContentType.TEXT

    def test_text_with_newlines(self):
        assert _sniff_content_type(b"line1\nline2\nline3\n") == ResourceContentType.TEXT


class TestSniffContentTypeImage:
    """Test _sniff_content_type for image content."""

    def test_png(self):
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        assert _sniff_content_type(content) == ResourceContentType.IMAGE

    def test_jpeg(self):
        content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        assert _sniff_content_type(content) == ResourceContentType.IMAGE

    def test_gif89a(self):
        content = b"GIF89a" + b"\x00" * 20
        assert _sniff_content_type(content) == ResourceContentType.IMAGE

    def test_gif87a(self):
        content = b"GIF87a" + b"\x00" * 20
        assert _sniff_content_type(content) == ResourceContentType.IMAGE

    def test_bmp(self):
        content = b"BM" + b"\x00" * 20
        assert _sniff_content_type(content) == ResourceContentType.IMAGE

    def test_webp(self):
        content = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 10
        assert _sniff_content_type(content) == ResourceContentType.IMAGE


class TestSniffContentTypeVideo:
    """Test _sniff_content_type for video content."""

    def test_mp4(self):
        content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        assert _sniff_content_type(content) == ResourceContentType.VIDEO

    def test_avi(self):
        content = b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 10
        assert _sniff_content_type(content) == ResourceContentType.VIDEO

    def test_flv(self):
        content = b"FLV\x01\x01\x00\x00\x00\x00\x00"
        assert _sniff_content_type(content) == ResourceContentType.VIDEO


class TestSniffContentTypeAudio:
    """Test _sniff_content_type for audio content."""

    def test_mp3_id3(self):
        content = b"ID3\x03\x00\x00\x00\x00\x00" + b"\x00" * 10
        assert _sniff_content_type(content) == ResourceContentType.AUDIO

    def test_wav(self):
        content = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 10
        assert _sniff_content_type(content) == ResourceContentType.AUDIO

    def test_flac(self):
        content = b"fLaC\x00\x00\x00\x00\x00" + b"\x00" * 10
        assert _sniff_content_type(content) == ResourceContentType.AUDIO


class TestSniffContentTypeEdgeCases:
    """Test _sniff_content_type edge cases."""

    def test_empty_content(self):
        assert _sniff_content_type(b"") is None

    def test_high_null_ratio_binary(self):
        content = b"\x00" * 100
        assert _sniff_content_type(content) is None

    def test_mixed_null_and_text(self):
        """Content with >5% null bytes is treated as binary."""
        # 60 null bytes out of 1024 = ~5.86% > 5%
        content = b"\x00" * 60 + b"a" * 964
        assert _sniff_content_type(content) is None

    def test_allowed_text_control_bytes_still_text(self):
        """Allowed text control bytes such as newline and tab still classify as text."""
        content = (b"a\n\tb\r\n" * 128) + b"plain text"
        assert _sniff_content_type(content) == ResourceContentType.TEXT

    def test_content_shorter_than_sniff_size(self):
        """Content shorter than _SNIFF_READ_SIZE uses actual length."""
        assert _sniff_content_type(b"hi") == ResourceContentType.TEXT
        assert _sniff_content_type(b"\x00\x00") is None

    def test_pdf_magic_is_not_misclassified_as_text(self):
        content = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\n"
        assert _sniff_content_type(content) is None

    def test_zip_magic_is_not_misclassified_as_text(self):
        content = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"a" * 20
        assert _sniff_content_type(content) is None

    def test_gzip_magic_is_not_misclassified_as_text(self):
        content = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + b"a" * 20
        assert _sniff_content_type(content) is None

    def test_invalid_utf8_is_not_misclassified_as_text(self):
        content = b"\xff\xfa\xf8\xf0"
        assert _sniff_content_type(content) is None

    def test_suspicious_control_chars_are_not_text(self):
        content = (b"\x01" * 30) + (b"a" * 970)
        assert _sniff_content_type(content) is None

    def test_utf16_with_bom_is_text(self):
        content = "hello world".encode("utf-16")
        assert _sniff_content_type(content) == ResourceContentType.TEXT

    def test_truncated_utf8_suffix_at_sniff_boundary_is_still_text(self):
        content = (b"a" * 1023) + "你".encode("utf-8")[:1]
        assert _sniff_content_type(content) == ResourceContentType.TEXT

    def test_truncated_utf16_suffix_at_sniff_boundary_is_still_text(self):
        content = ("a" * 510 + "😀").encode("utf-16")[:1024]
        assert _sniff_content_type(content) == ResourceContentType.TEXT

    def test_direct_sniff_uses_full_content_without_internal_truncation(self):
        content = (b"a" * 1024) + (b"\x01" * 64)
        assert _sniff_content_type(content) is None
