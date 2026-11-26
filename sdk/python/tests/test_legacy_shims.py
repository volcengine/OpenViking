# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx
import pytest

from openviking import AsyncHTTPClient, SyncHTTPClient
from openviking_cli import exceptions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_type", "code", "exception", "status"),
    [
        (AsyncHTTPClient, "INVALID_ARGUMENT", exceptions.InvalidArgumentError, 400),
        (AsyncHTTPClient, "INVALID_URI", exceptions.InvalidURIError, 400),
        (AsyncHTTPClient, "NOT_FOUND", exceptions.NotFoundError, 404),
        (AsyncHTTPClient, "ALREADY_EXISTS", exceptions.AlreadyExistsError, 409),
        (AsyncHTTPClient, "CONFLICT", exceptions.ConflictError, 409),
        (AsyncHTTPClient, "FAILED_PRECONDITION", exceptions.FailedPreconditionError, 412),
        (AsyncHTTPClient, "ABORTED", exceptions.AbortedError, 409),
        (AsyncHTTPClient, "UNAUTHENTICATED", exceptions.UnauthenticatedError, 401),
        (AsyncHTTPClient, "PERMISSION_DENIED", exceptions.PermissionDeniedError, 403),
        (AsyncHTTPClient, "RESOURCE_EXHAUSTED", exceptions.ResourceExhaustedError, 429),
        (AsyncHTTPClient, "UNAVAILABLE", exceptions.UnavailableError, 503),
        (AsyncHTTPClient, "INTERNAL", exceptions.InternalError, 500),
        (AsyncHTTPClient, "DEADLINE_EXCEEDED", exceptions.DeadlineExceededError, 504),
        (AsyncHTTPClient, "UNIMPLEMENTED", exceptions.UnimplementedError, 501),
        (AsyncHTTPClient, "NOT_INITIALIZED", exceptions.NotInitializedError, 503),
        (AsyncHTTPClient, "PROCESSING_ERROR", exceptions.ProcessingError, 500),
        (AsyncHTTPClient, "EMBEDDING_FAILED", exceptions.EmbeddingFailedError, 500),
        (AsyncHTTPClient, "VLM_FAILED", exceptions.VLMFailedError, 500),
        (AsyncHTTPClient, "SESSION_EXPIRED", exceptions.SessionExpiredError, 410),
        (AsyncHTTPClient, "UNKNOWN", exceptions.OpenVikingError, 500),
        (AsyncHTTPClient, "PROVIDER_SPECIFIC", exceptions.OpenVikingError, 502),
        (SyncHTTPClient, "CONFLICT", exceptions.ConflictError, 409),
    ],
)
async def test_legacy_clients_preserve_server_error_details(
    sdk_transport, client_type, code, exception, status
):
    details = {
        "resource": "viking://resources/demo",
        "uri": "viking://resources/demo",
        "retryable": True,
        "upstream_status_code": 429,
        "provider": {"retry_after": 2},
    }
    sdk_transport(
        lambda request: httpx.Response(
            status,
            json={
                "status": "error",
                "error": {"code": code, "message": "request failed", "details": details},
            },
        )
    )
    client = client_type(url="http://localhost:1933")
    if client_type is AsyncHTTPClient:
        await client.initialize()
        try:
            with pytest.raises(exception) as raised:
                await client.stat("viking://resources/demo")
        finally:
            await client.close()
    else:
        client.initialize()
        try:
            with pytest.raises(exception) as raised:
                client.stat("viking://resources/demo")
        finally:
            client.close()

    if code == "CONFLICT":
        assert str(raised.value) == "request failed"
    assert type(raised.value) is exception
    assert raised.value.code == code
    assert {key: raised.value.details[key] for key in details} == details
