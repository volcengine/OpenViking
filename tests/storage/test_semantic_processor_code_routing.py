# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.parse.parsers.code.ast import SkeletonExtractionResult
from openviking.parse.parsers.constants import (
    FILE_TYPE_CODE,
    FILE_TYPE_DOCUMENTATION,
    FILE_TYPE_OTHER,
)
from openviking.parse.parsers.media.utils import get_media_type
from openviking.prompts import render_prompt
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _config(vlm_available: bool = True):
    config = MagicMock()
    config.output_language_override = ""
    config.semantic.max_file_content_chars = 10000
    config.semantic.max_skeleton_chars = 10000
    config.vlm.is_available.return_value = vlm_available
    config.vlm.get_completion_async = AsyncMock(return_value="LLM summary")
    return config


class _TestVLMResolver:
    def __init__(self, vlm):
        self._vlm = vlm

    async def get_vlm(self, account_id):
        del account_id
        return self._vlm


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
            result = await SemanticProcessor(
                vlm_resolver=_TestVLMResolver(config.vlm)
            )._generate_text_summary(
                "viking://resources/sample.py",
                "sample.py",
                asyncio.Semaphore(1),
            )
        else:
            with patches[2]:
                result = await SemanticProcessor(
                    vlm_resolver=_TestVLMResolver(config.vlm)
                )._generate_text_summary(
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
async def test_live_context_uses_dedicated_summary_prompt():
    content = """---
type: OpenViking Live Context
title: Sum e and pi
description: Calculate the sum of e and pi.
x-openviking:
  schema_version: v1
  type: mcp
  definition: viking://agent/tools/demo/get-sum.json
  args:
    a: 3.14
    b: 2.718
---
"""
    config = _config()
    fs = MagicMock()
    fs.read_file = AsyncMock(return_value=content)

    with (
        patch(
            "openviking.storage.queuefs.semantic_processor.get_openviking_config",
            return_value=config,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.get_viking_fs",
            return_value=fs,
        ),
        patch(
            "openviking.storage.queuefs.semantic_processor.render_prompt",
            return_value="live context prompt",
        ) as render,
    ):
        result = await SemanticProcessor(
            vlm_resolver=_TestVLMResolver(config.vlm)
        )._generate_text_summary(
            "viking://resources/sum.md",
            "sum.md",
            asyncio.Semaphore(1),
        )

    assert result["summary"] == "LLM summary"
    prompt_id, variables = render.call_args.args
    assert prompt_id == "semantic.live_context_summary"
    assert variables["title"] == "Sum e and pi"
    assert variables["description"] == "Calculate the sum of e and pi."
    assert variables["tool_uri"] == "viking://agent/tools/demo/get-sum.json"
    assert variables["arguments"] == '{"a": 3.14, "b": 2.718}'


def test_live_context_summary_prompt_preserves_agent_invocation_details():
    prompt = render_prompt(
        "semantic.live_context_summary",
        {
            "file_name": "sum.md",
            "title": "Calculate e plus pi",
            "description": "Adds two mounted constants.",
            "tool_uri": "viking://agent/tools/demo/get-sum.json",
            "arguments": '{"a": 3.14, "b": 2.718}',
            "output_language": "en",
        },
    )

    assert "viking://agent/tools/demo/get-sum.json" in prompt
    assert '{"a": 3.14, "b": 2.718}' in prompt
    assert "Calculate e plus pi" in prompt
    assert "Adds two mounted constants." in prompt
    assert "inputSchema" in prompt
    assert "returns the latest result" in prompt
    assert "must not invoke the MCP Tool again with the same arguments" in prompt
    assert "only when the request requires different valid arguments" in prompt


def test_directory_overview_prompt_preserves_live_context_invocation_guidance():
    prompt = render_prompt(
        "semantic.overview_generation",
        {
            "dir_name": "arrange",
            "file_summaries": "- sum.md: live context summary",
            "children_abstracts": "",
            "output_language": "en",
            "directory_coverage": "1 direct entry; all represented.",
        },
    )

    assert "exact MCP Tool URI" in prompt
    assert "mounted arguments" in prompt
    assert "inputSchema" in prompt
    assert "returns the latest result" in prompt
    assert "must not invoke the Tool again with the same arguments" in prompt
    assert "only when different valid arguments are required" in prompt


def test_cuda_extensions_are_dispatched_as_code():
    processor = SemanticProcessor()
    assert processor._detect_file_type("kernel.cu") == FILE_TYPE_CODE
    assert processor._detect_file_type("common.cuh") == FILE_TYPE_CODE


def test_documentation_extensions_are_dispatched_before_code_skeleton_support():
    processor = SemanticProcessor()
    with patch(
        "openviking.parse.parsers.code.ast.providers.supports_code_skeleton",
        return_value=True,
    ):
        assert processor._detect_file_type("README.md") == FILE_TYPE_DOCUMENTATION


def test_process_supported_extensions_are_dispatched_as_code():
    processor = SemanticProcessor()
    assert processor._detect_file_type("main.tf") == FILE_TYPE_CODE


def test_process_denied_non_code_formats_are_not_dispatched_as_code():
    processor = SemanticProcessor()
    assert processor._detect_file_type("request.http") == FILE_TYPE_OTHER


def test_legacy_code_extensions_remain_fallback_when_skeleton_is_unsupported():
    processor = SemanticProcessor()
    with patch(
        "openviking.parse.parsers.code.ast.providers.supports_code_skeleton",
        return_value=False,
    ):
        assert processor._detect_file_type("sample.py") == FILE_TYPE_CODE


def test_plain_ts_extension_is_not_dispatched_as_video():
    assert get_media_type("viking://resources/sample.ts", None) is None
