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
