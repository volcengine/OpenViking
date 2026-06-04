# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for get_resource_content_type and _sniff_content_type."""

import pytest

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

    def test_low_null_ratio_still_text(self):
        """Content with <=5% null bytes is treated as text."""
        # 51 null bytes out of 1024 = ~4.98% <= 5%
        content = b"\x00" * 51 + b"a" * 973
        assert _sniff_content_type(content) == ResourceContentType.TEXT

    def test_content_shorter_than_sniff_size(self):
        """Content shorter than _SNIFF_READ_SIZE uses actual length."""
        assert _sniff_content_type(b"hi") == ResourceContentType.TEXT
        assert _sniff_content_type(b"\x00\x00") is None
