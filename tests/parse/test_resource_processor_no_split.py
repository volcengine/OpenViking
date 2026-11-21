# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for routing parsed resources through no-split mode."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from openviking.config.vlm import VLMHandle
from openviking.parse.output import LocalParseOutputStore
from openviking.parse.vlm import VLMProcessor
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError


@pytest.fixture
def processor() -> UnifiedResourceProcessor:
    vlm = Mock(spec=VLMHandle)
    vlm.is_available.return_value = False
    return UnifiedResourceProcessor(vlm_processor=VLMProcessor(vlm=vlm))


async def test_unified_processor_preserves_markdown_in_no_split_mode(
    tmp_path: Path, processor: UnifiedResourceProcessor
) -> None:
    source = tmp_path / "guide.md"
    content = "\n\n".join(
        f"# Section {i}\n\n" + "Configuration instructions. " * 40 for i in range(1, 4)
    )
    source.write_text(content, encoding="utf-8")
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))

    default = await processor.process(str(source), parse_output_store=store)
    assert default.artifact_ref is not None
    default_root = Path(default.artifact_ref.root) / default.artifact_ref.resource_rel
    assert len(list(default_root.glob("*.md"))) > 1

    result = await processor.process(str(source), parse_mode="no_split", parse_output_store=store)

    assert result.artifact_ref is not None
    root = Path(result.artifact_ref.root) / result.artifact_ref.resource_rel
    assert {file.name: file.read_text() for file in root.glob("*.md")} == {"guide.md": content}


async def test_parser_router_rejects_no_split_for_understanding_backend(
    tmp_path: Path, processor: UnifiedResourceProcessor
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"%PDF-fixture")

    with pytest.raises(InvalidArgumentError, match="no_split.*Understanding"):
        await processor.process(str(source), parse_mode="no_split", parser_backend="understanding")
