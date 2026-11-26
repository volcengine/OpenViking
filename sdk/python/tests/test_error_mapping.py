# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx
import pytest
from openviking_sdk import AsyncHTTPClient, SyncHTTPClient, errors


# Exercise constructor branches with the details a server would send.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "status", "exception", "details"),
    [
        ("INVALID_ARGUMENT", 400, errors.InvalidArgumentError, {"field": "uri"}),
        ("INVALID_URI", 400, errors.InvalidURIError, {"uri": "demo", "reason": "malformed"}),
        (
            "NOT_FOUND",
            404,
            errors.NotFoundError,
            {
                "resource": "viking://resources/demo",
                "type": "resource",
                "reason": "The data may not have been indexed yet or may have been deleted",
            },
        ),
        (
            "ALREADY_EXISTS",
            409,
            errors.AlreadyExistsError,
            {"resource": "demo", "type": "resource"},
        ),
        ("CONFLICT", 409, errors.ConflictError, {"resource": "demo"}),
        ("FAILED_PRECONDITION", 412, errors.FailedPreconditionError, {}),
        ("ABORTED", 409, errors.AbortedError, {}),
        ("UNAUTHENTICATED", 401, errors.UnauthenticatedError, {}),
        ("PERMISSION_DENIED", 403, errors.PermissionDeniedError, {}),
        ("RESOURCE_EXHAUSTED", 429, errors.ResourceExhaustedError, {"upstream_status_code": 429}),
        (
            "UNAVAILABLE",
            503,
            errors.UnavailableError,
            {"service": "storage backend", "reason": "lock contention"},
        ),
        ("INTERNAL", 500, errors.InternalError, {}),
        ("DEADLINE_EXCEEDED", 504, errors.DeadlineExceededError, {}),
        ("UNIMPLEMENTED", 501, errors.UnimplementedError, {}),
        ("NOT_INITIALIZED", 503, errors.NotInitializedError, {}),
        ("PROCESSING_ERROR", 500, errors.ProcessingError, {}),
        ("EMBEDDING_FAILED", 500, errors.EmbeddingFailedError, {}),
        ("VLM_FAILED", 500, errors.VLMFailedError, {}),
        ("SESSION_EXPIRED", 410, errors.SessionExpiredError, {}),
        ("UNKNOWN", 500, errors.OpenVikingError, {}),
        ("PROVIDER_SPECIFIC", 502, errors.OpenVikingError, {"provider": {"retry_after": 2}}),
    ],
)
async def test_public_clients_preserve_server_error_details(
    sdk_transport, code, status, exception, details
):
    sdk_transport(
        lambda request: httpx.Response(
            status,
            json={
                "status": "error",
                "error": {"code": code, "message": "request failed", "details": details},
            },
        )
    )
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        with pytest.raises(exception) as raised:
            await client.stat("viking://resources/demo")
    finally:
        await client.close()

    assert type(raised.value) is exception
    assert raised.value.code == code
    assert {key: raised.value.details[key] for key in details} == details
    if code == "NOT_FOUND":
        assert details["reason"] in str(raised.value)
    if code == "UNAVAILABLE":
        assert str(raised.value) == "Storage backend unavailable: lock contention"


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
def test_client_preserves_transport_failures(sdk_transport, failure):
    def handle(request):
        raise failure("transport failed", request=request)

    sdk_transport(handle)
    client = SyncHTTPClient(url="http://localhost:1933")
    client.initialize()
    try:
        with pytest.raises(failure, match="transport failed"):
            client.stat("viking://resources/demo")
    finally:
        client.close()
