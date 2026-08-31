# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise streaming through the OpenAI SDK and the public VLM completion methods."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import AsyncOpenAI, OpenAI

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.base import VLMResponse

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]
USAGE = {
    "prompt_tokens": 12,
    "completion_tokens": 8,
    "total_tokens": 20,
    "prompt_tokens_details": {"cached_tokens": 4},
    "completion_tokens_details": {"reasoning_tokens": 3},
}
TOOL_CALLS = [
    {
        "id": "call_0",
        "type": "function",
        "function": {"name": "remember", "arguments": '{"text":"经验"}'},
    },
    {
        "id": "call_1",
        "type": "function",
        "function": {"name": "remember", "arguments": '{"count":2}'},
    },
]


def _payloads(has_tools, include_usage):
    deltas = [
        {"role": "assistant", "content": None},
        {"content": "Hello", "reasoning_content": "Think"},
        {"content": " world", "reasoning_content": " first"},
    ]
    if has_tools:
        # Parallel tool calls interleave, and names/JSON arguments span chunks.
        deltas.extend(
            [
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_0",
                            "type": "function",
                            "function": {"name": "rem", "arguments": '{"text":'},
                        },
                        {
                            "index": 1,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "remember", "arguments": '{"count":'},
                        },
                    ]
                },
                {
                    "tool_calls": [
                        {"index": 1, "function": {"arguments": "2}"}},
                        {"index": 0, "function": {"name": "ember", "arguments": '"经验"}'}},
                    ]
                },
            ]
        )
    finish = "tool_calls" if has_tools else "stop"
    for delta in deltas:
        yield {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    yield {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    if include_usage:
        yield {"choices": [], "usage": USAGE}


class ResponseBody(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, state, has_tools, fail):
        self.state = state
        self.has_tools = has_tools
        self.fail = fail
        self.closed = 0

    def __iter__(self):
        payloads = self.state.payloads
        if payloads is None:
            payloads = _payloads(self.has_tools, self.state.include_usage)
        for index, payload in enumerate(payloads):
            payload.update(id="test", object="chat.completion.chunk", created=0, model="test-model")
            self.state.clock += 1
            yield ("data: " + json.dumps(payload) + "\n\n").encode()
            if self.fail and index == 3:
                raise httpx.ReadTimeout("stream timeout")
        yield b"data: [DONE]\n\n"

    async def __aiter__(self):
        for data in self:
            yield data
            if self.state.block:
                self.state.started.set()
                await asyncio.Event().wait()

    def close(self):
        self.closed += 1

    async def aclose(self):
        self.close()


@pytest.fixture
async def backend(monkeypatch):
    state = SimpleNamespace(
        requests=[],
        bodies=[],
        include_usage=True,
        payloads=None,
        fail_first=False,
        block=False,
        started=asyncio.Event(),
        clock=0,
    )

    def handle(request):
        body = json.loads(request.content)
        state.requests.append(body)
        if body.get("stream"):
            stream = ResponseBody(
                state,
                bool(body.get("tools")),
                state.fail_first and len(state.requests) == 1,
            )
            state.bodies.append(stream)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if body.get("tools") else "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Hello world",
                            "tool_calls": TOOL_CALLS if body.get("tools") else None,
                        },
                    }
                ],
                "usage": USAGE,
            },
        )

    transport = httpx.MockTransport(handle)
    with OpenAI(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=transport),
    ) as sync_client:
        async with AsyncOpenAI(
            api_key="test-key",
            base_url="https://example.invalid/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        ) as async_client:
            vlm = OpenAIVLM({"api_key": "test-key", "model": "test-model", "max_retries": 0})
            monkeypatch.setattr(vlm, "get_client", lambda: sync_client)
            monkeypatch.setattr(vlm, "get_async_client", lambda: async_client)
            monkeypatch.setattr(vlm, "update_token_usage", Mock())
            monkeypatch.setattr(
                "openviking.models.vlm.backends.openai_vlm.time",
                SimpleNamespace(perf_counter=lambda: state.clock),
            )
            yield vlm, state


async def _complete(vlm, method, tools=None):
    kwargs = {"prompt": "test", "tools": tools}
    if "vision" in method:
        kwargs["images"] = ["https://example.invalid/image.png"]
    result = getattr(vlm, method)(**kwargs)
    return await result if method.endswith("_async") else result


@pytest.mark.parametrize(
    "method",
    [
        "get_completion",
        "get_completion_async",
        "get_vision_completion",
        "get_vision_completion_async",
    ],
)
@pytest.mark.parametrize("tools", [None, [], TOOLS], ids=["text", "empty-tools", "tools"])
@pytest.mark.parametrize("stream", [False, True])
async def test_completion_stream_contract(backend, method, tools, stream):
    vlm, state = backend
    vlm.stream = stream
    result = await _complete(vlm, method, tools)
    request = state.requests[0]
    assert request.get("stream", False) is stream
    assert str(result) == "Hello world"
    assert isinstance(result, VLMResponse) is (tools is not None)
    if tools is not None:
        assert result.finish_reason == ("tool_calls" if tools else "stop")
        assert [(tc.id, tc.name, tc.arguments) for tc in result.tool_calls] == (
            [("call_0", "remember", {"text": "经验"}), ("call_1", "remember", {"count": 2})]
            if tools
            else []
        )
        assert result.usage["total_tokens"] == 20
        assert result.usage["prompt_tokens_details"].cached_tokens == 4
    if stream:
        assert request["stream_options"] == {"include_usage": True}
        assert all(body.closed == 1 for body in state.bodies)
        if tools is not None:
            assert result.reasoning_content == "Think first"
    else:
        assert "stream_options" not in request
    vlm.update_token_usage.assert_called_once_with(
        model_name="test-model",
        provider="openai",
        prompt_tokens=12,
        completion_tokens=8,
        prompt_cached_tokens=4,
        completion_reasoning_tokens=3,
        duration_seconds=state.clock,
    )


@pytest.mark.parametrize("method", ["get_completion", "get_completion_async"])
async def test_stream_without_usage(backend, method):
    vlm, state = backend
    vlm.stream = True
    state.include_usage = False
    result = await _complete(vlm, method, TOOLS)
    assert result.tool_calls[0].arguments == {"text": "经验"}
    assert result.usage == {}
    vlm.update_token_usage.assert_not_called()
    assert state.bodies[0].closed == 1


@pytest.mark.parametrize("method", ["get_completion", "get_completion_async"])
async def test_empty_stream(backend, method):
    vlm, state = backend
    vlm.stream = True
    state.payloads = []
    assert await _complete(vlm, method) == ""
    assert state.bodies[0].closed == 1
    vlm.update_token_usage.assert_not_called()


@pytest.mark.parametrize("method", ["get_completion", "get_completion_async"])
async def test_tool_only_stream_preserves_null_content_and_truncated_arguments(backend, method):
    vlm, state = backend
    vlm.stream = True
    state.payloads = list(_payloads(True, True))
    for payload in state.payloads:
        for choice in payload["choices"]:
            delta = choice["delta"]
            delta.pop("content", None)
            if choice["finish_reason"]:
                choice["finish_reason"] = "length"
            for tool in delta.get("tool_calls", []):
                if tool["index"] == 0 and tool["function"].get("name") == "ember":
                    tool["function"]["arguments"] = ""
    result = await _complete(vlm, method, TOOLS)
    assert result.content is None
    assert result.finish_reason == "length"
    assert result.tool_calls[0].arguments == {"raw": '{"text":'}
    assert result.tool_calls[1].arguments == {"count": 2}
    assert state.bodies[0].closed == 1


@pytest.mark.parametrize("method", ["get_completion", "get_completion_async"])
@pytest.mark.parametrize("override", [False, True])
async def test_raw_stream_override_uses_matching_sdk_parser(backend, method, override):
    vlm, state = backend
    vlm.stream = not override
    vlm.extra_request_body = {"stream": override}
    result = await _complete(vlm, method, TOOLS)
    assert state.requests[0]["stream"] is override
    assert result.tool_calls[0].arguments == {"text": "经验"}
    assert str(result) == "Hello world"
    assert vlm.extra_request_body == {"stream": override}


@pytest.mark.parametrize("method", ["get_completion", "get_completion_async"])
@pytest.mark.parametrize("retries", [0, 1])
async def test_stream_failure_closes_and_discards_partial_result(backend, method, retries):
    vlm, state = backend
    vlm.stream = True
    vlm.max_retries = retries
    state.fail_first = True
    if retries:
        result = await _complete(vlm, method, TOOLS)
        assert str(result) == "Hello world"
        assert len(result.tool_calls) == 2
        assert len(state.requests) == 2
        vlm.update_token_usage.assert_called_once()
    else:
        with pytest.raises(httpx.ReadTimeout, match="stream timeout"):
            await _complete(vlm, method, TOOLS)
        vlm.update_token_usage.assert_not_called()
    assert all(body.closed == 1 for body in state.bodies)


async def test_cancelled_stream_is_closed_without_retry(backend):
    vlm, state = backend
    vlm.stream = True
    vlm.max_retries = 1
    state.block = True
    task = asyncio.create_task(vlm.get_completion_async("test"))
    try:
        await asyncio.wait_for(state.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(state.requests) == 1
        assert state.bodies[0].closed == 1
        vlm.update_token_usage.assert_not_called()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class TestVLMConfigStream:
    """Test VLMConfig passes stream to VLM instance."""

    def test_vlm_config_accepts_stream(self):
        """VLMConfig should accept stream field."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=True,
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "api_base": "https://api.openai.com/v1",
                }
            },
        )

        assert config.stream is True

    def test_vlm_config_stream_defaults_to_false(self):
        """VLMConfig should default stream to False."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        assert config.stream is False

    def test_vlm_config_stream_passed_to_vlm_dict(self):
        """VLMConfig should pass stream to _build_vlm_config_dict."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=True,
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_stream_migrated_to_providers(self):
        """VLMConfig should migrate stream to providers structure."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            api_key="sk-test",
            api_base="https://api.openai.com/v1",
            stream=True,
        )

        # Verify stream is migrated to providers structure
        assert config.providers["openai"]["stream"] is True

        # Verify _build_vlm_config_dict uses the migrated value
        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_stream_in_providers_takes_precedence(self):
        """stream in providers config should take precedence over flat config."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            stream=False,  # flat config is False
            providers={
                "openai": {
                    "api_key": "sk-test",
                    "stream": True,  # provider config is True, should take precedence
                }
            },
        )

        result = config._build_vlm_config_dict()
        assert result["stream"] is True

    def test_vlm_config_max_retries_defaults_to_three(self):
        """VLMConfig should default max_retries to 3."""
        from openviking_cli.utils.config.vlm_config import VLMConfig

        config = VLMConfig(
            model="gpt-4o",
            provider="openai",
            providers={
                "openai": {
                    "api_key": "sk-test",
                }
            },
        )

        assert config.max_retries == 3
        assert config._build_vlm_config_dict()["max_retries"] == 3
