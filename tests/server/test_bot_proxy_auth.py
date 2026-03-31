# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for bot proxy endpoint auth enforcement."""

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request

import openviking.server.routers.bot as bot_router_module


def make_request(headers: dict[str, str]) -> Request:
    """Create a minimal request object with the provided headers."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in headers.items()
            ],
            "query_string": b"",
        }
    )


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-API-Key": "test-key"}, "test-key"),
        ({"Authorization": "Bearer test-token"}, "test-token"),
    ],
)
def test_extract_auth_token(headers: dict[str, str], expected: str):
    """Accepted auth header formats should both produce a token."""
    assert bot_router_module.extract_auth_token(make_request(headers)) == expected
