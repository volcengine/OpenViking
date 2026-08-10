import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from openviking_sdk import (
    AsyncHTTPClient,
    SyncHTTPClient,
    get_actor_peer_id,
    use_actor_peer,
)


class _HeaderRecordingTransport:
    def __init__(self):
        self.requests: list[dict[str, str]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        headers = {
            name: request.headers[name]
            for name in (
                "X-OpenViking-Account",
                "X-OpenViking-User",
                "X-OpenViking-Actor-Peer",
            )
            if name in request.headers
        }
        self.requests.append(headers)
        return httpx.Response(
            status_code=200,
            json={"status": "ok", "result": {}},
            request=request,
        )


@pytest.mark.asyncio
async def test_async_http_client_scopes_actor_peer_without_overriding_tenant():
    client = AsyncHTTPClient(url="http://localhost:1933")
    transport = _HeaderRecordingTransport()
    client._http = httpx.AsyncClient(
        base_url="http://localhost:1933",
        headers={
            "X-OpenViking-Account": "credential-account",
            "X-OpenViking-User": "credential-user",
            "X-OpenViking-Actor-Peer": "configured-assistant",
        },
        transport=httpx.MockTransport(transport.handle),
    )

    async def find_as(actor_peer_id: str) -> None:
        with use_actor_peer(actor_peer_id):
            await client.find(query=f"memory for {actor_peer_id}")

    await asyncio.gather(find_as("assistant-a"), find_as("assistant-b"))
    await client.find(query="no actor peer")
    await client.close()

    runtime_requests = {
        request["X-OpenViking-Actor-Peer"]: request for request in transport.requests[:2]
    }
    assert runtime_requests == {
        "assistant-a": {
            "X-OpenViking-Account": "credential-account",
            "X-OpenViking-User": "credential-user",
            "X-OpenViking-Actor-Peer": "assistant-a",
        },
        "assistant-b": {
            "X-OpenViking-Account": "credential-account",
            "X-OpenViking-User": "credential-user",
            "X-OpenViking-Actor-Peer": "assistant-b",
        },
    }
    assert transport.requests[2] == {
        "X-OpenViking-Account": "credential-account",
        "X-OpenViking-User": "credential-user",
        "X-OpenViking-Actor-Peer": "configured-assistant",
    }


def test_sync_http_client_isolates_actor_peers_across_worker_loop_calls():
    client = SyncHTTPClient(url="http://localhost:1933")
    transport = _HeaderRecordingTransport()
    client._async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933",
        headers={
            "X-OpenViking-Account": "credential-account",
            "X-OpenViking-User": "credential-user",
        },
        transport=httpx.MockTransport(transport.handle),
    )

    def find_as(actor_peer_id: str) -> None:
        with use_actor_peer(actor_peer_id):
            client.find(query=f"memory for {actor_peer_id}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(find_as, ("assistant-a", "assistant-b")))
    client.close()

    assert {request["X-OpenViking-Actor-Peer"]: request for request in transport.requests} == {
        "assistant-a": {
            "X-OpenViking-Account": "credential-account",
            "X-OpenViking-User": "credential-user",
            "X-OpenViking-Actor-Peer": "assistant-a",
        },
        "assistant-b": {
            "X-OpenViking-Account": "credential-account",
            "X-OpenViking-User": "credential-user",
            "X-OpenViking-Actor-Peer": "assistant-b",
        },
    }


def test_actor_peer_scope_is_nested_cleared_and_restored():
    assert get_actor_peer_id() is None
    with use_actor_peer(" outer "):
        assert get_actor_peer_id() == "outer"
        with use_actor_peer("inner"):
            assert get_actor_peer_id() == "inner"
        assert get_actor_peer_id() == "outer"
        with use_actor_peer(None):
            assert get_actor_peer_id() is None
        assert get_actor_peer_id() == "outer"
    assert get_actor_peer_id() is None

    with pytest.raises(RuntimeError, match="scope failed"):
        with use_actor_peer("actor"):
            raise RuntimeError("scope failed")
    assert get_actor_peer_id() is None


@pytest.mark.parametrize(
    ("actor_peer_id", "error", "message"),
    [
        (123, TypeError, "actor_peer_id must be a string or None"),
        ("  ", ValueError, "actor_peer_id must not be empty"),
    ],
)
def test_actor_peer_scope_validates_input(actor_peer_id, error, message):
    with pytest.raises(error, match=message):
        with use_actor_peer(actor_peer_id):
            pass
    assert get_actor_peer_id() is None
