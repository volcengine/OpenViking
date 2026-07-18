from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


@pytest.mark.asyncio
async def test_repository_typescript_uses_text_summary(monkeypatch):
    processor = SemanticProcessor.__new__(SemanticProcessor)
    processor.max_concurrent_llm = 1
    processor._generate_text_summary = AsyncMock(return_value={"kind": "text"})
    video_summary = AsyncMock(return_value={"kind": "video"})
    monkeypatch.setattr(semantic_processor_module, "generate_video_summary", video_summary)

    result = await processor._generate_single_file_summary(
        "viking://resources/org/repo/src/example.ts"
    )

    assert result == {"kind": "text"}
    processor._generate_text_summary.assert_awaited_once()
    video_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_mpeg_ts_resource_uses_video_summary(monkeypatch):
    processor = SemanticProcessor.__new__(SemanticProcessor)
    processor.max_concurrent_llm = 1
    processor._generate_text_summary = AsyncMock(return_value={"kind": "text"})
    video_summary = AsyncMock(return_value={"kind": "video"})
    monkeypatch.setattr(semantic_processor_module, "generate_video_summary", video_summary)

    result = await processor._generate_single_file_summary(
        "viking://resources/video/2026/07/19/example.ts"
    )

    assert result == {"kind": "video"}
    video_summary.assert_awaited_once()
    processor._generate_text_summary.assert_not_awaited()
