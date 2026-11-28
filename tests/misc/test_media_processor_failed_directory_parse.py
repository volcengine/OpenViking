# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A failed directory parse must not be reported as parsed output.

MediaProcessor used to attach the local source directory to a failed
ParseResult.temp_dir_path. Downstream treated that as usable parse output,
dropped the real failure, and skipped cleanup of the temporary source.
"""

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.base import NodeType, ResourceNode, create_parse_result
from openviking.parse.parsers.directory import DirectoryParser
from openviking.utils.media_processor import UnifiedResourceProcessor


@pytest.mark.asyncio
async def test_failed_directory_parse_keeps_failure_and_cleans_source(tmp_path, monkeypatch):
    source_dir = tmp_path / "downloaded"
    source_dir.mkdir()
    (source_dir / "payload.bin").write_bytes(b"not a directory payload")

    failure = create_parse_result(
        root=ResourceNode(type=NodeType.ROOT),
        warnings=["Failed to parse directory: boom"],
    )

    async def fail_parse(self, *args, **kwargs):
        return failure

    monkeypatch.setattr(DirectoryParser, "parse", fail_parse)

    resource = LocalResource(
        path=source_dir,
        source_type=SourceType.LOCAL,
        original_source=str(source_dir),
        is_temporary=True,
    )
    processor = UnifiedResourceProcessor(vlm_processor=object())
    result = await processor.process(str(source_dir), prepared_resource=resource)

    assert result is failure
    assert result.temp_dir_path is None
    assert resource.is_temporary is True
    assert not source_dir.exists()
