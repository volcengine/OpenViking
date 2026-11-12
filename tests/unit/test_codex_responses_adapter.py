# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.models.vlm.backends.codex_responses_adapter import (
    CodexAsyncCompletionsAdapter,
    CodexCompletionsAdapter,
)
from openviking.models.vlm.backends.codex_vlm import CodexVLM
from tests.unit.test_codex_vlm import _MockResponsesEventStream


def _stream(terminal, usage, tool_call=False):
    events = [SimpleNamespace(type="response.output_text.delta", delta="partial")]
    if tool_call:
        events.append(
            SimpleNamespace(
                type="response.output_item.done",
                item=SimpleNamespace(
                    type="function_call", call_id="call_1", name="act", arguments="{}"
                ),
            )
        )
    if terminal is not None:
        events.append(
            SimpleNamespace(
                type=terminal,
                response=SimpleNamespace(output=[], usage=usage, status=terminal.split(".")[-1]),
                message="provider error",
            )
        )
    return _MockResponsesEventStream(events)


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", "error", None])
@pytest.mark.parametrize("has_usage", [True, False])
@pytest.mark.parametrize("tool_call", [True, False])
@pytest.mark.parametrize("async_call", [True, False])
async def test_codex_rejects_unsuccessful_stream(terminal, has_usage, tool_call, async_call):
    usage = (
        SimpleNamespace(input_tokens=100, output_tokens=8, total_tokens=108) if has_usage else None
    )
    stream = _stream(terminal, usage, tool_call)
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: stream))
    adapter = CodexCompletionsAdapter(lambda: client, "synthetic-model")

    with pytest.raises(RuntimeError) as caught:
        if async_call:
            await CodexAsyncCompletionsAdapter(adapter).create(messages=[])
        else:
            adapter.create(messages=[])

    assert stream.closed
    error = caught.value
    assert (terminal or "EOF") in str(error)
    if has_usage and terminal is not None:
        assert error.usage.prompt_tokens == 100
        assert error.usage.completion_tokens == 8
        assert error.usage.total_tokens == 108
    else:
        assert error.usage is None


@pytest.mark.parametrize(
    "usage", [None, SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0)]
)
def test_codex_completed_stream_preserves_usage(usage):
    stream = _stream("response.completed", usage)
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: stream))
    result = CodexCompletionsAdapter(lambda: client, "synthetic-model").create(messages=[])
    assert result.choices[0].message.content == "partial"
    assert result.choices[0].finish_reason == "stop"
    assert (result.usage is None) == (usage is None)
    if usage is not None:
        assert result.usage.total_tokens == 0
    assert stream.closed


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", "error", None])
@pytest.mark.parametrize("tools", [None, []])
@pytest.mark.parametrize("async_call", [True, False])
async def test_codex_vlm_rejects_partial_output_and_accounts_usage(terminal, tools, async_call):
    usage = SimpleNamespace(input_tokens=100, output_tokens=8, total_tokens=108)
    stream = _stream(terminal, usage)
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: stream))
    vlm = CodexVLM({"provider": "openai-codex", "model": "synthetic-model", "api_key": "synthetic"})
    with patch.object(vlm, "_build_responses_client", return_value=client):
        with pytest.raises(RuntimeError):
            if async_call:
                await vlm.get_completion_async("hello", tools=tools)
            else:
                vlm.get_completion("hello", tools=tools)
    assert stream.closed
    assert vlm.get_token_usage()["total_usage"]["total_tokens"] == (
        108 if terminal is not None else 0
    )
