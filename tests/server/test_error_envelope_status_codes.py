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

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx


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
    body = _assert_envelope(resp, expected_code=400, expected_error_code="INVALID_ARGUMENT")
    assert body["error"]["message"].startswith("Unknown category: not_a_real_category.")


async def test_stats_session_lifecycle(client: httpx.AsyncClient):
    created = await client.post("/api/v1/sessions", json={})
    assert created.status_code == 200, created.text
    session_id = created.json()["result"]["session_id"]
    path = f"/api/v1/stats/sessions/{session_id}"

    response = await client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "session_id": session_id,
        "total_turns": 0,
        "memories_extracted": 0,
    }

    deleted = await client.delete(f"/api/v1/sessions/{session_id}")
    assert deleted.status_code == 200, deleted.text
    missing = await client.get(path)
    _assert_envelope(missing, expected_code=404, expected_error_code="NOT_FOUND")
    assert session_id in missing.json()["error"]["message"]


async def test_stats_session_internal_error_returns_500(
    client: httpx.AsyncClient, service, monkeypatch
):
    monkeypatch.setattr(
        service.sessions, "get", AsyncMock(side_effect=RuntimeError("storage failure"))
    )
    resp = await client.get("/api/v1/stats/sessions/anything")
    _assert_envelope(resp, expected_code=500, expected_error_code="INTERNAL_ERROR")
    assert "storage failure" not in resp.json()["error"]["message"]


async def test_debug_vector_endpoints_return_503_without_index(client, monkeypatch):
    monkeypatch.setattr(
        "openviking.server.routers.debug.get_service",
        lambda: SimpleNamespace(vikingdb_manager=None),
    )
    for operation in ("scroll", "count"):
        response = await client.get(f"/api/v1/debug/vector/{operation}")
        body = _assert_envelope(response, expected_code=503, expected_error_code="NO_VECTOR_DB")
        assert body["error"]["message"] == "Vector DB not initialized"


async def test_debug_vector_count_invalid_filter_returns_400(client: httpx.AsyncClient):
    resp = await client.get("/api/v1/debug/vector/count", params={"filter": "not-json{"})
    body = _assert_envelope(resp, expected_code=400, expected_error_code="INVALID_FILTER")
    assert body["error"]["message"] == "Invalid filter JSON"


async def test_sessions_archive_not_found_returns_404(client: httpx.AsyncClient):
    """Archive lookups for an unknown archive id must return 404, not 200."""
    create_resp = await client.post("/api/v1/sessions", json={})
    assert create_resp.status_code == 200
    session_id = create_resp.json()["result"]["session_id"]

    resp = await client.get(f"/api/v1/sessions/{session_id}/archives/archive_999")
    body = _assert_envelope(resp, expected_code=404, expected_error_code="NOT_FOUND")
    assert body["error"]["message"] == "Archive archive_999 not found"
