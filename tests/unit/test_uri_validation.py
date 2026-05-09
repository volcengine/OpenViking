# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for common Viking URI boundary validation."""

import pytest

from openviking.core.uri_validation import validate_optional_viking_uri, validate_viking_uri
from openviking_cli.exceptions import InvalidURIError


@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources/docs",
        "resources/docs",
        "/resources/docs",
        "viking://",
    ],
)
def test_validate_viking_uri_accepts_supported_forms(uri: str):
    assert validate_viking_uri(uri) == uri.strip()


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "   ",
        "viking:/resources/docs",
        "s3://bucket/key",
        "https://example.com/doc.md",
        "viking://unsupported/doc.md",
        "viking://temp/generated",
        "viking://queue/tasks",
    ],
)
def test_validate_viking_uri_rejects_invalid_or_unsupported_forms(uri: str):
    with pytest.raises(InvalidURIError):
        validate_viking_uri(uri)


def test_validate_viking_uri_hides_internal_scopes_in_public_error():
    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("ssd")

    message = str(exc_info.value)
    assert "resources" in message
    assert "temp" not in message
    assert "queue" not in message
    assert "frozenset" not in message


def test_validate_viking_uri_supports_internal_and_operation_scopes():
    assert validate_viking_uri("viking://temp/generated", allow_internal=True)

    with pytest.raises(InvalidURIError) as exc_info:
        validate_viking_uri("viking://user/memories", allowed_scopes={"resources"})

    message = str(exc_info.value)
    assert "resources" in message
    assert "user" in message
    assert "temp" not in message
    assert "queue" not in message


def test_validate_optional_viking_uri_preserves_unspecified():
    assert validate_optional_viking_uri(None) == ""
    assert validate_optional_viking_uri(" ") == ""
