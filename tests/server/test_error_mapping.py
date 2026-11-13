# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Focused tests for HTTP server exception-to-error mapping."""

import httpx
import pytest

from openviking.models.model_error import ModelError, ModelErrorKind, model_error_boundary
from openviking.pyagfs.exceptions import (
    AGFSClientError,
    AGFSDirectoryNotEmptyError,
    AGFSHTTPError,
    AGFSInternalError,
    AGFSIsADirectoryError,
    AGFSNotADirectoryError,
    AGFSNotFoundError,
    AGFSNotSupportedError,
    AGFSPluginError,
    AGFSResourceExhaustedError,
    GitConcurrentCommitError,
)
from openviking.server.error_mapping import map_exception
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.storage.errors import (
    InvalidPathLockRequestError,
    LockAcquisitionError,
    ResourceBusyError,
)
from openviking.utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from openviking.utils.exceptions import AllCredentialsFailedError
from openviking_cli.exceptions import (
    FailedPreconditionError,
    InvalidArgumentError,
    InvalidURIError,
    NotFoundError,
    ResourceExhaustedError,
)


class _UpstreamHTTPError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _HTTPStatusError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.response = _Response(status_code)


def test_agfs_client_does_not_exist_maps_to_not_found():
    mapped = map_exception(
        AGFSClientError("path viking://missing does not exist"),
        resource="viking://missing",
        resource_type="file",
    )

    assert isinstance(mapped, NotFoundError)
    assert mapped.code == "NOT_FOUND"
    assert mapped.details == {"resource": "viking://missing", "type": "file"}


def test_agfs_client_invalid_uri_maps_to_invalid_uri():
    mapped = map_exception(
        AGFSClientError("Invalid URI: viking://"),
        resource="viking://",
    )

    assert isinstance(mapped, InvalidURIError)
    assert mapped.code == "INVALID_URI"
    assert mapped.details["uri"] == "viking://"


def test_agfs_http_status_keeps_storage_mapping():
    mapped = map_exception(
        AGFSHTTPError("No such file or directory", 404),
        resource="viking://missing",
        resource_type="file",
    )

    assert isinstance(mapped, NotFoundError)
    assert mapped.code == "NOT_FOUND"
    assert mapped.message == "File not found: viking://missing"


@pytest.mark.parametrize(
    ("error", "expected_details"),
    [
        (AGFSIsADirectoryError("is a directory"), {"expected": "file", "actual": "directory"}),
        (IsADirectoryError("is a directory"), {"expected": "file", "actual": "directory"}),
        (AGFSNotADirectoryError("not a directory"), {}),
        (NotADirectoryError("not a directory"), {}),
        (AGFSPluginError("plugin error: not a directory: /resources/file.md"), {}),
        (AGFSClientError("not a directory"), {}),
        (ValueError("not a directory"), {}),
        (AGFSDirectoryNotEmptyError("directory not empty"), {}),
    ],
)
def test_file_directory_misuse_maps_to_invalid_argument(error, expected_details):
    mapped = map_exception(
        error,
        resource="viking://resources/docs",
        resource_type="file",
    )

    assert isinstance(mapped, InvalidArgumentError)
    assert mapped.code == "INVALID_ARGUMENT"
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == 400
    assert mapped.details == {
        "resource": "viking://resources/docs",
        **expected_details,
    }


def test_agfs_not_supported_maps_to_unimplemented():
    mapped = map_exception(AGFSNotSupportedError("git feature disabled"))

    assert mapped is not None
    assert mapped.code == "UNIMPLEMENTED"


def test_agfs_resource_limit_maps_to_resource_exhausted():
    mapped = map_exception(
        AGFSResourceExhaustedError("blob exceeds configured limit"),
        resource="viking://user/test/memories/experiences/example.md",
    )

    assert isinstance(mapped, ResourceExhaustedError)
    assert mapped.code == "RESOURCE_EXHAUSTED"


def test_value_error_invalid_uri_maps_to_invalid_uri():
    mapped = map_exception(ValueError("invalid viking URI: missing path"), resource="viking://")

    assert isinstance(mapped, InvalidURIError)
    assert mapped.code == "INVALID_URI"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (ResourceBusyError("resource busy"), "CONFLICT"),
        (LockAcquisitionError("lock acquisition failed"), "CONFLICT"),
        (FileNotFoundError("missing file"), "NOT_FOUND"),
        (AGFSNotFoundError("missing storage object"), "NOT_FOUND"),
        (PermissionError("permission denied"), "PERMISSION_DENIED"),
        (AGFSHTTPError("missing object", 404), "NOT_FOUND"),
        (InvalidPathLockRequestError("invalid lock scope"), "INVALID_ARGUMENT"),
        (ValueError("invalid viking URI: missing path"), "INVALID_URI"),
    ],
)
def test_domain_mapping_precedes_model_context(error, expected_code):
    error.__context__ = ModelError(
        ModelErrorKind.AUTHENTICATION_ERROR, "invalid model key", status_code=401
    )

    mapped = map_exception(error, resource="viking://resources/docs")

    assert mapped.code == expected_code
    if expected_code == "CONFLICT":
        assert mapped.details["retryable"] is True


