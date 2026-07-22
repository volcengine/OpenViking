# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VolcEngineVLM cache logic."""

import inspect
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.models.vlm.backends.volcengine_vlm import VolcEngineVLM as VLMClass


def test_async_completion_tracer_ignores_function_arguments() -> None:
    closure = inspect.getclosurevars(VLMClass.get_completion_async)
    trace_decorator = closure.nonlocals["self"]

    assert trace_decorator.ignore_args is True


def test_build_vlm_response_traces_text_when_tools_are_enabled() -> None:
    vlm = object.__new__(VLMClass)
    message = SimpleNamespace(content="raw final response", tool_calls=None)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
    )

    with patch("openviking.models.vlm.backends.volcengine_vlm.tracer.info") as trace_info:
        result = vlm._build_vlm_response(response, has_tools=True)

    assert result.content == "raw final response"
    trace_info.assert_called_once_with("message.content=raw final response")


def test_build_vlm_response_traces_tool_calls_when_tools_are_enabled() -> None:
    vlm = object.__new__(VLMClass)
    tool_calls = [
        SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="read", arguments='{"path": "/tmp/data"}'),
        )
    ]
    message = SimpleNamespace(content=None, tool_calls=tool_calls)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
    )

    with patch("openviking.models.vlm.backends.volcengine_vlm.tracer.info") as trace_info:
        result = vlm._build_vlm_response(response, has_tools=True)

    assert result.tool_calls[0].name == "read"
    trace_info.assert_called_once_with(f"message.tool_calls={tool_calls}")


@pytest.mark.asyncio
async def test_async_completion_traces_readable_request_without_mutating_sdk_messages() -> None:
    vlm = VLMClass(
        {
            "model": "test-model",
            "api_key": "test-key",
            "max_retries": 0,
        }
    )
    messages = [
        {"role": "system", "content": "Follow the rules."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hello."},
                {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
            ],
        },
    ]
    original_messages = deepcopy(messages)
    tools = [
        {
            "type": "function",
            "function": {"name": "read", "parameters": {"type": "object"}},
        }
    ]
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="done", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response)),
        )
    )

    with (
        patch.object(vlm, "get_async_client", return_value=client),
        patch.object(vlm, "_update_token_usage_from_response"),
        patch("openviking.models.vlm.backends.volcengine_vlm.tracer.info") as trace_info,
    ):
        result = await vlm.get_completion_async(messages=messages, tools=tools)

    assert result.content == "done"
    sent_messages = client.chat.completions.create.await_args.kwargs["messages"]
    assert sent_messages is messages
    assert sent_messages == original_messages
    trace_info.assert_any_call(
        "request: === Messages ===\n\n"
        "[system]\nFollow the rules.\n\n"
        "[user]\n[\n"
        "  {\n"
        '    "type": "text",\n'
        '    "text": "Hello."\n'
        "  },\n"
        "  {\n"
        '    "type": "image_url",\n'
        '    "image_url": {\n'
        '      "url": "https://example.com/a.png"\n'
        "    }\n"
        "  }\n"
        "]\n\n"
        "=== End Messages ==="
    )
    trace_info.assert_any_call('tools: ["read"]')
