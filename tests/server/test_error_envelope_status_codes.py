# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests: error-envelope return sites must surface mapped HTTP status codes.

These routers historically returned ``Response(status="error", error=ErrorInfo(...))``
directly, which FastAPI shipped with HTTP 200 because there was no ``status_code``
override. The fix routes them through ``error_response(...)`` so the canonical
``ERROR_CODE_TO_HTTP_STATUS`` mapping drives the HTTP status.

Each test pairs the route with the documented ``ErrorInfo`` code, so a future
refactor that drops an entry from the map or forgets to use ``error_response()``
will fail loudly here.
"""

from unittest.mock import patch

import httpx
import pytest


def _assert_envelope(resp: httpx.Response, expected_code: int, expected_error_code: str):
    assert resp.status_code == expected_code, (
        f"expected HTTP {expected_code} for {expected_error_code}, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["status"] == "error", body
    assert body["error"]["code"] == expected_error_code, body
    assert body["error"]["message"], body
    return body


async def test_stats_memories_unknown_category_returns_400(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/stats/memories", params={"category": "not_a_real_category"})
    _assert_envelope(resp, expected_code=400, expected_error_code="INVALID_ARGUMENT")


async def test_stats_session_not_found_returns_404(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/stats/sessions/session-does-not-exist")
    _assert_envelope(resp, expected_code=404, expected_error_code="NOT_FOUND")


async def test_stats_session_internal_error_returns_500(client: httpx.AsyncClient):
    """When the aggregator raises an unexpected exception, the route must
    return 500 with the canonical INTERNAL_ERROR envelope, not HTTP 200."""
    with patch(
        "openviking.server.routers.stats._get_aggregator"
    ) as fake_agg:
        agg = fake_agg.return_value
        agg.get_session_extraction_stats.side_effect = RuntimeError("boom")
        resp = await client.get("/api/v1/stats/sessions/anything")
    _assert_envelope(resp, expected_code=500, expected_error_code="INTERNAL_ERROR")


async def test_debug_vector_scroll_no_vikingdb_returns_503(client: httpx.AsyncClient):
    with patch(
        "openviking.server.dependencies.get_service"
    ) as fake_get_service:
        service = fake_get_service.return_value
        service.vikingdb_manager = None
        resp = await client.get("/api/v1/debug/vector/scroll")
    _assert_envelope(resp, expected_code=503, expected_error_code="NO_VECTOR_DB")


async def test_debug_vector_count_no_vikingdb_returns_503(client: httpx.AsyncClient):
    with patch(
        "openviking.server.dependencies.get_service"
    ) as fake_get_service:
        service = fake_get_service.return_value
        service.vikingdb_manager = None
        resp = await client.get("/api/v1/debug/vector/count")
    _assert_envelope(resp, expected_code=503, expected_error_code="NO_VECTOR_DB")


async def test_debug_vector_count_invalid_filter_returns_400(client: httpx.AsyncClient):
    """Bad JSON in the ``filter`` query string must surface as 400, not 200."""
    with patch(
        "openviking.server.dependencies.get_service"
    ) as fake_get_service:
        service = fake_get_service.return_value
        service.vikingdb_manager = object()  # truthy so we get past the NO_VECTOR_DB guard
        resp = await client.get(
            "/api/v1/debug/vector/count", params={"filter": "not-json{"}
        )
    _assert_envelope(resp, expected_code=400, expected_error_code="INVALID_FILTER")


async def test_sessions_archive_not_found_returns_404(client: httpx.AsyncClient):
    """Archive lookups for an unknown archive id must return 404, not 200."""
    create_resp = await client.post("/api/v1/sessions", json={})
    assert create_resp.status_code == 200
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}/archives/no-such-archive")
    _assert_envelope(resp, expected_code=404, expected_error_code="NOT_FOUND")


def test_error_code_map_has_internal_error_no_vector_db_invalid_filter():
    """Guards the status map: dropping any of these codes re-introduces the bug
    by silently falling back to HTTP 500 on routes that target them."""
    from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS

    assert ERROR_CODE_TO_HTTP_STATUS["INTERNAL_ERROR"] == 500
    assert ERROR_CODE_TO_HTTP_STATUS["NO_VECTOR_DB"] == 503
    assert ERROR_CODE_TO_HTTP_STATUS["INVALID_FILTER"] == 400