def test_wrapped_upstream_401_maps_to_unauthenticated():
    try:
        raise RuntimeError("OpenAI VLM completion failed") from _UpstreamHTTPError(
            401, "OpenAI API error: api_key is required"
        )
    except RuntimeError as exc:
        mapped = map_exception(exc)

    assert mapped is not None
    assert mapped.code == "UNAUTHENTICATED"
    assert mapped.details["upstream_status_code"] == 401


def test_upstream_provider_payload_message_is_not_duplicated():
    provider_error = (
        "Error code: 401 - {'error': {'code': 'AuthenticationError', "
        "'message': \"The API key doesn\\'t exist. Request id: req-1\", "
        "'param': '', 'type': 'Unauthorized'}}, request_id: req-1"
    )
    try:
        raise RuntimeError(f"Volcengine embedding failed: {provider_error}") from (
            _UpstreamHTTPError(401, provider_error)
        )
    except RuntimeError as exc:
        mapped = map_exception(exc)

    assert mapped is not None
    assert mapped.code == "UNAUTHENTICATED"
    assert mapped.message == (
        "Upstream model authentication failed (HTTP 401): "
        "The API key doesn't exist. Request id: req-1"
    )
    assert mapped.message.count("The API key doesn't exist") == 1
    assert "Error code: 401" not in mapped.message


@pytest.mark.parametrize(
    ("status", "message", "expected_code"),
    [
        (429, "Volcengine embedding failed: AccountQuotaExceeded", "RESOURCE_EXHAUSTED"),
        (503, "Embedding service unavailable while checking quota", "UNAVAILABLE"),
        (504, "Embedding request timed out", "DEADLINE_EXCEEDED"),
        (429, "OpenAI embedding failed: api_key is required", "RESOURCE_EXHAUSTED"),
    ],
)
def test_upstream_status_survives_circuit_breaker(status, message, expected_code, monkeypatch):
    error = _HTTPStatusError(status, message)
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=10)
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.monotonic", lambda: 1000)
    breaker.record_failure(error)

    for _ in range(2):
        with pytest.raises(CircuitBreakerOpen) as blocked:
            breaker.check()
        for exc in (error, blocked.value):
            mapped = map_exception(exc)
            assert mapped is not None
            assert mapped.code == expected_code
            assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == status
            assert mapped.details["upstream_status_code"] == status

    # A request that was already in flight must not replace the opening reason.
    breaker.record_failure(RuntimeError("Late failure from an in-flight request"))
    with pytest.raises(CircuitBreakerOpen) as blocked:
        breaker.check()
    assert map_exception(blocked.value).details["upstream_status_code"] == status

    # A failed recovery probe replaces the previous reason; recovery releases it.
    monkeypatch.setattr("openviking.utils.circuit_breaker.time.monotonic", lambda: 1011)
    breaker.check()
    breaker.record_failure(_HTTPStatusError(502, "Embedding API error: Bad Gateway"))
    with pytest.raises(CircuitBreakerOpen) as blocked:
        breaker.check()
    assert map_exception(blocked.value).details["upstream_status_code"] == 502
    breaker.record_success()
    breaker.check()
    breaker.record_failure(RuntimeError("Unexpected tensor shape"))
    with pytest.raises(CircuitBreakerOpen) as blocked:
        breaker.check()
    assert map_exception(blocked.value) is None


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("Cohere API error: 403 Forbidden", "PERMISSION_DENIED"),
        ("Volcengine embedding failed: Error code: 429 - quota exhausted", "RESOURCE_EXHAUSTED"),
        ("Embedding HTTP 503: api_key is required", "UNAVAILABLE"),
        ("Volcengine embedding failed: request req-401-abcd ended unexpectedly", None),
        ("Embedding failed for 429 tokens", None),
    ],
)
def test_upstream_text_status_requires_status_context(message, expected_code):
    mapped = map_exception(RuntimeError(message))
    if expected_code is None:
        assert mapped is None
    else:
        assert mapped is not None
        assert mapped.code == expected_code


