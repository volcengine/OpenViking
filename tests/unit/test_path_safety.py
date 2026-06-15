# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.utils.path_safety import safe_join_viking_uri, sanitize_relative_viking_path


def test_sanitize_relative_viking_path_normalizes_windows_separators():
    assert (
        sanitize_relative_viking_path("scripts\\check_bounding_boxes.py")
        == "scripts/check_bounding_boxes.py"
    )


def test_sanitize_relative_viking_path_preserves_posix_separators():
    assert (
        sanitize_relative_viking_path("scripts/check_bounding_boxes.py")
        == "scripts/check_bounding_boxes.py"
    )


@pytest.mark.parametrize(
    "rel_path",
    [
        "",
        "/absolute/file.txt",
        "\\absolute\\file.txt",
        "C:\\Windows\\System32",
        "C:Windows\\System32",
        "../outside.txt",
        "nested/../../outside.txt",
    ],
)
def test_sanitize_relative_viking_path_rejects_unsafe_paths(rel_path):
    with pytest.raises(ValueError):
        sanitize_relative_viking_path(rel_path)


def test_safe_join_viking_uri_sanitizes_relative_path():
    assert (
        safe_join_viking_uri(
            "viking://user/default/skills/pdf/",
            "scripts\\check_bounding_boxes.py",
        )
        == "viking://user/default/skills/pdf/scripts/check_bounding_boxes.py"
    )


def test_safe_join_viking_uri_preserves_posix_relative_path():
    assert (
        safe_join_viking_uri(
            "viking://user/default/skills/pdf/",
            "scripts/check_bounding_boxes.py",
        )
        == "viking://user/default/skills/pdf/scripts/check_bounding_boxes.py"
    )
