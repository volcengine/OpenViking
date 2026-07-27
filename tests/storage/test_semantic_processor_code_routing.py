# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.parse.parsers.code.ast import SkeletonExtractionResult
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _config(vlm_available: bool = True):
    config = MagicMock()
    config.output_language_override = ""
    config.semantic.max_file_content_chars = 10000
    config.semantic.max_skeleton_chars = 10000
    config.vlm.is_available.return_value = vlm_available
    config.vlm.get_completion_async = AsyncMock(return_value="LLM summary")
    return config


async def _generate(extraction=None, vlm_available=True):
    config = _config(vlm_available)
    fs = MagicMock()
    fs.read_file = AsyncMock(return_value="def run():\n    return 1\n")
    patches = [
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=config,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fs,
        ),
    ]
    if extraction is not None:
        patches.append(
            patch(
                "openviking.parse.parsers.code.ast.extract_skeleton_result",
                return_value=extraction,
            )
        )

    with patches[0], patches[1]:
        if extraction is None:
            result = await SemanticProcessor()._generate_text_summary(
                "viking://resources/sample.py",
                "sample.py",
                asyncio.Semaphore(1),
            )
        else:
            with patches[2]:
                result = await SemanticProcessor()._generate_text_summary(
                    "viking://resources/sample.py",
                    "sample.py",
                    asyncio.Semaphore(1),
                )
    return result, config


@pytest.mark.asyncio
async def test_useful_skeleton_skips_llm_even_when_vlm_unavailable():
    extraction = SkeletonExtractionResult(
        text="# sample.py [Python]\n\ndef run()",
        provider="process",
        should_fallback_to_llm=False,
        reason="process extraction succeeded",
    )
    result, config = await _generate(extraction, vlm_available=False)
    assert result["summary"] == extraction.text
    config.vlm.get_completion_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_skeleton_calls_llm_fallback():
    extraction = SkeletonExtractionResult(
        text=None,
        provider="llm",
        should_fallback_to_llm=True,
        reason="unsupported",
    )
    result, config = await _generate(extraction)
    assert result["summary"] == "LLM summary"
    config.vlm.get_completion_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_skeleton_without_vlm_returns_empty_summary():
    extraction = SkeletonExtractionResult(
        text=None,
        provider="llm",
        should_fallback_to_llm=True,
        reason="unsupported",
    )
    result, config = await _generate(extraction, vlm_available=False)
    assert result["summary"] == ""
    config.vlm.get_completion_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_typescript_is_dispatched_as_text_not_video():
    processor = SemanticProcessor()
    processor._generate_text_summary = AsyncMock(return_value={"name": "sample.ts", "summary": ""})
    with patch(
        "openviking.storage.queuefs.semantic_processor.generate_video_summary",
        new=AsyncMock(),
    ) as video_summary:
        await processor._generate_single_file_summary("viking://resources/sample.ts")
    processor._generate_text_summary.assert_awaited_once()
    video_summary.assert_not_awaited()
