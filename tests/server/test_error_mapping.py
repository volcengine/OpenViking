# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Focused tests for HTTP server exception-to-error mapping."""

from openviking.pyagfs.exceptions import AGFSClientError, AGFSHTTPError
from openviking.server.error_mapping import map_exception
from openviking_cli.exceptions import FailedPreconditionError, InvalidURIError, NotFoundError


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


def test_value_error_invalid_uri_maps_to_invalid_uri():
    mapped = map_exception(ValueError("invalid viking URI: missing path"), resource="viking://")

    assert isinstance(mapped, InvalidURIError)
    assert mapped.code == "INVALID_URI"


def test_wrapped_upstream_401_maps_to_unauthenticated():
    try:
        raise RuntimeError("OpenAI VLM completion failed") from _UpstreamHTTPError(
            401, "invalid_api_key"
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


def test_upstream_response_429_maps_to_resource_exhausted():
    mapped = map_exception(_HTTPStatusError(429, "LiteLLM embedding failed: Too Many Requests"))

    assert mapped is not None
    assert mapped.code == "RESOURCE_EXHAUSTED"
    assert mapped.details["upstream_status_code"] == 429


def test_upstream_text_status_maps_to_permission_denied():
    mapped = map_exception(RuntimeError("Cohere API error: 403 Forbidden"))

    assert mapped is not None
    assert mapped.code == "PERMISSION_DENIED"
    assert mapped.details["upstream_status_code"] == 403


def test_upstream_502_maps_to_unavailable():
    mapped = map_exception(RuntimeError("Volcengine embedding failed: HTTP 502 Bad Gateway"))

    assert mapped is not None
    assert mapped.code == "UNAVAILABLE"
    assert mapped.details["upstream_status_code"] == 502


def test_model_api_key_configuration_error_maps_to_failed_precondition():
    mapped = map_exception(ValueError("VLM configuration requires 'api_key' to be set"))

    assert isinstance(mapped, FailedPreconditionError)
    assert mapped.code == "FAILED_PRECONDITION"


def test_bare_model_api_key_required_maps_to_failed_precondition():
    mapped = map_exception(ValueError("api_key is required"))

    assert isinstance(mapped, FailedPreconditionError)
    assert mapped.code == "FAILED_PRECONDITION"
