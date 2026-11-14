# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Single-write target resolution for whitespace-bearing URIs (issue #6872).

``normalize_storage_target_uri`` canonicalizes whitespace segments to ``_`` for
fresh targets, but a file may still live at its literal pre-#5328 path. The
write coordinator must keep such targets literal so replace/append edits in
place instead of forking a sanitized sibling.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.content_write import ContentWriteCoordinator
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier

LITERAL_URI = "viking://system/memory/memory_templates/custom/Jane Doe/topic.md"
CANONICAL_URI = "viking://system/memory/memory_templates/custom/Jane_Doe/topic.md"


class _FakeVikingFS:
    def __init__(self, files=None, dirs=None):
        self._files = set(files or ())
        self._dirs = set(dirs or ())
        self.stat_calls = []
        self._ensure_access = AsyncMock()
        self._async_agfs = SimpleNamespace()

    async def stat(self, uri, ctx=None, skip_count=False):
        self.stat_calls.append(uri)
        if uri in self._files:
            return {"uri": uri, "isDir": False}
        if uri in self._dirs:
            return {"uri": uri, "isDir": True}
        raise NotFoundError(uri, "file")

    def _uri_to_path(self, uri, ctx=None):
        return "/fake/" + uri.removeprefix("viking://")


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ADMIN)


def _coordinator(fs) -> ContentWriteCoordinator:
    coordinator = ContentWriteCoordinator(viking_fs=fs)
    coordinator._write_file_only = AsyncMock(return_value={})
    return coordinator


def _written_uri(coordinator) -> str:
    return coordinator._write_file_only.await_args.kwargs["uri"]


@pytest.mark.asyncio
async def test_write_edits_literal_space_path_in_place():
    fs = _FakeVikingFS(files={LITERAL_URI})
    coordinator = _coordinator(fs)

    await coordinator._write(uri=LITERAL_URI, content="body", mode="replace", ctx=_ctx())

    assert _written_uri(coordinator) == LITERAL_URI


@pytest.mark.asyncio
async def test_write_sanitizes_whitespace_path_for_fresh_target():
    fs = _FakeVikingFS()
    coordinator = _coordinator(fs)

    await coordinator._write(uri=LITERAL_URI, content="body", mode="replace", ctx=_ctx())

    assert _written_uri(coordinator) == CANONICAL_URI


@pytest.mark.asyncio
async def test_write_ignores_literal_directory_target():
    fs = _FakeVikingFS(dirs={LITERAL_URI})
    coordinator = _coordinator(fs)

    await coordinator._write(uri=LITERAL_URI, content="body", mode="replace", ctx=_ctx())

    assert _written_uri(coordinator) == CANONICAL_URI


@pytest.mark.asyncio
async def test_write_without_whitespace_skips_existence_probe():
    uri = "viking://system/memory/memory_templates/custom/demo/topic.md"
    fs = _FakeVikingFS(files={uri})
    coordinator = _coordinator(fs)

    await coordinator._write(uri=uri, content="body", mode="replace", ctx=_ctx())

    assert fs.stat_calls == []
    assert _written_uri(coordinator) == uri


@pytest.mark.asyncio
async def test_append_edits_literal_space_path_in_place():
    fs = _FakeVikingFS(files={LITERAL_URI})
    coordinator = _coordinator(fs)

    await coordinator._write(uri=LITERAL_URI, content="more", mode="append", ctx=_ctx())

    assert _written_uri(coordinator) == LITERAL_URI
