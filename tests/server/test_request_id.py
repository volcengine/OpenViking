# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Request ID validation, propagation, and logging tests."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from uuid import UUID

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openviking.metrics.datasources import HttpRequestLifecycleDataSource
from openviking.observability.context import get_root_observability_context
from openviking.observability.http_observability_middleware import (
    create_http_observability_middleware,
)
from openviking.server.app import create_app
from openviking.server.config import ServerConfig
from openviking.server.request_id import REQUEST_ID_HEADER, RequestIdMiddleware


class _CaptureHandler(logging.Handler):
    def __init__(self, records: list[logging.LogRecord]) -> None:
        super().__init__()
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record)


def _make_test_app() -> FastAPI:
    app = FastAPI()
    observability_middleware = create_http_observability_middleware()

    @app.middleware("http")
    async def add_observability(request, call_next):
        return await observability_middleware(request, call_next)

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    return app


async def _request(app: FastAPI, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, **kwargs)


def _assert_generated_request_id(value: str) -> None:
    assert UUID(value).version == 4


async def test_client_request_id_is_returned_and_reused_by_observability() -> None:
    app = _make_test_app()

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        root = get_root_observability_context()
        return {"item_id": item_id, "observability_request_id": root.request_id}

    response = await _request(
        app,
        "/items/42",
        headers={REQUEST_ID_HEADER: "resource-import-20260728-001"},
    )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "resource-import-20260728-001"
    assert response.json()["observability_request_id"] == "resource-import-20260728-001"


async def test_missing_request_id_generates_uuid_for_health_and_cors_exposes_it() -> None:
    app = _make_test_app()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    response = await _request(
        app,
        "/health",
        headers={"Origin": "https://example.test"},
    )

    assert response.status_code == 200
    _assert_generated_request_id(response.headers[REQUEST_ID_HEADER])
    assert REQUEST_ID_HEADER.lower() in response.headers["Access-Control-Expose-Headers"].lower()


async def test_invalid_request_ids_are_rejected_without_logging_raw_values() -> None:
    app = _make_test_app()
    records: list[logging.LogRecord] = []
    server_logger = logging.getLogger("openviking")

    handler = _CaptureHandler(records)
    server_logger.addHandler(handler)
    try:
        cases = [
            [(REQUEST_ID_HEADER, "")],
            [(REQUEST_ID_HEADER, "contains spaces")],
            [(REQUEST_ID_HEADER, "x" * 129)],
            [(REQUEST_ID_HEADER, "first"), (REQUEST_ID_HEADER, "second")],
        ]
        responses = [await _request(app, "/items", headers=headers) for headers in cases]
    finally:
        server_logger.removeHandler(handler)

    assert all(response.status_code == 400 for response in responses)
    assert all(response.json()["error"]["code"] == "INVALID_ARGUMENT" for response in responses)
    for response in responses:
        _assert_generated_request_id(response.headers[REQUEST_ID_HEADER])
    completion_records = [
        record for record in records if record.name == "openviking.observability.http"
    ]
    assert all(record.request_id for record in completion_records)
    rendered = "\n".join(record.getMessage() for record in completion_records)
    assert all(raw not in rendered for raw in ("contains spaces", "x" * 129, "first", "second"))


async def test_unhandled_500_keeps_request_id_for_log_and_response(monkeypatch) -> None:
    app = create_app(config=ServerConfig(), service=SimpleNamespace())
    records: list[logging.LogRecord] = []
    completed_requests: list[dict] = []
    monkeypatch.setattr(
        HttpRequestLifecycleDataSource,
        "record_request",
        lambda **kwargs: completed_requests.append(kwargs),
    )
    server_logger = logging.getLogger("openviking")

    handler = _CaptureHandler(records)
    server_logger.addHandler(handler)

    @app.get("/_request-id-test/boom")
    async def boom():
        raise RuntimeError("synthetic failure")

    try:
        response = await _request(
            app,
            "/_request-id-test/boom",
            headers={REQUEST_ID_HEADER: "failed-request-001"},
        )
    finally:
        server_logger.removeHandler(handler)

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "failed-request-001"
    error_records = [
        record
        for record in records
        if record.name == "openviking.server.app" and "Unhandled exception" in record.getMessage()
    ]
    assert len(error_records) == 1
    assert error_records[0].request_id == "failed-request-001"
    assert error_records[0].getMessage().startswith("[request_id=failed-request-001] ")
    assert len(completed_requests) == 1
    assert completed_requests[0]["error_code"] == "INTERNAL"
    assert completed_requests[0]["error_message"] == "Internal server error"
    assert completed_requests[0]["error_details"] is None
    assert "synthetic failure" not in str(completed_requests[0])


async def test_mapped_exception_audit_matches_public_response(monkeypatch) -> None:
    app = create_app(config=ServerConfig(), service=SimpleNamespace())
    completed_requests: list[dict] = []
    monkeypatch.setattr(
        HttpRequestLifecycleDataSource,
        "record_request",
        lambda **kwargs: completed_requests.append(kwargs),
    )

    @app.get("/_request-id-test/invalid")
    async def invalid():
        raise ValueError("invalid page size")

    response = await _request(app, "/_request-id-test/invalid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert len(completed_requests) == 1
    assert completed_requests[0]["status"] == "400"
    assert completed_requests[0]["error_code"] == "INVALID_ARGUMENT"
    assert completed_requests[0]["error_message"] == "invalid page size"
    assert completed_requests[0]["error_details"] == response.json()["error"]["details"]


async def test_concurrent_requests_do_not_mix_or_leak_request_ids() -> None:
    app = _make_test_app()
    records: list[logging.LogRecord] = []
    server_logger = logging.getLogger("openviking")
    business_logger = logging.getLogger("openviking.service.concurrent_test")

    handler = _CaptureHandler(records)
    server_logger.addHandler(handler)
    gate = asyncio.Event()
    arrivals = 0

    @app.get("/work/{label}")
    async def work(label: str):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            gate.set()
        await gate.wait()
        business_logger.info("finished label=%s", label)
        return {"label": label}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first, second = await asyncio.gather(
                client.get("/work/first", headers={REQUEST_ID_HEADER: "request-first"}),
                client.get("/work/second", headers={REQUEST_ID_HEADER: "request-second"}),
            )
    finally:
        server_logger.removeHandler(handler)

    assert first.headers[REQUEST_ID_HEADER] == "request-first"
    assert second.headers[REQUEST_ID_HEADER] == "request-second"
    business_records = [record for record in records if record.name == business_logger.name]
    assert {record.args[0]: record.request_id for record in business_records} == {
        "first": "request-first",
        "second": "request-second",
    }