@pytest.mark.parametrize(
    ("errors", "expected_code", "expected_status"),
    [
        (
            [_UpstreamHTTPError(429, "quota exhausted"), _UpstreamHTTPError(429, "rate limit")],
            "RESOURCE_EXHAUSTED",
            429,
        ),
        ([RuntimeError("Volcengine embedding failed: HTTP 502 Bad Gateway")], "UNAVAILABLE", 503),
        (
            [httpx.ReadTimeout("Request timed out."), httpx.ConnectTimeout("Request timed out.")],
            "DEADLINE_EXCEEDED",
            504,
        ),
        (
            [httpx.ConnectError("Connection refused"), httpx.ReadError("Connection reset")],
            "UNAVAILABLE",
            503,
        ),
        (
            [
                _UpstreamHTTPError(401, "invalid_api_key"),
                _UpstreamHTTPError(401, "invalid_api_key"),
            ],
            "UNAUTHENTICATED",
            401,
        ),
        (
            [_UpstreamHTTPError(401, "invalid_api_key"), httpx.ReadTimeout("Request timed out.")],
            "INTERNAL",
            500,
        ),
        (
            [httpx.ReadTimeout("Request timed out."), _UpstreamHTTPError(401, "invalid_api_key")],
            "INTERNAL",
            500,
        ),
        (
            [httpx.ReadTimeout("Request timed out."), RuntimeError("Unexpected tensor shape")],
            "INTERNAL",
            500,
        ),
        (
            [
                ValueError("Embedding provider api_key is required"),
                httpx.ReadTimeout("Request timed out."),
            ],
            "INTERNAL",
            500,
        ),
    ],
)
def test_upstream_failures_keep_their_status_through_wrappers(
    errors, expected_code, expected_status
):
    failures = []
    for index, error in enumerate(errors):
        wrapped = RuntimeError(f"Volcengine embedding failed: {error}")
        wrapped.__cause__ = error
        failures.append((f"credential-{index}", "unknown", wrapped, index))

    aggregate = AllCredentialsFailedError(failures)
    outer = RuntimeError(f"Embedding failed: {aggregate}")
    outer.__cause__ = aggregate
    mapped = map_exception(outer)

    assert mapped is not None
    assert mapped.code == expected_code
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == expected_status
    if expected_code == "INTERNAL":
        assert mapped.message == str(outer)
    else:
        assert mapped.details["upstream_error_type"] == "AllCredentialsFailedError"

    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure(outer)
    with pytest.raises(CircuitBreakerOpen) as blocked:
        breaker.check()
    mapped = map_exception(blocked.value)
    assert mapped.code == expected_code
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == expected_status


def test_model_api_key_configuration_error_maps_to_failed_precondition():
    mapped = map_exception(ValueError("VLM configuration requires 'api_key' to be set"))

    assert isinstance(mapped, FailedPreconditionError)
    assert mapped.code == "FAILED_PRECONDITION"


def test_structured_model_error_maps_without_message_guessing():
    model_error = ModelError(
        ModelErrorKind.AUTHENTICATION_ERROR,
        "MiniMax rejected the credential",
        provider="minimax",
        code="1004",
    )
    wrapper = ValueError("provider request failed")
    wrapper.__cause__ = model_error
    mapped = map_exception(wrapper)

    assert mapped is not None
    assert mapped.code == "UNAUTHENTICATED"
    assert mapped.details["model_error_kind"] == "authentication_error"
    assert mapped.details["provider"] == "minimax"
    assert mapped.details["provider_error_code"] == "1004"
    unknown = map_exception(ModelError(ModelErrorKind.UNKNOWN, "internal timeout detail"))
    assert unknown is not None
    assert unknown.code == "INTERNAL"
    assert "internal timeout detail" not in unknown.message


def test_gemini_429_without_reason_preserves_public_status():
    model_error = ModelError(
        ModelErrorKind.UNKNOWN,
        "Gemini embedding failed (HTTP 429)",
        provider="gemini",
        status_code=429,
    )

    mapped = map_exception(model_error)

    assert mapped is not None
    assert mapped.code == "RESOURCE_EXHAUSTED"
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == 429
    assert mapped.details["model_error_kind"] == "unknown"
    assert model_error.kind is ModelErrorKind.UNKNOWN
    assert mapped.details["upstream_message"] == ""


