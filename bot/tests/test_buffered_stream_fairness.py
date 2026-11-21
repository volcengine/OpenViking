"""Real SDK iteration must yield while draining a buffered SSE response."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from vikingbot.providers.vlm_adapter import VLMProviderAdapter
from volcenginesdkarkruntime import AsyncArk

from openviking.models.vlm import OpenAIVLM, VolcEngineVLM


def frames(count, kind):
    for _ in range(count):
        delta = {"content": "x"} if kind == "content" else {}
        choices = [{"index": 0, "delta": delta, "finish_reason": None}]
        yield {"choices": choices if kind != "empty" else []}
    yield {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    yield {
        "choices": [],
        "usage": {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17},
    }


@pytest.mark.parametrize(
    "backend,client_type", [(OpenAIVLM, AsyncOpenAI), (VolcEngineVLM, AsyncArk)]
)
@pytest.mark.parametrize("kind", ["content", "empty", "no_delta"])
@pytest.mark.parametrize("count", [128, 512])
async def test_buffered_frames_allow_an_already_ready_callback(backend, client_type, kind, count):
    parsed = 0
    observed = []
    calls = []
    closed = []

    class Buffered(httpx.AsyncByteStream):
        async def __aiter__(self):
            asyncio.get_running_loop().call_soon(lambda: observed.append(parsed))
            yield b"".join(
                ("data: " + json.dumps(f) + "\n\n").encode() for f in frames(count, kind)
            )
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.append(True)

    def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Buffered())

    vlm = backend({"model": "synthetic", "api_key": "synthetic", "max_retries": 0})
    adapter = VLMProviderAdapter(vlm, "synthetic", langfuse_client=SimpleNamespace(enabled=False))
    parse = adapter._parse_usage

    def parse_usage(value):
        nonlocal parsed
        parsed += 1
        return parse(value)

    adapter._parse_usage = parse_usage
    async with client_type(
        api_key="synthetic",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        vlm.get_async_client = lambda: client
        events = [
            event
            async for event in adapter.chat_stream(messages=[{"role": "user", "content": "hello"}])
        ]
    await asyncio.sleep(0)
    assert observed and observed[0] <= 32
    assert parsed == count + 2
    assert len(calls) == 1 and closed
    assert events[-1].response.content == ("x" * count if kind == "content" else None)
    assert events[-1].response.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "total_tokens": 17,
    }


@pytest.mark.parametrize(
    "backend,client_type", [(OpenAIVLM, AsyncOpenAI), (VolcEngineVLM, AsyncArk)]
)
async def test_cancellation_during_buffered_frames_closes_without_retry(backend, client_type):
    calls = []
    closed = []
    consumer = None

    class Buffered(httpx.AsyncByteStream):
        async def __aiter__(self):
            asyncio.get_running_loop().call_soon(consumer.cancel)
            yield b"".join(
                ("data: " + json.dumps(f) + "\n\n").encode() for f in frames(512, "content")
            )
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.append(True)

    def respond(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Buffered())

    vlm = backend({"model": "synthetic", "api_key": "synthetic", "max_retries": 2})
    adapter = VLMProviderAdapter(vlm, "synthetic", langfuse_client=SimpleNamespace(enabled=False))
    async with client_type(
        api_key="synthetic",
        base_url="https://example.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as client:
        vlm.get_async_client = lambda: client

        async def consume():
            return [
                event
                async for event in adapter.chat_stream(
                    messages=[{"role": "user", "content": "hello"}]
                )
            ]

        consumer = asyncio.create_task(consume())
        with pytest.raises(asyncio.CancelledError):
            await consumer
    assert len(calls) == 1 and closed
    assert vlm.get_token_usage().get("prompt_tokens", 0) == 0
