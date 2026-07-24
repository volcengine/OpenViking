# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from openviking.session.memory.extract_loop import ExtractLoop
from openviking_cli.exceptions import ResourceExhaustedError
from openviking_cli.utils.config.memory_config import MemoryConfig


def _config(max_tokens: int):
    return SimpleNamespace(memory=SimpleNamespace(extraction_request_max_tokens=max_tokens))


def test_memory_config_has_a_positive_default_extraction_request_budget():
    assert MemoryConfig().extraction_request_max_tokens == 32768
    with pytest.raises(ValidationError):
        MemoryConfig(extraction_request_max_tokens=0)


@pytest.mark.asyncio
async def test_call_llm_rejects_oversized_full_request_before_provider_call():
    vlm = Mock(model="test-model")
    vlm.get_completion_async = AsyncMock()
    loop = ExtractLoop(vlm=vlm, viking_fs=Mock(), context_provider=Mock())
    loop._tool_schemas = [{"type": "function", "function": {"name": "read"}}]

    # ASCII text is conservatively estimated at one token per four characters.
    # The content alone contributes 42,336 tokens before message/tool overhead.
    messages = [{"role": "user", "content": "x" * 169344}]

    with (
        patch(
            "openviking.session.memory.extract_loop.get_openviking_config",
            return_value=_config(32768),
        ),
        pytest.raises(ResourceExhaustedError) as exc_info,
    ):
        await loop._call_llm(messages)

    assert exc_info.value.code == "RESOURCE_EXHAUSTED"
    assert exc_info.value.details["estimated_tokens"] > 42336
    assert exc_info.value.details["max_tokens"] == 32768
    assert exc_info.value.details["config_key"] == "memory.extraction_request_max_tokens"
    vlm.get_completion_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_llm_counts_tool_schema_in_request_budget():
    vlm = Mock(model="test-model")
    vlm.get_completion_async = AsyncMock()
    loop = ExtractLoop(vlm=vlm, viking_fs=Mock(), context_provider=Mock())
    loop._tool_schemas = [
        {
            "type": "function",
            "function": {"name": "read", "description": "x" * 400},
        }
    ]

    with (
        patch(
            "openviking.session.memory.extract_loop.get_openviking_config",
            return_value=_config(80),
        ),
        pytest.raises(ResourceExhaustedError),
    ):
        await loop._call_llm([{"role": "user", "content": "short"}])

    vlm.get_completion_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_llm_excludes_disabled_tools_from_request_budget():
    vlm = Mock(model="test-model")
    vlm.get_completion_async = AsyncMock(return_value=None)
    loop = ExtractLoop(vlm=vlm, viking_fs=Mock(), context_provider=Mock())
    loop._tool_schemas = [
        {
            "type": "function",
            "function": {"name": "read", "description": "x" * 400},
        }
    ]
    loop._disable_tools_for_iteration = True

    with patch(
        "openviking.session.memory.extract_loop.get_openviking_config",
        return_value=_config(80),
    ):
        result = await loop._call_llm([{"role": "user", "content": "short"}])

    assert result == (None, None)
    vlm.get_completion_async.assert_awaited_once_with(
        messages=[{"role": "user", "content": "short"}],
        tools=None,
        tool_choice=None,
        thinking=False,
    )


@pytest.mark.asyncio
async def test_call_llm_allows_request_within_budget():
    vlm = Mock(model="test-model")
    vlm.get_completion_async = AsyncMock(return_value=None)
    loop = ExtractLoop(vlm=vlm, viking_fs=Mock(), context_provider=Mock())
    loop._tool_schemas = []

    with patch(
        "openviking.session.memory.extract_loop.get_openviking_config",
        return_value=_config(1000),
    ):
        result = await loop._call_llm([{"role": "user", "content": "short"}])

    assert result == (None, None)
    vlm.get_completion_async.assert_awaited_once()
