"""SSE heartbeats preserve framing, capture, deadlines and cancellation."""

import asyncio
import gzip
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import async_timeout
import orjson
import pytest
from aiohttp import web
from starlette.requests import ClientDisconnect
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
    record = proxy.RequestRecord(None, None)
    return SimpleNamespace(
        response=response,
        capture=ResponseCapture("chat"),
        record=record,
        metrics=record.metrics,
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


def hidden_tool_proxy(monkeypatch, tasks):
    response = SimpleNamespace(headers={}, status=200, close=Mock())
    prepared = SimpleNamespace(metrics={})
    capture = ResponseCapture("responses")
    capture.complete = True
    source_closed = []

    class Loop:
        async def run(self, response, send):
            tasks.append(asyncio.current_task())
            try:
                yield b"data: event\n\n"
                await asyncio.Event().wait()
            finally:
                source_closed.append(True)

    monkeypatch.setattr(proxy, "ToolExecutor", lambda *args: object())
    monkeypatch.setattr(proxy, "HiddenToolLoop", lambda *args: Loop())
    request = proxy.ProxyRequest.__new__(proxy.ProxyRequest)
    request.app = SimpleNamespace(
        state=SimpleNamespace(viking=object(), kernel=SimpleNamespace(capture=None))
    )
    request.store = object()
    request.prepared = prepared
    request.credential = {}
    request.config = SimpleNamespace(
        public_url="http://gateway", max_body_bytes=1, mcp_file_links="proxy"
    )
    request.path = "/v1/responses"
    request.body = {"stream": True, "store": False}
    request.response = response
    request.capture = capture
    request.record = proxy.RequestRecord(request.app, None)
    request.metrics = request.record.metrics
    request.target = "https://upstream/v1/responses"
    request.headers = {}
    request.relay = AsyncMock()
    request.finish = AsyncMock()
    return request, response, source_closed


@pytest.mark.parametrize("failure_stage", ["start", "body"])
async def test_hidden_tool_stream_closes_upstream_when_send_fails(monkeypatch, failure_stage):
    tasks = []
    request, response, source_closed = hidden_tool_proxy(monkeypatch, tasks)
    outgoing = await request.tool_response()

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == f"http.response.{failure_stage}" and (
            failure_stage == "start" or message.get("body")
        ):
            raise OSError("client disconnected")

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
    with pytest.raises(ClientDisconnect):
        await outgoing(scope, receive, send)

    assert response.close.called
    assert request.finish.await_count == 1
    assert not request.capture.complete
    if failure_stage == "body":
        assert tasks[0].done()
        assert source_closed == [True]
    else:
        assert not tasks
        assert not source_closed


async def test_hidden_tool_stream_closes_upstream_on_asgi_disconnect(monkeypatch):
    tasks = []
    request, response, source_closed = hidden_tool_proxy(monkeypatch, tasks)
    outgoing = await request.tool_response()
    body_started = asyncio.Event()

    async def receive():
        await body_started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            body_started.set()
            await asyncio.Event().wait()

    scope = {"type": "http", "asgi": {"spec_version": "2.3"}}
    await outgoing(scope, receive, send)

    assert response.close.called
    assert request.finish.await_count == 1
    assert not request.capture.complete
    assert tasks[0].done()
    assert source_closed == [True]


def first_event(protocol):
    """The opening event of a native stream, as the provider frames it."""
    if protocol == "chat":
        return sse({"id": "chatcmpl-1", "choices": [{"delta": {"content": "hel"}, "index": 0}]})
    event = native_events(protocol, native_response(protocol, 1))[0]
    return b"event: " + event["type"].encode() + b"\n" + sse(event)


def failing_passthrough(protocol, data, error):
    """A relayed stream that sends ``data`` and then fails with ``error``."""

    async def source():
        for chunk in data:
            yield chunk
        raise error

    response = upstream(source)
    request = passthrough(response)
    request.capture = ResponseCapture(protocol)
    request.protocol, request.body = protocol, request_body(protocol, True)
    return request, response


@pytest.mark.parametrize("protocol", ["anthropic", "chat", "responses"])
async def test_passthrough_stream_failure_emits_protocol_error(protocol):
    first = first_event(protocol)
    request, response = failing_passthrough(protocol, [first], aiohttp.ServerTimeoutError())
    outgoing = await proxy.ProxyRequest.observe_response(request)
    output = b"".join([chunk async for chunk in outgoing.body_iterator])
    assert output.startswith(first)
    added = output[len(first) :]
    message = "upstream_timeout: the model provider stopped before the reply finished"
    if protocol == "chat":
        error = {"error": {"type": "upstream_timeout", "message": message}}
        assert added == sse(error) + b"data: [DONE]\n\n"
    else:
        (frame,) = SSEDecoder().feed(added)
        event = SSEDecoder.data(frame)
        if protocol == "anthropic":
            assert frame.startswith(b"event: error\n")
            assert event == {
                "type": "error",
                "error": {"type": "upstream_timeout", "message": message},
            }
        else:
            assert frame.startswith(b"event: response.failed\n")
            failed = event["response"]
            assert event["type"] == "response.failed" and failed["id"] == "resp_1"
            assert failed["status"] == "failed" and failed["output"] == []
            assert failed["error"] == {"code": "server_error", "message": message}
    assert response.close.called and not request.capture.complete
    metrics = request.record.metrics
    assert metrics["stream_error"] == metrics["error_reason"] == "upstream_timeout"
    assert metrics["error_origin"] == "upstream" and metrics["status"] == 200


async def test_failure_inside_an_event_cuts_the_connection():
    first = first_event("chat")
    request, _ = failing_passthrough("chat", [first[:10]], aiohttp.ClientPayloadError("cut"))
    outgoing = await proxy.ProxyRequest.observe_response(request)
    output = []
    with pytest.raises(aiohttp.ClientPayloadError):
        async for chunk in outgoing.body_iterator:
            output.append(chunk)
    assert output == [first[:10]]
    assert request.record.metrics["stream_error"] == "upstream_disconnected"


async def test_failure_after_terminal_event_adds_nothing():
    data = [first_event("chat"), sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})]
    request, _ = failing_passthrough("chat", data, aiohttp.ServerDisconnectedError())
    outgoing = await proxy.ProxyRequest.observe_response(request)
    output = []
    with pytest.raises(aiohttp.ServerDisconnectedError):
        async for chunk in outgoing.body_iterator:
            output.append(chunk)
    assert output == data
    assert request.record.metrics["stream_error"] == "upstream_disconnected"


@pytest.mark.parametrize(
    ("protocol", "event", "code"),
    [
        ("chat", {"error": {"code": "RateLimit", "message": "secret text"}}, "RateLimit"),
        (
            "anthropic",
            {"type": "error", "error": {"type": "overloaded_error", "message": "secret text"}},
            "overloaded_error",
        ),
        (
            "responses",
            {
                "type": "response.failed",
                "response": {"id": "resp_1", "error": {"code": "server_error", "message": "x"}},
            },
            "server_error",
        ),
    ],
)
async def test_upstream_error_event_is_relayed_and_recorded(protocol, event, code):
    data = [first_event(protocol), sse(event)]

    async def source():
        for chunk in data:
            yield chunk

    response = upstream(source)
    request = passthrough(response)
    request.capture = ResponseCapture(protocol)
    outgoing = await proxy.ProxyRequest.observe_response(request)
    assert [chunk async for chunk in outgoing.body_iterator] == data
    metrics = request.record.metrics
    assert metrics["stream_error"] == "upstream_event"
    assert metrics["upstream_error_code"] == code
    assert metrics["error_reason"] == "upstream_error" and metrics["error_origin"] == "upstream"
    assert "secret text" not in orjson.dumps(metrics).decode()
