# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Windows-reserved-name safety in resource URI segments.

Mirrors the Win32 device-name guard that PR #4517 added to the memory path
(``openviking/session/memory/utils/uri.py``) onto the resource path's
``VikingURI.sanitize_segment``. A segment whose stem is a Win32 reserved
name (``CON``, ``PRN``, ``NUL``, ``AUX``, ``COM1``..``COM9``,
``LPT1``..``LPT9``) is prefixed with ``_`` so it does not collide with a
Windows device name and cause a silent write failure.
"""

import pytest

from openviking_cli.utils.uri import VikingURI


@pytest.mark.parametrize(
    "reserved",
    ["CON", "PRN", "NUL", "AUX", "COM1", "LPT1"],
)
def test_sanitize_segment_rejects_windows_reserved_names(reserved: str):
    """A bare Win32 reserved stem is prefixed so it is not a device name."""
    result = VikingURI.sanitize_segment(reserved)
    assert result != reserved
    assert result.startswith("_")
    # Exact pin: for an already-clean name the only transformation
    # ``sanitize_segment`` applies is the reserved-stem prefix, so the result
    # is the input preceded by a single underscore.
    assert result == f"_{reserved}"


def test_sanitize_segment_reserved_with_extension():
    """A reserved stem carrying an extension is still guarded on the stem.

    ``CON.txt`` -> stem ``CON`` -> ``_CON.txt`` (extension preserved). The
    guard runs after the existing ``strip("_.")`` step, so the extension
    survives and only the stem is defused.
    """
    assert VikingURI.sanitize_segment("CON.txt") == "_CON.txt"


def test_sanitize_segment_preserves_normal_names():
    """Non-reserved names are unaffected by the added guard."""
    assert VikingURI.sanitize_segment("my_resource") == "my_resource"
    # CJK characters are preserved by the existing sanitizer and must not be
    # touched by the reserved-name guard.
    assert VikingURI.sanitize_segment("报告") == "报告"


def test_sanitize_segment_preserves_extension_on_long_names():
    """The length cap bounds the whole segment, not the stem alone.

    A 60-char stem carrying ``.pdf`` must keep its extension after the cap:
    no-split ingest flattens temp files shaped like ``<stem>.md`` into the
    stored URI, and the old inline ``[:50]`` used to amputate the extension.
    """
    result = VikingURI.sanitize_segment("a" * 60 + ".pdf")
    assert result.endswith(".pdf")
    assert len(result) <= 50


def test_sanitize_segment_no_split_md_hash_shape():
    """The exact shape produced by no-split ingest keeps its ``.md`` suffix.

    ``MarkdownParser._sanitize_for_path`` emits ``<41 chars>_<8-char hash>``
    (50 chars) and ``.md`` is appended afterwards, so the temp file name is
    53 chars. The cap must budget for the extension instead of cutting it.
    """
    result = VikingURI.sanitize_segment("x" * 41 + "_abcd1234.md")
    assert result.endswith(".md")
    assert len(result) == 50
    assert result.startswith("x" * 41)


def test_sanitize_segment_multiple_dots():
    """Only the last dot-run is treated as the extension candidate."""
    assert VikingURI.sanitize_segment("a.b.c") == "a.b.c"


def test_sanitize_segment_trailing_dot_stripped():
    """Trailing dots are stripped before any extension detection runs."""
    assert VikingURI.sanitize_segment("名字.") == "名字"


def test_sanitize_segment_long_cjk_truncates():
    """Names without a dot keep the plain truncation behavior."""
    assert VikingURI.sanitize_segment("报" * 60) == "报" * 50


def test_sanitize_segment_long_reserved_with_extension():
    """A long reserved-stem name keeps its extension without the guard.

    The rejoined stem is no longer a bare device name, so no ``_`` prefix
    is added; the extension must still survive the cap.
    """
    result = VikingURI.sanitize_segment("CON" + "a" * 50 + ".md")
    assert result.endswith(".md")
    assert len(result) <= 50
    assert not result.startswith("_")


def test_sanitize_segment_long_version_tail_not_extension():
    """A long tail after a version dot is not misread as an extension.

    ``v1.2`` followed by 48 chars has a 49-char tail (``2`` + stem), which
    exceeds the extension budget, so the name falls back to plain truncation.
    """
    name = "v1.2" + "b" * 48
    assert VikingURI.sanitize_segment(name) == name[:50]


@pytest.mark.parametrize(
    "name",
    ["report.md", "v1.2", "a.b.c", "my.song_mp4", "draft_v2.pdf"],
)
def test_sanitize_segment_short_names_unchanged(name: str):
    """Short names pass through exactly as before the cap change."""
    assert VikingURI.sanitize_segment(name) == name
