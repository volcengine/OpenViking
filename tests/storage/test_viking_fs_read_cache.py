# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the VikingFS read-path cache and the content read fast path."""

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import Response as FastAPIResponse

from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.server.identity import RequestContext, Role
from openviking.server.models import Response as ServerResponse
from openviking.server.routers import content as content_router
from openviking.storage.viking_fs import VikingFS
from openviking.storage.viking_fs._read_cache import CACHE as READ_CACHE
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class FakeAsyncAGFS:
    """Minimal in-memory stand-in for AsyncAGFSClient."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.read_calls = 0
        self.stat_calls = 0
        self.write_calls = 0

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

    async def write(self, path: str, data, *args, **kwargs) -> str:
        self.write_calls += 1
        self.files[path] = data if isinstance(data, bytes) else data.encode("utf-8")
        return path

    async def ensure_parent_dirs(self, path: str, *args, **kwargs) -> None:
        return None

    async def mkdir(self, path: str, *args, **kwargs) -> None:
        self.dirs.add(path)

    async def pathlock_acquire_exact(self, path: str, *args, **kwargs) -> dict:
        return {"lease_ref": f"lease:{path}"}

    async def pathlock_acquire_tree(self, path: str, *args, **kwargs) -> dict:
        return {"lease_ref": f"lease:{path}"}

    async def pathlock_release(self, lease, *args, **kwargs) -> None:
        return None

    async def rm(self, path: str, *args, **kwargs):
        self.files.pop(path, None)
        self.dirs.discard(path)
        return {}


def _ctx(user_id: str = "default") -> RequestContext:
    return RequestContext(user=UserIdentifier("default", user_id), role=Role.ROOT)


def _make_fs() -> tuple[VikingFS, FakeAsyncAGFS]:
    fs = VikingFS(agfs=object())
    fake = FakeAsyncAGFS()
    fs._async_agfs = fake
    return fs, fake


def _path(fs: VikingFS, uri: str) -> str:
    return fs._uri_to_path(uri, ctx=_ctx())


@pytest.fixture(autouse=True)
def _reset_read_cache():
    READ_CACHE.clear()
    READ_CACHE.reset_stats()
    yield
    READ_CACHE.clear()
    READ_CACHE.reset_stats()


@pytest.mark.asyncio
async def test_second_read_hits_cache(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"hello\nworld\n"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, ctx=_ctx()) == "hello\nworld\n"
    assert await fs.read_file(uri, ctx=_ctx()) == "hello\nworld\n"

    assert fake.read_calls == 1
    assert READ_CACHE.hits == 1


@pytest.mark.asyncio
async def test_cache_hit_still_runs_authorization(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"cached"
    ensure_access = AsyncMock()
    monkeypatch.setattr(fs, "_ensure_access", ensure_access)

    await fs.read_file(uri, ctx=_ctx())
    await fs.read_file(uri, ctx=_ctx())

    assert ensure_access.await_count == 2
    assert fake.read_calls == 1


@pytest.mark.asyncio
async def test_write_file_invalidates_cache(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"before"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, ctx=_ctx()) == "before"
    await fs.write_file(uri, "after", ctx=_ctx())

    assert await fs.read_file(uri, ctx=_ctx()) == "after"
    assert fake.read_calls == 2


@pytest.mark.asyncio
async def test_append_file_invalidates_cache(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"hello"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, ctx=_ctx()) == "hello"
    await fs.append_file(uri, " world", ctx=_ctx())
    calls_after_append = fake.read_calls

    assert await fs.read_file(uri, ctx=_ctx()) == "hello world"
    assert fake.read_calls == calls_after_append + 1


@pytest.mark.asyncio
async def test_rm_invalidates_cache(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"gone"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, ctx=_ctx()) == "gone"
    await fs.rm(uri, ctx=_ctx(), no_vector=True)

    with pytest.raises(NotFoundError):
        await fs.read_file(uri, ctx=_ctx())


@pytest.mark.asyncio
async def test_offset_limit_matches_between_cache_paths(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"a\nb\nc\nd\n"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    await fs.read_file(uri, ctx=_ctx())
    hit = await fs.read_file(uri, ctx=_ctx(), offset=1, limit=2)
    READ_CACHE.clear()
    miss = await fs.read_file(uri, ctx=_ctx(), offset=1, limit=2)

    assert hit == miss == "b\nc\n"
    assert fake.read_calls == 2


@pytest.mark.asyncio
async def test_cache_is_isolated_per_user(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"shared"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())

    assert await fs.read_file(uri, ctx=_ctx("alice")) == "shared"
    assert await fs.read_file(uri, ctx=_ctx("bob")) == "shared"

    assert fake.read_calls == 2


@pytest.mark.asyncio
async def test_cache_can_be_disabled_by_env(monkeypatch):
    fs, fake = _make_fs()
    uri = "viking://resources/notes.txt"
    fake.files[_path(fs, uri)] = b"uncached"
    monkeypatch.setattr(fs, "_ensure_access", AsyncMock())
    monkeypatch.setenv("OPENVIKING_CONTENT_CACHE", "0")

    assert await fs.read_file(uri, ctx=_ctx()) == "uncached"
    assert await fs.read_file(uri, ctx=_ctx()) == "uncached"

    assert fake.read_calls == 2
    assert len(READ_CACHE) == 0


class _FakeFs:
    def __init__(self, value: str) -> None:
        self.value = value

    async def read(self, uri, ctx, offset, limit):
        return self.value

    async def read_visible(self, uri, ctx, offset, limit):
        return self.value


class _FakeMcps:
    async def read_live_context(self, result, ctx):  # noqa: ANN001, ANN201
        return None


class _FakeService:
    def __init__(self, value: str) -> None:
        self.fs = _FakeFs(value)
        self.mcps = _FakeMcps()


@pytest.mark.asyncio
async def test_read_fast_json_matches_native_response(monkeypatch):
    value = "line one\nline two"
    monkeypatch.setattr(content_router, "get_service", lambda: _FakeService(value))
    # A 32-hex vector record id skips request URI validation.
    uri = "0123456789abcdef0123456789abcdef"
    ctx = _ctx()

    monkeypatch.delenv("OPENVIKING_READ_FAST_JSON", raising=False)
    fast = await content_router.read(uri=uri, offset=0, limit=-1, raw=False, _ctx=ctx)
    assert isinstance(fast, FastAPIResponse)
    payload = json.loads(fast.body)

    monkeypatch.setenv("OPENVIKING_READ_FAST_JSON", "0")
    native = await content_router.read(uri=uri, offset=0, limit=-1, raw=False, _ctx=ctx)
    assert isinstance(native, ServerResponse)

    assert native.status == payload["status"]
    assert native.result == payload["result"]
    assert native.model_dump(exclude_none=True) == payload
