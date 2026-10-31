# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient
from openviking_sdk.client import ERROR_CODE_TO_EXCEPTION
from openviking_sdk.errors import (
    AbortedError,
    ConflictError,
    OpenVikingError,
    ResourceExhaustedError,
    UnavailableError,
    UnimplementedError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (200, b"<html>Please sign in</html>"),
        (200, b'{"status":'),
        (502, b"Bad Gateway"),
        (503, b""),
    ],
)
async def test_public_clients_reject_non_json_api_responses(sync, status_code, body):
    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    async_client = client._async_client if isinstance(client, SyncHTTPClient) else client
    async with httpx.AsyncClient(
        base_url="http://localhost:1933",
        transport=httpx.MockTransport(lambda _request: httpx.Response(status_code, content=body)),
    ) as http:
        async_client._http = http
        for method, args in [
            ("rm", ("viking://resources/demo",)),
            ("add_message", ("demo", "user", "hello")),
        ]:
            with pytest.raises(OpenVikingError) as raised:
                result = getattr(client, method)(*args)
                if not sync:
                    await result
            assert raised.value.code == "INTERNAL"
            assert str(raised.value) == f"HTTP {status_code}: {body.decode() or 'empty response'}"

        # These same bytes are valid content for a raw download.
        if status_code == 200:
            result = client.download_bytes("viking://resources/demo")
            if not sync:
                result = await result
            assert result == body


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (200, b'{"status":"ok","result":{"message_id":"accepted"}}', {"message_id": "accepted"}),
        (200, b"", {}),
        (204, b"", {}),
    ],
)
async def test_public_clients_preserve_json_and_empty_successes(sync, status_code, body, expected):
    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    async_client = client._async_client if isinstance(client, SyncHTTPClient) else client
    async with httpx.AsyncClient(
        base_url="http://localhost:1933",
        transport=httpx.MockTransport(lambda _request: httpx.Response(status_code, content=body)),
    ) as http:
        async_client._http = http
        for method, args, expected_result in [
            ("rm", ("viking://resources/demo",), None),
            ("add_message", ("demo", "user", "hello"), expected),
        ]:
            result = getattr(client, method)(*args)
            if not sync:
                result = await result
            assert result == expected_result


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("code", ERROR_CODE_TO_EXCEPTION)
async def test_public_clients_preserve_server_error_details(sync, code):
    details = {
        "resource": "viking://resources/demo",
        "uri": "viking://resources/demo",
        "retryable": True,
        "upstream_status_code": 503,
        "provider": {"retry_after": 2},
    }
    client = (SyncHTTPClient if sync else AsyncHTTPClient)(url="http://localhost:1933")
    async_client = client._async_client if sync else client
    async_client._http = httpx.AsyncClient(
        base_url="http://localhost:1933",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={
                    "status": "error",
                    "error": {"code": code, "message": "busy", "details": details},
                },
            )
        ),
    )
    try:
        with pytest.raises(OpenVikingError) as raised:
            if sync:
                client.stat("viking://resources/demo")
            else:
                await client.stat("viking://resources/demo")
        assert raised.value.code == code
        assert {key: raised.value.details[key] for key in details} == details
    finally:
        await async_client.close()


@pytest.mark.parametrize(
    ("code", "exc_type"),
    (
        ("CONFLICT", ConflictError),
        ("ABORTED", AbortedError),
        ("RESOURCE_EXHAUSTED", ResourceExhaustedError),
        ("UNIMPLEMENTED", UnimplementedError),
    ),
)
def test_client_maps_standard_error_codes(code, exc_type):
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(exc_type) as exc_info:
        client._raise_exception({"code": code, "message": "mapped"})

    assert exc_info.value.code == code


def test_client_preserves_unknown_error_code():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(OpenVikingError) as exc_info:
        client._raise_exception(
            {
                "code": "PROVIDER_SPECIFIC",
                "message": "provider-specific failure",
                "details": {"x": 1},
            }
        )

    assert exc_info.value.code == "PROVIDER_SPECIFIC"
    assert exc_info.value.details == {"x": 1}


def test_client_preserves_unavailable_service_and_reason():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(UnavailableError) as exc_info:
        client._raise_exception(
            {
                "code": "UNAVAILABLE",
                "message": "Storage backend unavailable: lock contention",
                "details": {
                    "service": "storage backend",
                    "reason": "failed to read lock token (os error 33)",
                },
            }
        )

    assert str(exc_info.value) == (
        "Storage backend unavailable: failed to read lock token (os error 33)"
    )
    assert exc_info.value.details == {
        "service": "storage backend",
        "reason": "failed to read lock token (os error 33)",
    }
