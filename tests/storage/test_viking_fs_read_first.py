# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Edge cases for the fused read-then-stat read path (read first, stat on empty)."""

from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class FakeAsyncAGFS:
    """Minimal in-memory stand-in for AsyncAGFSClient."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.read_calls = 0
        self.stat_calls = 0

    async def read(self, path: str, *args, **kwargs) -> bytes:
        self.read_calls += 1
        return self.files.get(path, b"")

    async def stat(self, path: str, *args, **kwargs) -> dict:
        self.stat_calls += 1
        if path in self.dirs:
            return {"isDir": True, "size": 0}
        if path in self.files:
            return {"isDir": False, "size": len(self.files[path])}
        raise AGFSNotFoundError(path)


def _ctx(user_id: str = "default") -> RequestContext:
    return RequestContext(user=UserIdentifier("default", user_id), role=Role.ROOT)


def _make_fs() -> tuple[VikingFS, FakeAsyncAGFS]:
    fs = VikingFS(agfs=object())
    fake = FakeAsyncAGFS()
    fs._async_agfs = fake
    return fs, fake


def _path(fs: VikingFS, uri: str) -> str:
    return fs._uri_to_path(uri, ctx=_ctx())


@pytest.mark.asyncio
async def test_normal_file_skips_stat(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"hello"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, ctx=_ctx()) == "hello"
    assert fake.read_calls == 1
    assert fake.stat_calls == 0


@pytest.mark.asyncio
async def test_empty_file_returns_empty(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/empty.txt"
    fake.files[_path(fs, uri)] = b""
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, ctx=_ctx()) == ""
    assert fake.read_calls == 1
    assert fake.stat_calls == 1


@pytest.mark.asyncio
async def test_directory_raises_invalid_argument(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/folder"
    fake.dirs.add(_path(fs, uri))
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    with pytest.raises(InvalidArgumentError):
        await fs.read_file(uri, ctx=_ctx())


@pytest.mark.asyncio
async def test_missing_file_raises_not_found(monkeypatch):
    fs, _ = _make_fs()
    uri = "viking://resources/missing.txt"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    with pytest.raises(NotFoundError):
        await fs.read_file(uri, ctx=_ctx())


@pytest.mark.asyncio
async def test_offset_and_limit_still_slice(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/multi.txt"
    fake.files[_path(fs, uri)] = b"l0\nl1\nl2\nl3\n"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, offset=1, limit=2, ctx=_ctx()) == "l1\nl2\n"


@pytest.mark.asyncio
async def test_read_error_is_still_mapped(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/err.txt"

    async def _boom(*args, **kwargs):
        raise AGFSNotFoundError("gone")

    fake.read = _boom
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    with pytest.raises(NotFoundError):
        await fs.read_file(uri, ctx=_ctx())
