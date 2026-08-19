# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for Feishu OpenAPI error mapping."""

from types import SimpleNamespace

import pytest

from openviking.parse.accessors.feishu_accessor import _raise_from_lark_response
from openviking_cli.exceptions import OpenVikingError


def _fake_response(
    *,
    code: int,
    msg: str,
    http_status: int = 400,
):
    return SimpleNamespace(
        code=code,
        msg=msg,
        raw=SimpleNamespace(status_code=http_status),
    )


def _raised_error(response, *, operation="resolve wiki node", resource=None):
    with pytest.raises(OpenVikingError) as exc_info:
        _raise_from_lark_response(response, operation=operation, resource=resource)
    return exc_info.value


@pytest.mark.parametrize("feishu_code", [1770032, 131006, 91403, 95008, 95009])
def test_maps_feishu_forbidden_to_permission_denied_and_keeps_details(feishu_code):
    response = _fake_response(
        code=feishu_code,
        msg="forBidden",
        http_status=400,
    )
    exc = _raised_error(
        response,
        operation="fetch document blocks",
        resource="doc_token",
    )

    assert exc.code == "PERMISSION_DENIED"
    assert f"code={feishu_code}, msg=forBidden" in exc.message
    assert exc.details["feishu_code"] == feishu_code
    assert exc.details["feishu_msg"] == "forBidden"
    assert exc.details["http_status"] == 400
    assert exc.details["resource"] == "doc_token"


def test_maps_missing_bitable_scope_without_parsing_message():
    response = _fake_response(
        code=99991672,
        msg="opaque provider message",
        http_status=400,
    )

    exc = _raised_error(response, operation="list bitable tables")

    assert exc.code == "FAILED_PRECONDITION"
    assert exc.message == (
        "Feishu application is missing required Bitable permissions: "
        "code=99991672, msg=opaque provider message"
    )


@pytest.mark.parametrize(
    ("feishu_code", "http_status", "error_code"),
    [
        (123, 401, "UNAUTHENTICATED"),
        (123, 404, "NOT_FOUND"),
        (123, 429, "RESOURCE_EXHAUSTED"),
        (123, 500, "UNAVAILABLE"),
        (123, 400, "INVALID_ARGUMENT"),
        (91402, 200, "NOT_FOUND"),
        (95006, 200, "NOT_FOUND"),
        (95007, 200, "NOT_FOUND"),
        (91404, 200, "UNAUTHENTICATED"),
    ],
)
def test_maps_provider_or_http_status_to_openviking_error(
    feishu_code,
    http_status,
    error_code,
):
    response = _fake_response(code=feishu_code, msg="failed", http_status=http_status)

    exc = _raised_error(response)

    assert exc.code == error_code
    assert exc.details["feishu_code"] == feishu_code
    assert exc.details["http_status"] == http_status
