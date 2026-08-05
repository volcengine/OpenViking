# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for routing parsed resources through no-split mode."""

from pathlib import Path
from typing import Any

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parser_router import ParserRouter
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError


class _AccessorRegistry:
    def __init__(self, resource: LocalResource) -> None:
        self.resource = resource

    async def access(self, _source: str, **_kwargs: Any) -> LocalResource:
        return self.resource


class _RecordingParserRouter:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def parse(self, resource: LocalResource, **kwargs: Any):
        self.kwargs = kwargs
        return create_parse_result(
            root=ResourceNode(type=NodeType.ROOT, title=resource.path.stem),
            source_path=str(resource.path),
            source_format="markdown",
            parser_name="RecordingParser",
        )


class _UnusedRegistry:
    async def parse(self, _source: Any, **_kwargs: Any):
        raise AssertionError("internal parser must not run when Understanding is selected")


def _local_resource(path: Path) -> LocalResource:
    return LocalResource(
        path=path,
        source_type=SourceType.LOCAL,
        original_source=str(path),
        is_temporary=False,
    )


@pytest.mark.asyncio
async def test_unified_processor_no_split_still_uses_parser_router(
    tmp_path: Path,
) -> None:
    source = tmp_path / "guide.md"
    source.write_text("# guide", encoding="utf-8")
    router = _RecordingParserRouter()
    processor = UnifiedResourceProcessor(vlm_processor=object())
    processor._accessor_registry = _AccessorRegistry(_local_resource(source))
    processor._parser_router = router

    result = await processor.process(str(source), parse_mode="no_split")

    assert result.parser_name == "RecordingParser"
    assert router.kwargs is not None
    assert router.kwargs["split_content"] is False


@pytest.mark.asyncio
async def test_parser_router_rejects_no_split_for_understanding_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"%PDF-fixture")
    router = ParserRouter(_UnusedRegistry())  # type: ignore[arg-type]
    monkeypatch.setattr(router, "should_use_understanding_api", lambda *_args, **_kwargs: True)

    with pytest.raises(InvalidArgumentError, match="no_split.*Understanding"):
        await router.parse(_local_resource(source), split_content=False)
