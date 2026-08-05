# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""FSService.write must accept and forward tags/tag_mode.

Regression guard: the /content/write router passes tags/tag_mode
unconditionally, so a missing kwarg on this wrapper breaks every write
request — including ones that carry no tags.
"""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.fs_service import FSService
from openviking_cli.session.user_id import UserIdentifier


def _ctx():
    return RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user"),
        role=Role.ROOT,
    )


class _FakeCoordinator:
    captured: dict = {}

    def __init__(self, *, viking_fs, vikingdb=None):
        del viking_fs, vikingdb

    async def write(self, **kwargs):
        _FakeCoordinator.captured = kwargs
        return {"content_updated": True}


@pytest.fixture
def svc(monkeypatch):
    _FakeCoordinator.captured = {}
    monkeypatch.setattr(
        "openviking.service.fs_service.ContentWriteCoordinator",
        _FakeCoordinator,
    )
    return FSService(viking_fs=object())


@pytest.mark.asyncio
async def test_write_forwards_tags_and_tag_mode(svc):
    out = await svc.write(
        "viking://resources/demo/doc.md",
        "hello",
        _ctx(),
        tags=["env=prod"],
        tag_mode="append",
    )

    assert out == {"content_updated": True}
    assert _FakeCoordinator.captured["uri"] == "viking://resources/demo/doc.md"
    assert _FakeCoordinator.captured["tags"] == ["env=prod"]
    assert _FakeCoordinator.captured["tag_mode"] == "append"


@pytest.mark.asyncio
async def test_write_router_style_call_without_tags(svc):
    # Mirrors the router's unconditional kwargs for a tag-less request.
    await svc.write(
        "viking://resources/demo/doc.md",
        "hello",
        _ctx(),
        mode="replace",
        wait=False,
        timeout=None,
        tags=None,
        tag_mode="replace",
    )

    assert _FakeCoordinator.captured["tags"] is None
    assert _FakeCoordinator.captured["tag_mode"] == "replace"
