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
