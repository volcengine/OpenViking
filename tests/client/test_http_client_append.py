"""The additive SDK endpoint never silently downgrades replay guarantees."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openviking_sdk.client import AsyncHTTPClient, SyncHTTPClient, SyncSession
from openviking_sdk.errors import NotFoundError


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 404])
async def test_sdk_transport_keeps_identity_and_fails_closed(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={
                "status": "ok" if status == 200 else "error",
                "result": {"added": 1},
                "error": {"code": "NOT_FOUND", "message": "Unsupported endpoint"},
            },
        )

    client = AsyncHTTPClient(
        url="https://example.invalid",
        api_key="synthetic",
        account="a",
        user="u",
        actor_peer_id="actor",
        profile_enabled=False,
    )
    await client.initialize()
    headers = dict(client._http.headers)
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url="https://example.invalid", headers=headers, transport=httpx.MockTransport(handler)
    )
    try:
        call = client.session("s").append_messages(
            [{"role": "user", "content": "synthetic"}], "stable"
        )
        if status == 200:
            assert await call == {"added": 1}
        else:
            with pytest.raises(NotFoundError):
                await call
        assert len(requests) == 1
        request = requests[0]
        assert request.url.path == "/api/v1/sessions/s/messages/append"
        assert json.loads(request.content)["idempotency_key"] == "stable"
        assert request.headers["X-OpenViking-Account"] == "a"
        assert request.headers["X-OpenViking-User"] == "u"
        assert request.headers["X-OpenViking-Actor-Peer"] == "actor"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, 1, True, "", " ", "x" * 129])
async def test_invalid_keys_fail_before_request(key):
    client = AsyncHTTPClient(url="https://example.invalid")
    client._request = AsyncMock()
    with pytest.raises(ValueError):
        await client.append_messages("s", [], key)
    client._request.assert_not_called()


def test_sync_session_forwards_the_same_key_without_rebuilding_messages():
    append = AsyncMock(return_value={"added": 1})
    client = SyncHTTPClient.__new__(SyncHTTPClient)
    client._async_client = SimpleNamespace(append_messages=append)
    messages = [{"role": "user", "content": "synthetic"}]
    assert SyncSession(client, "s").append_messages(messages, "key") == {"added": 1}
    append.assert_awaited_once_with("s", messages, "key", None)
