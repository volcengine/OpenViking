"""SSE heartbeats preserve framing, capture, deadlines and cancellation."""

import asyncio
import gzip
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import async_timeout
import orjson
import pytest
from aiohttp import web
from test_app import enable_tools
from test_native_tools import PATHS, native_events, native_response, request_body, wire_response

from openviking_gateway import proxy
from openviking_gateway.protocols import SSEDecoder, heartbeat
from openviking_gateway.tool_loop import HiddenToolLoop
from openviking_gateway.tool_protocols import ResponseCapture
from openviking_gateway.tool_protocols.common import sse

KEEP_ALIVE = b": keep-alive\n\n"


def upstream(source, **headers):
    """The aiohttp response fields the proxy reads, streaming ``source``."""
    return SimpleNamespace(
        headers={"content-type": "text/event-stream", **headers},
        content=SimpleNamespace(iter_any=source),
        close=Mock(),
        status=200,
    )


def passthrough(response):
    """The ProxyRequest fields observe_response reads."""
    return SimpleNamespace(
        response=response,
        capture=ResponseCapture("chat"),
        metrics={},
        relay=AsyncMock(),
        finish=AsyncMock(),
    )


async def test_busy_source_preserves_bytes():
    data = [b'data: {"text":"', b'hello"}\n\n', b"data: [DONE]\n\n"]

    async def source():
        for chunk in data:
            yield chunk

    assert [chunk async for chunk in heartbeat(source(), 1)] == data


async def test_idle_source_keeps_one_task_and_propagates_timeout():
    tasks, output = [], []

    async def source():
        async with async_timeout.timeout(0.06):
            for chunk in (b"first", b"second"):
                tasks.append(asyncio.current_task())
                yield chunk
                await asyncio.sleep(0.02)
            await asyncio.Event().wait()

    with pytest.raises(asyncio.TimeoutError):
        async with async_timeout.timeout(1):
            async for chunk in heartbeat(source(), 0.005):
                output.append(chunk)
    assert [chunk for chunk in output if chunk is not None] == [b"first", b"second"]
    assert None in output
    assert tasks[0] is tasks[1] and tasks[0].done()


@pytest.mark.parametrize("error", [ValueError("upstream failed"), asyncio.CancelledError()])
async def test_source_failure_wakes_consumer_without_waiting_for_heartbeat(error):
    async def source():
        yield b"first"
        await asyncio.sleep(0.01)
        raise error

    async with async_timeout.timeout(1):
        with pytest.raises(type(error)):
            async for _ in heartbeat(source(), 15):
                pass


async def test_deadline_during_backpressure_reaches_source_timeout_context():
    async def source():
        async with async_timeout.timeout(0.03):
            for _ in range(10):
                yield b"chunk"

    stream = heartbeat(source(), 15)
    assert await anext(stream) == b"chunk"
    await asyncio.sleep(0.06)
    async with async_timeout.timeout(1):
        with pytest.raises(asyncio.TimeoutError):
            async for _ in stream:
                pass


@pytest.mark.parametrize("backpressure", [False, True])
async def test_closing_consumer_cancels_and_closes_source(backpressure):
    advanced, closed, tasks = [], [], []

    async def source():
        tasks.append(asyncio.current_task())
        try:
            async with async_timeout.timeout(1):
                for number in range(100):
                    advanced.append(number)
                    yield b"chunk"
                    if not backpressure:
                        await asyncio.Event().wait()
        finally:
            closed.append(asyncio.current_task())

    stream = heartbeat(source(), 0.005)
    assert await anext(stream) == b"chunk"
    await asyncio.sleep(0.02)
    assert len(advanced) <= 3
    async with async_timeout.timeout(1):
        await stream.aclose()
    assert closed == tasks and tasks[0].done()


