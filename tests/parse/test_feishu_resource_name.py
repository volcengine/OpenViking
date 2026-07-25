# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression: Feishu document titles must not be truncated at slash separators."""

from openviking.utils.feishu_naming import feishu_title_to_resource_segment
from openviking.utils.media_processor import _smart_stem

COMPOUND_TITLE = "Product Guide Web Setup-Auth/Profile/Settings"


def test_feishu_title_normalizes_spaces_and_slashes_without_truncating():
    name = feishu_title_to_resource_segment(COMPOUND_TITLE)
    # Every slash-separated component is preserved (not truncated to the last).
    assert "Web_Setup" in name
    assert "Auth_Profile_Settings" in name


def test_feishu_title_slash_becomes_underscore_not_truncated():
    name = feishu_title_to_resource_segment("API Docs/Overview")
    assert name == "API_Docs_Overview"
    # _smart_stem would truncate at the slash (Path.name -> "Overview").
    assert name != _smart_stem("API Docs/Overview")


def test_feishu_title_differs_from_smart_stem():
    name = feishu_title_to_resource_segment(COMPOUND_TITLE)
    assert name != _smart_stem(COMPOUND_TITLE)


def test_http_original_filename_still_uses_smart_stem():
    assert _smart_stem("report.docx") == "report"