def _sdk_status_error(provider, status):
    request = httpx.Request("POST", "https://model.example.test/embeddings")
    body = {"error": {"message": "request rejected"}}
    response = httpx.Response(status, request=request, json=body)
    if provider == "openai":
        import openai

        with openai.OpenAI(api_key="test-key") as client:
            return client._make_status_error("request rejected", response=response, body=body)
    if provider == "ark":
        from volcenginesdkarkruntime import Ark

        with Ark(api_key="test-key") as client:
            return client._make_status_error(
                "request rejected", response=response, body=body, request_id="test-request"
            )
    if provider == "gemini":
        errors = pytest.importorskip("google.genai.errors")
        return errors.APIError(status, body)
    if provider == "requests":
        import requests

        response = requests.Response()
        response.status_code = status
        return requests.HTTPError("request rejected", response=response)
    return httpx.HTTPStatusError("request rejected", request=request, response=response)


@model_error_boundary
def _raise_at_model_boundary(error):
    raise error


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("provider", ["openai", "ark", "gemini", "httpx", "requests"])
async def test_final_bad_request_ignores_prior_timeout(provider, asynchronous, monkeypatch):
    from openviking.models import model_error as model_errors
    from openviking.utils.circuit_breaker import CircuitBreaker
    from openviking.utils.model_retry import retry_async, retry_sync

    attempts = 0
    classifications = 0
    sdk_kind = model_errors._sdk_kind

    def classify(error):
        nonlocal classifications
        classifications += 1
        return sdk_kind(error)

    monkeypatch.setattr(model_errors, "_sdk_kind", classify)

    def request():
        nonlocal attempts
        attempts += 1
        try:
            raise httpx.ReadTimeout("previous route timed out")
        except httpx.ReadTimeout:
            raise _sdk_status_error(provider, 400)

    @model_error_boundary
    def call():
        request()

    @model_error_boundary
    async def call_async():
        request()

    with pytest.raises(ModelError) as raised:
        if asynchronous:
            await retry_async(call_async, max_retries=2, base_delay=0, jitter=False)
        else:
            retry_sync(call, max_retries=2, base_delay=0, jitter=False)

    assert attempts == 1
    assert raised.value.kind is ModelErrorKind.INVALID_REQUEST
    mapped = map_exception(raised.value)
    assert mapped.code == "INVALID_ARGUMENT"
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == 400
    assert mapped.details["upstream_status_code"] == 400
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure(raised.value)
    breaker.check()
    assert classifications == 1


@pytest.mark.parametrize("provider", ["openai", "ark", "gemini", "httpx", "requests"])
@pytest.mark.parametrize(
    ("status", "expected_code", "expected_http"),
    [
        (402, "RESOURCE_EXHAUSTED", 429),
        (406, "INVALID_ARGUMENT", 400),
        (408, "DEADLINE_EXCEEDED", 504),
        (409, "CONFLICT", 409),
        (410, "INVALID_ARGUMENT", 400),
        (415, "INVALID_ARGUMENT", 400),
        (501, "UNAVAILABLE", 503),
        (504, "DEADLINE_EXCEEDED", 504),
    ],
)
def test_sdk_status_survives_model_boundary(provider, status, expected_code, expected_http):
    with pytest.raises(ModelError) as raised:
        _raise_at_model_boundary(_sdk_status_error(provider, status))
    wrapper = RuntimeError("Embedding failed")
    wrapper.__cause__ = raised.value

    mapped = map_exception(wrapper)

    assert mapped.code == expected_code
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == expected_http
    assert mapped.details["upstream_status_code"] == status
    if raised.value.kind is ModelErrorKind.UNKNOWN:
        assert "request rejected" not in mapped.message
        assert mapped.details["upstream_message"] == ""


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(401, "UNAUTHENTICATED"), (429, "RESOURCE_EXHAUSTED"), (504, "DEADLINE_EXCEEDED")],
)
def test_litellm_generic_bad_request_preserves_response_status(status, expected_code):
    from litellm.exceptions import BadRequestError

    response = httpx.Response(status, request=httpx.Request("POST", "https://model.example.test"))
    sdk_error = BadRequestError(
        "invalid_request_error", model="test-model", llm_provider="openai", response=response
    )
    with pytest.raises(ModelError) as raised:
        _raise_at_model_boundary(sdk_error)

    mapped = map_exception(raised.value)

    assert mapped.code == expected_code
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == status


