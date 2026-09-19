# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for rejecting an empty fetched resource."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.utils.media_processor import UnifiedResourceProcessor
from openviking_cli.exceptions import InvalidArgumentError


def _processor(monkeypatch, resource: LocalResource) -> UnifiedResourceProcessor:
    processor = UnifiedResourceProcessor.__new__(UnifiedResourceProcessor)
    monkeypatch.setattr(
        processor,
        "_get_accessor_registry",
        lambda: SimpleNamespace(access=AsyncMock(return_value=resource)),
        raising=False,
    )
    return processor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "reject"),
    [
        (b"", True),
        (b"\n", True),
        (b" \t\r\n" * 4096, True),
        (b" \t\r\n" * 4096 + b"key=value\n", False),
    ],
)
async def test_prepare_rejects_and_cleans_up_a_temporary_empty_file(
    monkeypatch, tmp_path, content, reject
):
    downloaded = tmp_path / "tmp9f3a21"
    downloaded.write_bytes(content)
    resource = LocalResource(
        path=downloaded,
        source_type=SourceType.HTTP,
        original_source="https://example.com/quarterly-report.pdf",
        meta={"original_filename": "quarterly-report.pdf"},
        is_temporary=True,
    )
    processor = _processor(monkeypatch, resource)

    if not reject:
        assert await processor.prepare(resource.original_source) is resource
        assert downloaded.read_bytes() == content
        return

    with pytest.raises(InvalidArgumentError) as excinfo:
        await processor.prepare("https://example.com/quarterly-report.pdf")

    assert "quarterly-report.pdf" in str(excinfo.value)
    assert "tmp9f3a21" not in str(excinfo.value)
    assert not downloaded.exists()
