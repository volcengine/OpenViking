# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression: the default Feishu import path (no caller-supplied source_name)
must name the resource from meta["feishu_title"], not the temp-file stem.

This is the reproduction from #3025: importing a Feishu doc whose title contains
a slash previously landed under the temp upload stem instead of the title.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.utils.feishu_naming import feishu_title_to_resource_segment
from openviking.utils.media_processor import UnifiedResourceProcessor

FEISHU_URL = "https://example.feishu.cn/docx/abc"
COMPOUND_TITLE = "API Docs/Overview"


def _feishu_local_resource(tmp_path: Path) -> LocalResource:
    # A realistic accessor output: a temp markdown file whose stem is a
    # meaningless upload id, with the real title only in meta["feishu_title"].
    temp_file = tmp_path / "feishu_9f8e7d6c.md"
    temp_file.write_text("# body", encoding="utf-8")
    return LocalResource(
        path=temp_file,
        source_type=SourceType.FEISHU,
        original_source=FEISHU_URL,
        meta={"feishu_title": COMPOUND_TITLE, "feishu_doc_type": "docx"},
        is_temporary=False,
    )


@pytest.mark.asyncio
async def test_default_feishu_import_names_resource_from_title(tmp_path):
    processor = UnifiedResourceProcessor(storage=MagicMock())

    local_resource = _feishu_local_resource(tmp_path)
    registry = MagicMock()
    registry.access = AsyncMock(return_value=local_resource)

    captured = {}

    async def _fake_parse(_resource, **parse_kwargs):
        captured.update(parse_kwargs)
        return SimpleNamespace(source_path=None, temp_dir_path=None)

    router = MagicMock()
    router.parse = _fake_parse

    with patch.object(processor, "_get_accessor_registry", return_value=registry), \
         patch.object(processor, "_get_parser_router", return_value=router), \
         patch.object(processor, "_get_vlm_processor", return_value=None):
        # No source_name kwarg -> exercises the default import path.
        await processor.process(FEISHU_URL)

    # The resource name must come from the title, not the temp-file stem.
    assert captured["resource_name"] == feishu_title_to_resource_segment(COMPOUND_TITLE)
    assert captured["resource_name"] == "API_Docs_Overview"
    assert captured.get("resource_name_is_safe") is True
    assert "feishu_9f8e7d6c" not in captured["resource_name"]


@pytest.mark.asyncio
async def test_explicit_source_name_still_wins_for_feishu(tmp_path):
    processor = UnifiedResourceProcessor(storage=MagicMock())

    local_resource = _feishu_local_resource(tmp_path)
    registry = MagicMock()
    registry.access = AsyncMock(return_value=local_resource)

    captured = {}

    async def _fake_parse(_resource, **parse_kwargs):
        captured.update(parse_kwargs)
        return SimpleNamespace(source_path=None, temp_dir_path=None)

    router = MagicMock()
    router.parse = _fake_parse

    with patch.object(processor, "_get_accessor_registry", return_value=registry), \
         patch.object(processor, "_get_parser_router", return_value=router), \
         patch.object(processor, "_get_vlm_processor", return_value=None):
        await processor.process(FEISHU_URL, source_name="Explicit Title/Sub")

    assert captured["resource_name"] == feishu_title_to_resource_segment("Explicit Title/Sub")
    assert captured["resource_name"] == "Explicit_Title_Sub"
