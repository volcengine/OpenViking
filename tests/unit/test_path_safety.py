# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.utils.path_safety import (
    safe_join_viking_uri,
    sanitize_relative_viking_path,
    validate_safe_viking_uri_path,
)


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


def test_sanitize_relative_viking_path_preserves_literal_current_directory_segment():
    assert sanitize_relative_viking_path("./scripts/check.py") == "./scripts/check.py"


@pytest.mark.parametrize(
    "rel_path",
    [
        "%2e/scripts/check.py",
        "%2e%2e/outside.py",
        "scripts%2fcheck.py",
        "scripts%5ccheck.py",
    ],
)
def test_sanitize_relative_viking_path_rejects_encoded_path_escape(rel_path):
    with pytest.raises(ValueError):
        sanitize_relative_viking_path(rel_path)


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


@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/proj/notes#1.md",
        "viking://user/u/memories/notes/meeting.md#chunk_0000",
        "viking://resources/proj/dir#1/b.md",
    ],
)
def test_validate_safe_viking_uri_path_allows_hash(uri):
    assert validate_safe_viking_uri_path(uri) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/proj/notes?draft=1",
        "viking://resources/proj/../secret.md",
        "viking://resources/proj/%2e%2e/secret.md",
    ],
)
def test_validate_safe_viking_uri_path_rejects_unsafe_uri(uri):
    with pytest.raises(ValueError):
        validate_safe_viking_uri_path(uri)