@pytest.mark.parametrize("encoding", [None, "gzip"])
@pytest.mark.parametrize("split", [False, True])
async def test_passthrough_heartbeats_only_at_uncompressed_event_boundaries(
    monkeypatch, encoding, split
):
    monkeypatch.setattr(proxy, "HEARTBEAT_SECONDS", 0.005)
    first = sse({"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]})
    last = sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    original = first + last
    data = gzip.compress(original) if encoding else original
    cut = 8 if split else len(first)

    async def source():
        yield data[:cut]
        await asyncio.sleep(0.03)
        yield data[cut:]

    headers = {"content-length": str(len(data))}
    if encoding:
        headers["content-encoding"] = encoding
    response = upstream(source, **headers)
    request = passthrough(response)
    outgoing = await proxy.ProxyRequest.observe_response(request)
    chunks = [chunk async for chunk in outgoing.body_iterator]
    assert (KEEP_ALIVE in chunks) == (not split and not encoding)
    assert b"".join(chunk for chunk in chunks if chunk != KEEP_ALIVE) == data
    assert "content-length" not in outgoing.headers
    output = b"".join(chunks)
    decoded = [
        value
        for frame in SSEDecoder().feed(gzip.decompress(output) if encoding else output)
        if (value := SSEDecoder.data(frame))
    ]
    assert decoded == [SSEDecoder.data(frame) for frame in SSEDecoder().feed(original)]
    assert response.close.called
    if not encoding:
        assert request.capture.complete
        assert request.capture.message["content"] == "hello"


@pytest.mark.parametrize("asgi_disconnect", [False, True])
async def test_disconnect_closes_passthrough_response_and_pump(monkeypatch, asgi_disconnect):
    monkeypatch.setattr(proxy, "HEARTBEAT_SECONDS", 0.005)
    closed, tasks = [], []

    async def source():
        tasks.append(asyncio.current_task())
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            closed.append(True)

    response = upstream(source)
    request = passthrough(response)
    outgoing = await proxy.ProxyRequest.observe_response(request)
    if asgi_disconnect:
        disconnected = asyncio.Event()

        async def send(message):
            if message.get("body") == KEEP_ALIVE:
                disconnected.set()

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        await outgoing({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    else:
        assert await anext(outgoing.body_iterator) == KEEP_ALIVE
        await outgoing.body_iterator.aclose()
    assert closed and tasks[0].done() and response.close.called
    assert not request.capture.complete


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
async def test_disconnect_closes_hidden_loop_response_and_its_timeout_context(protocol):
    tasks = []

    async def source():
        tasks.append(asyncio.current_task())
        yield sse(native_events(protocol, native_response(protocol, 1))[0])
        await asyncio.Event().wait()

    response = upstream(source)
    prepared = SimpleNamespace(
        body=request_body(protocol, True),
        protocol=protocol,
        root={"policy": {"tool_total_seconds": 1}},
        metrics={},
        tools_active=True,
        reply_lead="",
    )
    loop = HiddenToolLoop(
        prepared, SimpleNamespace(allowed={"openviking_search"}), None, ResponseCapture(protocol)
    )
    stream = heartbeat(loop.run(response, None), 0.005)
    assert await anext(stream)
    await stream.aclose()
    assert response.close.called and tasks[0].done() and not loop.capture.complete


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
@pytest.mark.parametrize("timeout", [False, True])
async def test_hidden_tools_heartbeat_and_total_deadline(
    running_gateway, monkeypatch, protocol, timeout
):
    monkeypatch.setattr(proxy, "HEARTBEAT_SECONDS", 0.005)
    app, client, admin, key, _, _ = running_gateway
    await enable_tools(
        client, admin, show_tool_calls=False, tool_total_seconds=0.05 if timeout else None
    )
    calls, captures = [], []
    original_finish = proxy.ProxyRequest.finish

    async def finish(request):
        captures.append(request.capture)
        await original_finish(request)

    monkeypatch.setattr(proxy.ProxyRequest, "finish", finish)

    async def backend(request):
        if request.path == "/mcp":
            await asyncio.sleep(0.1)
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path == PATHS[protocol]:
            calls.append(await request.json())
            value = native_response(protocol, len(calls), owned=len(calls) == 1)
            return await wire_response(request, protocol, value, True)
        return None

    app.state.test_backend["handler"] = backend
    async with async_timeout.timeout(2):
        response = await client.post(
            PATHS[protocol],
            headers={"Authorization": "Bearer " + key["key"]},
            json=request_body(protocol, True),
        )
    assert response.status_code == 200
    assert KEEP_ALIVE in response.content
    events = [
        value for frame in SSEDecoder().feed(response.content) if (value := SSEDecoder.data(frame))
    ]
    assert len(calls) == (1 if timeout else 2)
    assert captures[-1].complete == (not timeout)
    if timeout:
        assert b"Hidden tool request timed out" in response.content
        assert events[-1]["type"] == ("response.failed" if protocol == "responses" else "error")
    else:
        assert b"answer-2" in orjson.dumps(captures[-1].output)
        assert events[-1]["type"] == (
            "response.completed" if protocol == "responses" else "message_stop"
        )
        assert captures[-1].usage["output_tokens"] == 40


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
async def test_gateway_passthrough_preserves_events_and_capture(
    running_gateway, monkeypatch, protocol
):
    monkeypatch.setattr(proxy, "HEARTBEAT_SECONDS", 0.005)
    app, client, _, key, _, _ = running_gateway
    expected = native_events(protocol, native_response(protocol, 1))
    captures = []
    original_finish = proxy.ProxyRequest.finish

    async def finish(request):
        captures.append(request.capture)
        await original_finish(request)

    monkeypatch.setattr(proxy.ProxyRequest, "finish", finish)

    async def backend(request):
        if request.path != PATHS[protocol]:
            return None
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(sse(expected[0]))
        await asyncio.sleep(0.03)
        await response.write(b"".join(sse(event) for event in expected[1:]))
        return response

    app.state.test_backend["handler"] = backend
    response = await client.post(
        PATHS[protocol],
        headers={"Authorization": "Bearer " + key["key"]},
        json=request_body(protocol, True),
    )
    assert KEEP_ALIVE in response.content
    actual = [
        value for frame in SSEDecoder().feed(response.content) if (value := SSEDecoder.data(frame))
    ]
    assert actual == expected
    assert captures[-1].complete and captures[-1].usage["output_tokens"] == 20