@pytest.mark.parametrize(
    ("kind", "status", "provider_code", "expected_code"),
    [
        (ModelErrorKind.AUTHENTICATION_ERROR, 400, "API_KEY_INVALID", "UNAUTHENTICATED"),
        (ModelErrorKind.QUOTA_EXHAUSTED, 403, "BILLING_DISABLED", "RESOURCE_EXHAUSTED"),
        (ModelErrorKind.INPUT_TOO_LARGE, 500, "context_length_exceeded", "INVALID_ARGUMENT"),
        (ModelErrorKind.CONTENT_SAFETY, 500, "content_policy_violation", "INVALID_ARGUMENT"),
        (ModelErrorKind.UNSUPPORTED, 400, "unsupported_parameter", "INVALID_ARGUMENT"),
    ],
)
def test_specific_model_kind_still_overrides_http_status(
    kind, status, provider_code, expected_code
):
    mapped = map_exception(
        ModelError(kind, "request rejected", status_code=status, code=provider_code)
    )

    assert mapped.code == expected_code
    assert mapped.details["provider_error_code"] == provider_code


@pytest.mark.parametrize(
    "statuses,expected_code", [((504, 504), "DEADLINE_EXCEEDED"), ((401, 429), "INTERNAL")]
)
def test_model_boundary_preserves_aggregate_protocol_mapping(statuses, expected_code):
    failures = []
    for index, status in enumerate(statuses):
        with pytest.raises(ModelError) as raised:
            _raise_at_model_boundary(_sdk_status_error("openai", status))
        failures.append((f"credential-{index}", raised.value.kind, raised.value, index))
    aggregate = AllCredentialsFailedError(failures)
    with pytest.raises(ModelError) as raised:
        _raise_at_model_boundary(aggregate)

    assert map_exception(aggregate).code == expected_code
    assert map_exception(raised.value).code == expected_code


def test_bare_model_api_key_required_maps_to_failed_precondition():
    mapped = map_exception(ValueError("api_key is required"))

    assert isinstance(mapped, FailedPreconditionError)
    assert mapped.code == "FAILED_PRECONDITION"


def test_resource_busy_maps_to_structured_conflict():
    mapped = map_exception(
        ResourceBusyError(
            "Reexact is busy: viking://resources/docs/a.md",
            uri="viking://resources/docs/a.md",
            conflict_type="path_busy",
            retryable=True,
        ),
        resource="viking://resources/docs",
    )

    assert mapped is not None
    assert mapped.code == "CONFLICT"
    assert mapped.details == {
        "resource": "viking://resources/docs/a.md",
        "uri": "viking://resources/docs/a.md",
        "conflict_type": "path_busy",
        "retryable": True,
    }


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_status"),
    [
        (LockAcquisitionError("Failed to acquire exact lock"), "CONFLICT", 409),
        (AGFSInternalError("invalid lock token: missing ':' in token"), "INTERNAL", 500),
        (AGFSInternalError("lock I/O error: failed to create lock dir"), "INTERNAL", 500),
        (AGFSInternalError("AES-GCM authentication failed: aead::Error"), "INTERNAL", 500),
        (AGFSPluginError("plugin error: backend connection lost"), "UNAVAILABLE", 503),
    ],
)
def test_storage_conflicts_remain_distinct_from_failures(error, expected_code, expected_status):
    mapped = map_exception(
        error,
        resource="viking://resources/docs/a.md",
    )

    assert mapped is not None
    assert mapped.code == expected_code
    assert ERROR_CODE_TO_HTTP_STATUS[mapped.code] == expected_status
    if expected_code == "CONFLICT":
        assert mapped.details == {
            "resource": "viking://resources/docs/a.md",
            "uri": "viking://resources/docs/a.md",
            "conflict_type": "path_busy",
            "retryable": True,
        }


def test_git_concurrent_commit_maps_to_conflict():
    err = GitConcurrentCommitError("ref moved")
    mapped = map_exception(err)
    assert mapped is not None
    assert mapped.code == "CONFLICT"
    assert ERROR_CODE_TO_HTTP_STATUS.get(mapped.code) == 409


def test_git_auth_failure_remains_a_client_error():
    from openviking.server.responses import error_response, response_from_result
    from openviking.utils.git_auth import GIT_AUTH_FAILED, raise_git_auth_error
    from openviking_cli.exceptions import OpenVikingError

    with pytest.raises(OpenVikingError) as caught:
        raise_git_auth_error(b"fatal: Authentication failed")
    error = map_exception(caught.value)
    assert error.code == GIT_AUTH_FAILED
    assert ERROR_CODE_TO_HTTP_STATUS.get(error.code, 500) == 400
    assert error_response(error.code, error.message).status_code == 400
    assert (
        response_from_result(
            {"status": "error", "code": error.code, "errors": [error.message]}
        ).status_code
        == 400
    )
