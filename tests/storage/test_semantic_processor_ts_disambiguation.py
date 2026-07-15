# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _transport_stream() -> bytes:
    packet = b"\x47" + bytes(187)
    return packet * 5


@pytest.mark.asyncio
async def test_typescript_summary_uses_text_path(monkeypatch):
    processor = SemanticProcessor()
    fake_fs = SimpleNamespace(read=AsyncMock(return_value=b"export const answer: number = 42;\n"))
    text_summary = AsyncMock(return_value={"name": "component.ts", "summary": "code"})
    video_summary = AsyncMock(return_value={"name": "component.ts", "summary": "video"})
    monkeypatch.setattr(semantic_processor_module, "get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(processor, "_generate_text_summary", text_summary)
    monkeypatch.setattr(semantic_processor_module, "generate_video_summary", video_summary)

    result = await processor._generate_single_file_summary("viking://resources/repo/component.ts")

    assert result["summary"] == "code"
    text_summary.assert_awaited_once()
    video_summary.assert_not_awaited()
    fake_fs.read.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_stream_summary_keeps_video_path(monkeypatch):
    processor = SemanticProcessor()
    fake_fs = SimpleNamespace(read=AsyncMock(return_value=_transport_stream()))
    text_summary = AsyncMock(return_value={"name": "clip.ts", "summary": "code"})
    video_summary = AsyncMock(return_value={"name": "clip.ts", "summary": "video"})
    monkeypatch.setattr(semantic_processor_module, "get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(processor, "_generate_text_summary", text_summary)
    monkeypatch.setattr(semantic_processor_module, "generate_video_summary", video_summary)

    result = await processor._generate_single_file_summary("viking://resources/video/clip.ts")

    assert result["summary"] == "video"
    video_summary.assert_awaited_once()
    text_summary.assert_not_awaited()
