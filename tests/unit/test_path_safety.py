# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import logging

import pytest

from openviking.utils.path_safety import (
    normalize_storage_target_uri,
    safe_join_viking_uri,
    sanitize_relative_viking_path,
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


def test_normalize_storage_target_uri_warns_when_sanitized_to_different_path(caplog):
    with caplog.at_level(logging.WARNING, logger="openviking.utils.path_safety"):
        normalized = normalize_storage_target_uri("viking://user/test_user/memories/a b.md")

    assert normalized == "viking://user/test_user/memories/a_b.md"
    assert "viking://user/test_user/memories/a b.md" in caplog.text
    assert "viking://user/test_user/memories/a_b.md" in caplog.text


def test_normalize_storage_target_uri_no_warning_when_path_unchanged(caplog):
    with caplog.at_level(logging.WARNING, logger="openviking.utils.path_safety"):
        normalized = normalize_storage_target_uri("viking://user/test_user/memories/a_b.md")

    assert normalized == "viking://user/test_user/memories/a_b.md"
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_normalize_storage_target_uri_warns_includes_both_full_uris(caplog):
    requested = "viking://user/test_user/memories/notes/2026 plan.md"
    with caplog.at_level(logging.WARNING, logger="openviking.utils.path_safety"):
        normalized = normalize_storage_target_uri(requested)

    assert normalized == "viking://user/test_user/memories/notes/2026_plan.md"
    assert requested in caplog.text
    assert normalized in caplog.text


def test_whitespace_naming_asymmetry_between_write_and_extraction(caplog):
    """The #6521 fork: extraction keeps interior spaces while write sanitizes them.

    A space-bearing URI produced by extraction is addressable by read, but a
    write through the same URI is stored under the underscore name; the
    warning added to ``normalize_storage_target_uri`` surfaces that divergence
    instead of succeeding silently.
    """
    from openviking.session.memory.utils.uri import _portable_uri_segment
    assert _portable_uri_segment("a b.md") == "a b.md"

    with caplog.at_level(logging.WARNING, logger="openviking.utils.path_safety"):
        assert (
            normalize_storage_target_uri("viking://user/test_user/memories/a b.md")
            == "viking://user/test_user/memories/a_b.md"
        )
    assert caplog.records
