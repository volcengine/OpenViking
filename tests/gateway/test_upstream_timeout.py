"""upstream_timeout_seconds bounds each wait on the upstream, not the length of a reply."""

import asyncio
import json
import socket
import time

import aiohttp
import async_timeout
import pytest
from aiohttp import web
from test_app import enable_tools
from test_native_tools import PATHS, native_events, native_response, request_body, wire_response

from openviking_gateway import proxy
from openviking_gateway.protocols import SSEDecoder
from openviking_gateway.tool_protocols.common import sse

WAIT = 0.3
CHAT = "/v1/chat/completions"


@pytest.fixture
def gateway_config():
    return {"upstream_timeout_seconds": WAIT}


def chat_events(count):
    events = [
        {"id": "r-1", "choices": [{"delta": {"content": f"part-{n} "}, "finish_reason": None}]}
        for n in range(count)
    ]
    events.append({"id": "r-1", "choices": [{"delta": {}, "finish_reason": "stop"}]})
    return b"".join(sse(event) for event in events) + b"data: [DONE]\n\n"


async def trickle(request, data, pieces, pause, content_type="text/event-stream"):
    """Send ``data`` in ``pieces`` parts with ``pause`` seconds of silence before each."""
    response = web.StreamResponse(headers={"Content-Type": content_type})
    await response.prepare(request)
    size = -(-len(data) // pieces)
    for offset in range(0, len(data), size):
        await asyncio.sleep(pause)
        await response.write(data[offset : offset + size])
    await response.write_eof()
    return response


@pytest.fixture
def release():
    """Lets handlers that never answer return once the test is over."""
    event = asyncio.Event()
    yield event
    event.set()


def auth(key):
    return {"Authorization": "Bearer " + key["key"]}


async def test_stream_that_keeps_producing_outlives_the_timeout(running_gateway):
    app, client, _, key, _, _ = running_gateway
    data = chat_events(20)

    async def backend(request):
        if request.path == CHAT:
            # About 1.2 s in all, four times the timeout, never silent for long.
            return await trickle(request, data, 20, WAIT / 5)
        return None

    app.state.test_backend["handler"] = backend
    started = time.monotonic()
    async with async_timeout.timeout(5):
        response = await client.post(
            CHAT, headers=auth(key), json={"model": "model", "messages": [], "stream": True}
        )
    assert time.monotonic() - started > 3 * WAIT
    assert response.status_code == 200
    assert response.content == data


async def test_non_streaming_body_that_keeps_arriving_outlives_the_timeout(running_gateway):
    app, client, _, key, _, _ = running_gateway
    reply = {
        "id": "r-1",
        "choices": [
            {"message": {"role": "assistant", "content": "x" * 200}, "finish_reason": "stop"}
        ],
    }
    data = json.dumps(reply).encode()

    async def backend(request):
        if request.path == CHAT:
            return await trickle(request, data, 20, WAIT / 5, "application/json")
        return None

    app.state.test_backend["handler"] = backend
    started = time.monotonic()
    async with async_timeout.timeout(5):
        response = await client.post(
            CHAT, headers=auth(key), json={"model": "model", "messages": []}
        )
    assert time.monotonic() - started > 3 * WAIT
    assert response.status_code == 200
    assert response.json() == reply


@pytest.mark.parametrize("stream", [False, True])
async def test_upstream_that_never_answers_is_cut_off(running_gateway, release, stream):
    app, client, _, key, _, _ = running_gateway

    async def backend(request):
        if request.path == CHAT:
            await release.wait()
            return web.json_response({})
        return None

    app.state.test_backend["handler"] = backend
    started = time.monotonic()
    async with async_timeout.timeout(5):
        response = await client.post(
            CHAT, headers=auth(key), json={"model": "model", "messages": [], "stream": stream}
        )
    elapsed = time.monotonic() - started
    assert response.status_code == 502
    assert response.json()["detail"] == "Model upstream is unavailable"
    assert WAIT * 0.8 < elapsed < WAIT + 1.5


async def test_stream_that_goes_silent_is_cut_off(running_gateway, release):
    app, client, _, key, _, _ = running_gateway
    first = chat_events(1).split(b"\n\n")[0] + b"\n\n"

    async def backend(request):
        if request.path == CHAT:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(first)
            await release.wait()
            return response
        return None

    app.state.test_backend["handler"] = backend
    started = time.monotonic()
    # The in-process transport raises the error that aborts the client connection; the
    # elapsed time shows it is the upstream timeout, not the test's own guard.
    with pytest.raises(asyncio.TimeoutError):
        async with async_timeout.timeout(5):
            await client.post(
                CHAT, headers=auth(key), json={"model": "model", "messages": [], "stream": True}
            )
    assert WAIT * 0.8 < time.monotonic() - started < WAIT + 1.5


def slow_wire_response(request, protocol, value):
    data = b"".join(
        b"event: " + e["type"].encode() + b"\n" + sse(e) for e in native_events(protocol, value)
    )
    # About 2.4 s in all, eight times the upstream timeout, never silent for long.
    return trickle(request, data, 40, WAIT / 5)


@pytest.mark.parametrize("deadline", [False, True])
async def test_tool_continuation_waits_per_read_and_total_deadline_stays_total(
    running_gateway, monkeypatch, deadline
):
    monkeypatch.setattr(proxy, "HEARTBEAT_SECONDS", 0.05)
    protocol = "responses"
    app, client, admin, key, _, _ = running_gateway
    # The deadline is longer than the upstream timeout but shorter than the continuation.
    await enable_tools(
        client, admin, show_tool_calls=False, tool_total_seconds=2 * WAIT if deadline else None
    )
    calls = []

    async def backend(request):
        if request.path == "/mcp":
            return web.json_response({"id": 1, "result": {"content": []}})
        if request.path == PATHS[protocol]:
            calls.append(await request.json())
            value = native_response(protocol, len(calls), owned=len(calls) == 1)
            if len(calls) == 1:
                return await wire_response(request, protocol, value, True)
            return await slow_wire_response(request, protocol, value)
        return None

    app.state.test_backend["handler"] = backend
    started = time.monotonic()
    async with async_timeout.timeout(5):
        response = await client.post(
            PATHS[protocol], headers=auth(key), json=request_body(protocol, True)
        )
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert len(calls) == 2
    events = [
        value for frame in SSEDecoder().feed(response.content) if (value := SSEDecoder.data(frame))
    ]
    if deadline:
        assert b"Hidden tool request timed out" in response.content
        assert events[-1]["type"] == "response.failed"
        assert elapsed < 2 * WAIT + 0.6
    else:
        assert b"answer-2" in response.content
        assert events[-1]["type"] == "response.completed"
        assert elapsed > 3 * WAIT


@pytest.fixture
async def silent_server():
    """A TCP server that accepts connections and then neither reads nor answers."""
    held = []

    async def accept(reader, writer):
        held.append(writer)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    for sock in server.sockets:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()


async def test_wait_covers_sending_a_body_the_upstream_never_reads(silent_server):
    async with aiohttp.ClientSession() as http:
        started = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            async with async_timeout.timeout(5):
                await proxy.open_upstream(http, "POST", silent_server, WAIT, data=b"x" * (16 << 20))
        assert time.monotonic() - started < WAIT + 1.5


async def test_sock_read_alone_bounds_the_wait_for_response_headers(silent_server):
    # open_upstream relies on aiohttp starting sock_read once the request is sent.
    async with aiohttp.ClientSession() as http:
        started = time.monotonic()
        with pytest.raises(aiohttp.ServerTimeoutError):
            async with async_timeout.timeout(5):
                await http.post(
                    silent_server, data=b"{}", timeout=aiohttp.ClientTimeout(sock_read=WAIT)
                )
        assert WAIT * 0.8 < time.monotonic() - started < WAIT + 1.5
