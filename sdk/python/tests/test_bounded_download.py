"""Bounded downloads use the real transport and close uncertain responses."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openviking_sdk.client import AsyncHTTPClient, SyncHTTPClient
from openviking_sdk.errors import NotFoundError, ResourceExhaustedError


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None, block=False):
        self.chunks, self.error, self.block = chunks, error, block
        self.closed = False
        self.reads = 0
        self.started = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error:
            raise self.error
        if self.block:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def make_client(handler, **kwargs):
    gateway = kwargs.pop("gateway_token", None)
    client = AsyncHTTPClient(
        url="https://example.invalid",
        account="a",
        user="u",
        api_key="synthetic",
        actor_peer_id="actor",
        **kwargs,
    )
    if gateway is not None:
        client._gateway_token = gateway
    await client.initialize()
    headers = client._http.headers
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://example.invalid", headers=headers, transport=httpx.MockTransport(handler)
    )
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks,limit", [([b"ab", b"cd", b"ef"], 4), ([b"x"], 0)])
async def test_chunked_old_server_is_bounded_without_trusting_the_query(chunks, limit):
    body = Body(chunks)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=body)

    client = await make_client(handler)
    try:
        with pytest.raises(ResourceExhaustedError):
            await client.download_bytes("resources/demo/input.bin", max_bytes=limit)
        assert body.closed
        assert requests[0].url.params["max_bytes"] == str(limit)
        assert requests[0].headers["X-OpenViking-Account"] == "a"
        assert requests[0].headers["X-OpenViking-User"] == "u"
        assert requests[0].headers["X-OpenViking-Actor-Peer"] == "actor"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_content_length_rejects_before_reading():
    body = Body([b"oversized"])
    client = await make_client(
        lambda r: httpx.Response(200, headers={"Content-Length": "9"}, stream=body)
    )
    try:
        with pytest.raises(ResourceExhaustedError):
            await client.download_bytes("resources/demo/input.bin", max_bytes=1)
        assert body.closed and body.reads == 0
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks,limit,expected", [([], 0, b""), ([b"ab", b"cd"], 4, b"abcd")])
async def test_exact_limit_and_empty_file_are_preserved(chunks, limit, expected):
    body = Body(chunks)
    client = await make_client(lambda r: httpx.Response(200, stream=body))
    try:
        assert await client.download_bytes("resources/demo/input.bin", max_bytes=limit) == expected
        assert body.closed
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_gateway_challenge_closes_before_retry_and_keeps_headers():
    challenge, output = Body([b"unused challenge body"]), Body([b"ok"])
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401, headers={"X-VikingBot-Gateway": "true"}, stream=challenge)
        assert challenge.closed
        assert request.headers["X-Gateway-Token"] == "synthetic-gateway"
        return httpx.Response(200, stream=output)

    client = await make_client(handler, gateway_token="synthetic-gateway")
    try:
        assert await client.download_bytes("resources/demo/input.bin", max_bytes=2) == b"ok"
        assert len(requests) == 2 and output.closed
        assert all(r.headers["X-OpenViking-User"] == "u" for r in requests)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sdk_error_mapping_closes_the_bounded_response():
    body = Body(
        [
            json.dumps(
                {"status": "error", "error": {"code": "NOT_FOUND", "message": "missing"}}
            ).encode()
        ]
    )
    client = await make_client(lambda r: httpx.Response(404, stream=body))
    try:
        with pytest.raises(NotFoundError):
            await client.download_bytes("resources/demo/input.bin", max_bytes=1024)
        assert body.closed
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_failure_closes_the_bounded_response():
    body = Body([b"a"], error=httpx.ReadError("synthetic disconnect"))
    client = await make_client(lambda r: httpx.Response(200, stream=body))
    try:
        with pytest.raises(httpx.ReadError):
            await client.download_bytes("resources/demo/input.bin", max_bytes=10)
        assert body.closed
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancellation_closes_the_bounded_response():
    body = Body([], block=True)
    client = await make_client(lambda r: httpx.Response(200, stream=body))
    try:
        task = asyncio.create_task(client.download_bytes("resources/demo/input.bin", max_bytes=10))
        await asyncio.wait_for(body.started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [-1, True, 1.5, "1", 2**63 - 1])
async def test_invalid_limits_fail_before_network(limit):
    client = AsyncHTTPClient(url="https://example.invalid")
    client._request = AsyncMock()
    with pytest.raises(ValueError):
        await client.download_bytes("resources/demo/input.bin", max_bytes=limit)
    client._request.assert_not_called()


def test_sync_wrapper_forwards_the_limit():
    download = AsyncMock(return_value=b"ok")
    client = SyncHTTPClient.__new__(SyncHTTPClient)
    client._async_client = SimpleNamespace(download_bytes=download)
    assert client.download_bytes("resources/demo/input.bin", max_bytes=2) == b"ok"
    download.assert_awaited_once_with("resources/demo/input.bin", max_bytes=2)


@pytest.mark.asyncio
async def test_decoded_response_is_checked_instead_of_compressed_content_length():
    import gzip

    compressed = gzip.compress(b"x" * 100)
    body = Body([compressed])
    client = await make_client(
        lambda r: httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(compressed))},
            stream=body,
        )
    )
    try:
        with pytest.raises(ResourceExhaustedError):
            await client.download_bytes("resources/demo/input.bin", max_bytes=50)
        assert body.closed and body.reads == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_request_scoped_actor_uses_the_existing_header_owner():
    from openviking_sdk import use_actor_peer

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Body([b"ok"]))

    client = await make_client(handler)
    try:
        with use_actor_peer("current-assistant"):
            assert await client.download_bytes("resources/demo/input.bin", max_bytes=2) == b"ok"
        assert requests[0].headers["X-OpenViking-Actor-Peer"] == "current-assistant"
        assert requests[0].headers["X-OpenViking-User"] == "u"
        assert requests[0].headers["X-OpenViking-Account"] == "a"
    finally:
        await client.close()
