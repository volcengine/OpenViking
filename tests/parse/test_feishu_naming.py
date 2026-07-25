# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for Feishu title → resource segment helpers."""

import pytest

from openviking.utils.feishu_naming import (
    feishu_title_to_resource_segment,
    is_feishu_url,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.feishu.cn/docx/abc", True),
        ("https://example.feishu.cn/wiki/abc", True),
        ("https://example.larksuite.com/docx/abc", True),
        ("https://example.com/docx/abc", False),
        ("https://example.feishu.cn/other/abc", False),
    ],
)
def test_is_feishu_url(url: str, expected: bool):
    assert is_feishu_url(url) is expected


def test_feishu_title_empty_becomes_unnamed():
    assert feishu_title_to_resource_segment("") == "unnamed"
    assert feishu_title_to_resource_segment("   ") == "unnamed"


def test_feishu_title_normalizes_backslashes():
    assert feishu_title_to_resource_segment("a\\b/c") == "a_b_c"


def test_feishu_title_unifies_folder_and_markdown_stem():
    # The folder segment and the primary .md stem share this one value.
    segment = feishu_title_to_resource_segment("Project Tracker Base")
    assert segment == "Project_Tracker_Base"


def test_feishu_title_keeps_slash_prefix_without_path_semantics():
    assert feishu_title_to_resource_segment("API Docs/Overview") == "API_Docs_Overview"


def test_feishu_title_long_title_capped_by_sanitize_segment():
    # No bespoke truncation: an over-long title is capped by
    # VikingURI.sanitize_segment (the same cap the rest of the codebase uses).
    result = feishu_title_to_resource_segment("章" * 250)
    assert len(result) <= 50
    assert result == "章" * 50
