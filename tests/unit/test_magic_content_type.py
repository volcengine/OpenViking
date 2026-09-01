# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Demonstrates the content-based classification approach used for
vectorization: libmagic (python-magic) on the file's leading bytes decides
image/video/audio-vs-text-vs-unknown, so extensions carry no authority.

Fixtures live in tests/fixtures/files and are real files of each type.

Key properties pinned down by the fixtures:

* Real media files are recognized from their magic bytes and mapped to
  IMAGE / VIDEO / AUDIO.
* Text is recognized from content regardless of file name: an extensionless
  LICENSE, a Makefile, JSON, and even a .bin file holding plain text all
  classify as text.
* Binary content never classifies as text, even when named .txt (ELF bytes).
* SVG is the special case: libmagic reports it as image/svg+xml, it is not
  in the raster whitelist, and it decodes as text, so it classifies as TEXT.
"""

from pathlib import Path

import magic
import pytest

from openviking.core.context import ResourceContentType
from openviking.utils import embedding_utils

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "files"

PROBE_BYTES = 65536

#: (fixture name, exact libmagic mime, expected classification)
IMAGE_FIXTURES = {
    "favicon-32.png": ("image/png", ResourceContentType.IMAGE),
    "banner.jpg": ("image/jpeg", ResourceContentType.IMAGE),
    "sample.gif": ("image/gif", ResourceContentType.IMAGE),
    "agent-access.webp": ("image/webp", ResourceContentType.IMAGE),
}

VIDEO_FIXTURES = {
    "sample.mp4": ("video/mp4", ResourceContentType.VIDEO),
    "sample.avi": ("video/x-msvideo", ResourceContentType.VIDEO),
    "sample.mov": ("video/quicktime", ResourceContentType.VIDEO),
    "sample.wmv": ("video/x-ms-asf", ResourceContentType.VIDEO),
    "sample.flv": ("video/x-flv", ResourceContentType.VIDEO),
    "sample.mkv": ("video/x-matroska", ResourceContentType.VIDEO),
    "sample.webm": ("video/webm", ResourceContentType.VIDEO),
    # Audio in an MP4 container reports the container MIME; the vectorizer
    # handles AUDIO and VIDEO identically.
    "sample.m4a": ("video/mp4", ResourceContentType.VIDEO),
}

AUDIO_FIXTURES = {
    "sample.mp3": ("audio/mpeg", ResourceContentType.AUDIO),
    "sample.wav": ("audio/x-wav", ResourceContentType.AUDIO),
    "sample.aac": ("audio/x-hx-aac-adts", ResourceContentType.AUDIO),
    "sample.flac": ("audio/flac", ResourceContentType.AUDIO),
    # Ogg containers (Vorbis and Opus) share the audio/ogg MIME.
    "sample.ogg": ("audio/ogg", ResourceContentType.AUDIO),
    "sample.opus": ("audio/ogg", ResourceContentType.AUDIO),
}

TEXT_FIXTURES = {
    "notes.md": "text/plain",
    "LICENSE": "text/plain",
    "Makefile": "text/x-makefile",
    "config.json": "application/json",
    "logo.svg": "image/svg+xml",
    "sample.py": "text/x-script.python",
    "text_named_bin.bin": "text/plain",
}

BINARY_FIXTURES = {
    # Real compiled x86-64 PIE executable (built with cc).
    "sample.elf": "application/x-sharedlib",
    "binary_named_txt.txt": "application/x-sharedlib",
    # A 128-byte zip is below libmagic's confidence for the zip MIME on this
    # platform; it must still classify as unknown, which it does.
    "sample.zip": "application/octet-stream",
    "weights.bin": "application/octet-stream",
}


def _read_fixture(name: str, size: int = PROBE_BYTES) -> bytes:
    return (FIXTURES / name).read_bytes()[:size]


@pytest.mark.parametrize(
    "name,expected",
    sorted(
        {**IMAGE_FIXTURES, **VIDEO_FIXTURES, **AUDIO_FIXTURES}.items(),
        key=lambda item: item[0],
    ),
)
def test_media_is_classified_by_magic_bytes(name, expected):
    expected_mime, expected_type = expected
    prefix = _read_fixture(name)
    assert magic.from_buffer(prefix, mime=True) == expected_mime
    assert embedding_utils.classify_file_content(prefix) == expected_type


@pytest.mark.parametrize("name,expected_mime", sorted(TEXT_FIXTURES.items()))
def test_text_is_classified_from_content_regardless_of_name(name, expected_mime):
    prefix = _read_fixture(name)
    assert magic.from_buffer(prefix, mime=True) == expected_mime
    assert embedding_utils.is_text_bytes(prefix), f"{name} did not decode as text"
    assert embedding_utils.classify_file_content(prefix) == ResourceContentType.TEXT


@pytest.mark.parametrize("name,expected_mime", sorted(BINARY_FIXTURES.items()))
def test_binaries_never_classify_as_text(name, expected_mime):
    prefix = _read_fixture(name)
    assert magic.from_buffer(prefix, mime=True) == expected_mime
    assert not embedding_utils.is_text_bytes(prefix)
    assert embedding_utils.classify_file_content(prefix) is None


def test_text_extension_does_not_mask_binary_content():
    """ELF bytes named .txt must not reach the text pipeline."""
    assert embedding_utils.classify_file_content(_read_fixture("binary_named_txt.txt")) is None


def test_binary_extension_does_not_mask_text_content():
    """Plain text named .bin must still be parsed as text."""
    assert (
        embedding_utils.classify_file_content(_read_fixture("text_named_bin.bin"))
        == ResourceContentType.TEXT
    )


def test_svg_is_text_not_image():
    """libmagic reports image/svg+xml; it is XML text and must classify as
    TEXT, not IMAGE."""
    assert embedding_utils.classify_file_content(_read_fixture("logo.svg")) == ResourceContentType.TEXT


def test_empty_prefix_is_unknown():
    assert embedding_utils.classify_file_content(b"") is None


def test_probe_prefix_is_enough_for_real_files():
    """Classification from the first 64 KB must match full-file classification
    for every fixture, including the 200 KB banner.jpg."""
    for name in (
        list(IMAGE_FIXTURES)
        + list(VIDEO_FIXTURES)
        + list(AUDIO_FIXTURES)
        + list(TEXT_FIXTURES)
        + list(BINARY_FIXTURES)
    ):
        full = (FIXTURES / name).read_bytes()
        assert embedding_utils.classify_file_content(full[:PROBE_BYTES]) == embedding_utils.classify_file_content(full)
