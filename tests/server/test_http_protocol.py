# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise both HTTP parsers on a real reusable loopback connection."""

import asyncio
import socket

import pytest
import uvicorn
from fastapi import FastAPI, Request


@pytest.mark.parametrize("protocol", ["h11", "httptools"])
async def test_http11_keepalive_chunked_and_not_found(protocol):
    pytest.importorskip(protocol)
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return {"body": (await request.body()).decode()}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, http=protocol, loop="asyncio", log_config=None, lifespan="off")
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    writer = None
    try:

        async def ready():
            while not server.started:
                if task.done():
                    await task
                    raise AssertionError("server exited before startup")
                await asyncio.sleep(0.01)

        await asyncio.wait_for(ready(), 5)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        async def request(payload):
            writer.write(payload)
            await writer.drain()
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            return headers, await asyncio.wait_for(reader.readexactly(length), 5)

        headers, body = await request(
            b"POST /echo HTTP/1.1\r\nHost: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n4\r\ntest\r\n0\r\n\r\n"
        )
        assert headers.startswith(b"HTTP/1.1 200")
        assert body == b'{"body":"test"}'
        headers, body = await request(b"GET /missing HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert headers.startswith(b"HTTP/1.1 404")
        assert body == b'{"detail":"Not Found"}'
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()
