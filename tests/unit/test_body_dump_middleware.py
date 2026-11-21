# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from starlette.background import BackgroundTasks
from starlette.requests import Request
from starlette.responses import StreamingResponse

from openviking.server.body_dump_middleware import (
    _should_skip,
    create_dump_http_body_middleware,
)


def test_zip_responses_skip_body_capture():
    assert _should_skip("application/zip")
    assert _should_skip("application/zip; charset=binary")


@pytest.mark.asyncio
async def test_rebuilt_response_preserves_background_tasks():
    """The dump middleware must not drop response.background when rebuilding."""
    ran: list[str] = []

    async def record_task() -> None:
        ran.append("ran")

    background = BackgroundTasks()
    background.add_task(record_task)
    original = StreamingResponse(
        iter([b'{"status":"ok"}']),
        media_type="application/json",
        background=background,
    )

    async def call_next(_request: Request) -> StreamingResponse:
        return original

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {"type": "http", "method": "POST", "path": "/api/v1/admin/restart", "headers": []},
        receive,
    )

    response = await create_dump_http_body_middleware()(request, call_next)

    assert response is not original
    assert response.body == b'{"status":"ok"}'
    assert response.background is background
    await response.background()
    assert ran == ["ran"]
