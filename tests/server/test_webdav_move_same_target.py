# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest
from starlette.requests import Request

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import webdav as webdav_router
from openviking_cli.session.user_id import UserIdentifier


class _UntouchedFS:
    """Any storage access means the request was not rejected before mutation."""

    def __getattr__(self, name):
        raise AssertionError(f"fs.{name} must not be called")


def _request(destination: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "MOVE",
            "path": "/webdav/resources/space/src.md",
            "headers": [(b"destination", destination.encode())],
        }
    )


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("default", "default"), role=Role.ROOT)


def _service():
    class Service:
        pass

    service = Service()
    service.fs = _UntouchedFS()
    return service


@pytest.mark.asyncio
async def test_move_to_the_same_uri_is_rejected_without_touching_storage(monkeypatch):
    monkeypatch.setattr(webdav_router, "get_service", lambda: _service())
    destination = "http://testserver/webdav/resources/space/src.md"

    response = await webdav_router.move(_request(destination), "space/src.md", _ctx())

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_move_into_its_own_subtree_is_rejected_without_touching_storage(monkeypatch):
    monkeypatch.setattr(webdav_router, "get_service", lambda: _service())
    destination = "http://testserver/webdav/resources/space/src.md/inner.md"

    response = await webdav_router.move(_request(destination), "space/src.md", _ctx())

    assert response.status_code == 403
