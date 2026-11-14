# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""WebDAV MOVE must not destroy an existing destination before the move works.

``service.fs.mv`` is copy + vector update + source delete and can fail after
touching the destination, so deleting the destination up front loses data.
"""

import pytest
from starlette.requests import Request

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import webdav as webdav_router
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

PARENT = "viking://resources/space"
SOURCE = f"{PARENT}/src.md"
DESTINATION = f"{PARENT}/dst.md"


class _FakeFS:
    def __init__(self, fail_source_move: bool):
        self.fail_source_move = fail_source_move
        self.moves: list[tuple[str, str]] = []
        self.removals: list[str] = []

    async def stat(self, uri, ctx=None, skip_count=False):
        if uri in {PARENT, SOURCE, DESTINATION}:
            return {"isDir": uri == PARENT}
        raise NotFoundError(uri)

    async def mv(self, old_uri, new_uri, ctx=None):
        self.moves.append((old_uri, new_uri))
        if self.fail_source_move and old_uri == SOURCE:
            raise OSError("injected move failure")

    async def rm(self, uri, ctx=None, recursive=False):
        self.removals.append(uri)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "MOVE",
            "path": "/webdav/resources/space/src.md",
            "headers": [
                (b"destination", b"http://testserver/webdav/resources/space/dst.md"),
            ],
        }
    )


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("default", "default"), role=Role.ROOT)


def _service(fs: _FakeFS):
    class Service:
        pass

    service = Service()
    service.fs = fs
    return service


@pytest.mark.asyncio
async def test_failed_move_keeps_existing_destination(monkeypatch):
    fs = _FakeFS(fail_source_move=True)
    monkeypatch.setattr(webdav_router, "get_service", lambda: _service(fs))

    with pytest.raises(OSError, match="injected move failure"):
        await webdav_router.move(_request(), "space/src.md", _ctx())

    assert fs.removals == []
    backup = fs.moves[0][1]
    assert fs.moves[0] == (DESTINATION, backup)
    assert backup.startswith(f"{DESTINATION}.webdav-move-backup-")
    assert fs.moves[-1] == (backup, DESTINATION)


@pytest.mark.asyncio
async def test_successful_overwrite_removes_backup_only(monkeypatch):
    fs = _FakeFS(fail_source_move=False)
    monkeypatch.setattr(webdav_router, "get_service", lambda: _service(fs))

    response = await webdav_router.move(_request(), "space/src.md", _ctx())

    assert response.status_code == 204
    backup = fs.moves[0][1]
    assert fs.moves == [(DESTINATION, backup), (SOURCE, DESTINATION)]
    assert fs.removals == [backup]
