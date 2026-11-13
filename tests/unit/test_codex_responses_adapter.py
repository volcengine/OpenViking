# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
import pytest

from openviking.models.model_error import ModelErrorKind, model_error_kind
from openviking.models.vlm.backends.codex_responses_adapter import (
    CodexAsyncCompletionsAdapter,
    CodexCompletionsAdapter,
    CodexResponseError,
)
from openviking.models.vlm.backends.codex_vlm import CodexVLM
from openviking.models.vlm.retry_vlm import RetryVLM
from openviking.utils.model_retry import extract_metric_error_code
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

    with pytest.raises(CodexResponseError) as caught:
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
        with pytest.raises(CodexResponseError):
            if async_call:
                await vlm.get_completion_async("hello", tools=tools)
            else:
                vlm.get_completion("hello", tools=tools)
    assert stream.closed
    assert vlm.get_token_usage()["total_usage"]["total_tokens"] == (
        108 if terminal is not None else 0
    )


def _sse(*events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"


@pytest.mark.parametrize(
    ("provider_code", "expected_kind", "should_retry"),
    [
        ("rate_limit_exceeded", ModelErrorKind.RATE_LIMIT_ERROR, True),
        ("server_error", ModelErrorKind.TRANSIENT_SERVICE_ERROR, True),
        ("insufficient_quota", ModelErrorKind.QUOTA_EXHAUSTED, False),
    ],
)
@pytest.mark.parametrize("tools", [None, []])
@pytest.mark.parametrize("async_call", [False, True])
async def test_codex_failed_sse_crosses_retry_boundary(
    provider_code, expected_kind, should_retry, tools, async_call
):
    """Use the real OpenAI SSE decoder and public RetryVLM, not a mock adapter."""
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            response = {
                "id": "resp_failed",
                "object": "response",
                "created_at": 0,
                "model": "synthetic-model",
                "status": "failed",
                "output": [],
                "error": {"code": provider_code, "message": "synthetic provider error"},
                "usage": {"input_tokens": 100, "output_tokens": 8, "total_tokens": 108},
            }
            body = _sse(
                {"type": "response.output_text.delta", "delta": "do not return partial"},
                {"type": "response.failed", "response": response},
            )
        else:
            response = {
                "id": "resp_completed",
                "object": "response",
                "created_at": 0,
                "model": "synthetic-model",
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18},
            }
            body = _sse(
                {"type": "response.output_text.delta", "delta": "recovered"},
                {"type": "response.completed", "response": response},
            )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    client = openai.OpenAI(
        api_key="synthetic",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    inner = CodexVLM(
        {
            "provider": "openai-codex",
            "model": "synthetic-model",
            "api_key": "synthetic",
            "max_retries": 1,
        }
    )
    vlm = RetryVLM(inner)

    with (
        patch.object(inner, "_build_responses_client", return_value=client),
        patch("openviking.utils.model_retry.time.sleep"),
        patch("openviking.utils.model_retry.asyncio.sleep"),
    ):
        if should_retry:
            if async_call:
                result = await vlm.get_completion_async("hello", tools=tools)
            else:
                result = vlm.get_completion("hello", tools=tools)
            assert (result if tools is None else result.content) == "recovered"
        else:
            with pytest.raises(CodexResponseError) as caught:
                if async_call:
                    await vlm.get_completion_async("hello", tools=tools)
                else:
                    vlm.get_completion("hello", tools=tools)
            error = caught.value
            assert model_error_kind(error) == expected_kind
            assert error.code == provider_code
            assert "synthetic provider error" in str(error)
            assert extract_metric_error_code(error) == provider_code
            assert error.usage.total_tokens == 108

    assert len(requests) == (2 if should_retry else 1)
    assert vlm.get_token_usage()["total_usage"]["total_tokens"] == (126 if should_retry else 108)


def test_codex_error_event_preserves_provider_code_and_message():
    stream = _MockResponsesEventStream(
        [SimpleNamespace(type="error", code="insufficient_quota", message="out of balance")]
    )
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: stream))
    with pytest.raises(CodexResponseError) as caught:
        CodexCompletionsAdapter(lambda: client, "synthetic-model").create(messages=[])
    assert caught.value.kind == ModelErrorKind.QUOTA_EXHAUSTED
    assert extract_metric_error_code(caught.value) == "insufficient_quota"
    assert "out of balance" in str(caught.value)


@pytest.mark.parametrize(
    ("code", "expected_kind"),
    [
        ("rate_limit_exceeded", ModelErrorKind.RATE_LIMIT_ERROR),
        ("server_error", ModelErrorKind.TRANSIENT_SERVICE_ERROR),
        ("insufficient_quota", ModelErrorKind.QUOTA_EXHAUSTED),
    ],
)
def test_codex_response_failure_preserves_structured_error(code, expected_kind):
    response = SimpleNamespace(
        status="failed",
        output=[],
        usage=SimpleNamespace(input_tokens=100, output_tokens=8, total_tokens=108),
        error=SimpleNamespace(code=code, message="provider failure details"),
    )
    stream = _MockResponsesEventStream([SimpleNamespace(type="response.failed", response=response)])
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: stream))
    with pytest.raises(CodexResponseError) as caught:
        CodexCompletionsAdapter(lambda: client, "synthetic-model").create(messages=[])
    error = caught.value
    assert model_error_kind(error) == expected_kind
    assert error.provider == "openai-codex"
    assert error.code == extract_metric_error_code(error) == code
    assert "provider failure details" in str(error)
    assert error.usage.total_tokens == 108
