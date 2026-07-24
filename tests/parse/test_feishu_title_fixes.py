# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for Feishu title handling across service/tree layers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.service.resource_service import ResourceService, _ResourceSourceInfo
from openviking.utils.feishu_naming import feishu_title_to_resource_segment


COMPOUND_TITLE = "Product Guide Web Setup-Auth/Profile/Settings"
FEISHU_URL = "https://example.feishu.cn/wiki/abc"


def test_target_doc_name_uses_feishu_naming_for_feishu_urls():
    source_info = _ResourceSourceInfo(
        source_name=None,
        source_path=FEISHU_URL,
        source_format="file",
    )
    name = ResourceService._target_doc_name(FEISHU_URL, COMPOUND_TITLE, source_info)
    assert name == feishu_title_to_resource_segment(COMPOUND_TITLE)
    assert "Web_Setup" in name


def test_target_doc_name_still_uses_smart_stem_for_http():
    source_info = _ResourceSourceInfo(
        source_name="report.docx",
        source_path="https://example.com/report.docx",
        source_format="file",
    )
    name = ResourceService._target_doc_name(
        "https://example.com/report.docx",
        "report.docx",
        source_info,
    )
    assert name == "report"


@pytest.mark.asyncio
async def test_tree_builder_feishu_sync_uses_title_not_stale_to_uri():
    from openviking.parse.tree_builder import TreeBuilder

    ctx = SimpleNamespace(account_id="acct", user=SimpleNamespace(user_id="user"))
    stale_to = "viking://resources/user/default/Product_Guide_Web_Setup-Archive"
    temp_name = feishu_title_to_resource_segment(COMPOUND_TITLE)

    with patch("openviking.parse.tree_builder.get_viking_fs") as mock_get_fs:
        fs = MagicMock()
        fs.exists = AsyncMock(return_value=True)
        fs.stat = AsyncMock(return_value={"isDir": True})
        mock_get_fs.return_value = fs

        builder = TreeBuilder()
        planned_uri, candidate = await builder.resolve_target_uri(
            ctx=ctx,
            doc_name=temp_name,
            scope="resources",
            to_uri=None,
            parent_uri="viking://resources/user/default",
            source_path=FEISHU_URL,
            source_format="file",
        )

    assert candidate == planned_uri
    assert "Web_Setup" in planned_uri
    assert stale_to not in planned_uri

    with patch("openviking.parse.tree_builder.get_viking_fs") as mock_get_fs:
        fs = MagicMock()
        mock_get_fs.return_value = fs
        builder = TreeBuilder()
        pinned_uri, candidate = await builder.resolve_target_uri(
            ctx=ctx,
            doc_name=temp_name,
            scope="resources",
            to_uri=stale_to,
            parent_uri=None,
            source_path=FEISHU_URL,
            source_format="file",
        )

    assert pinned_uri == stale_to
    assert candidate is None
