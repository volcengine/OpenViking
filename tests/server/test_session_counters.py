# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the GET /sessions/{id} archive-derived counter fallback (#1550).

Sessions written through async / out-of-band paths (e.g. the Hermes
provider) materialize archive_NNN/ directories on disk but leave the
persisted .meta.json counter at zero. The read endpoint must surface the
on-disk truth so the CLI does not report "nothing happened".

These tests directly exercise the router handler with a fake service so
they do not require the native vectordb backend.
"""

from types import SimpleNamespace

import pytest

from openviking.server.routers import sessions as sessions_router
from openviking.session.session import SessionMeta
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


def _fake_session(
    *,
    session_id: str = "sess-1",
    message_count: int = 0,
    commit_count: int = 0,
    archive_dir_names=(),
    raise_on_ls: bool = False,
):
    user = UserIdentifier.the_default_user("u1")
    meta = SessionMeta(
        session_id=session_id,
        message_count=message_count,
        commit_count=commit_count,
    )

    async def _ls(uri, ctx=None):
        if raise_on_ls:
            raise FileNotFoundError(uri)
        return [{"name": name, "isDir": True} for name in archive_dir_names]

    viking_fs = SimpleNamespace(ls=_ls)
    return SimpleNamespace(
        session_id=session_id,
        uri=f"viking://session/{session_id}",
        meta=meta,
        user=user,
        ctx=SimpleNamespace(),
        _viking_fs=viking_fs,
    )


def _patch_service(monkeypatch, *, session=None, exc=None):
    async def _get(session_id, ctx, auto_create=False):
        if exc is not None:
            raise exc
        return session

    fake_service = SimpleNamespace(sessions=SimpleNamespace(get=_get))
    monkeypatch.setattr(sessions_router, "get_service", lambda: fake_service)


@pytest.mark.asyncio
async def test_count_on_disk_archives_counts_archive_dirs():
    session = _fake_session(
        archive_dir_names=("archive_001", "archive_002", "summary.md", "extras")
    )
    assert await sessions_router._count_on_disk_archives(session) == 2


@pytest.mark.asyncio
async def test_count_on_disk_archives_returns_zero_on_missing_history():
    session = _fake_session(raise_on_ls=True)
    assert await sessions_router._count_on_disk_archives(session) == 0


@pytest.mark.asyncio
async def test_get_session_overrides_zero_counters_when_archives_exist(monkeypatch):
    """The Hermes-style stale-counter case."""
    session = _fake_session(
        message_count=0,
        commit_count=0,
        archive_dir_names=("archive_001", "archive_002"),
    )
    _patch_service(monkeypatch, session=session)

    response = await sessions_router.get_session(
        session_id="sess-1", auto_create=False, _ctx=SimpleNamespace()
    )
    result = response.result
    assert result["archive_count"] == 2
    assert result["commit_count"] == 2
    assert result["message_count"] == 2


@pytest.mark.asyncio
async def test_get_session_does_not_downgrade_persisted_counters(monkeypatch):
    """Persisted counters > derived value must remain authoritative."""
    session = _fake_session(
        message_count=42,
        commit_count=5,
        archive_dir_names=("archive_001", "archive_002"),
    )
    _patch_service(monkeypatch, session=session)

    response = await sessions_router.get_session(
        session_id="sess-1", auto_create=False, _ctx=SimpleNamespace()
    )
    result = response.result
    assert result["archive_count"] == 2
    assert result["message_count"] == 42
    assert result["commit_count"] == 5


@pytest.mark.asyncio
async def test_get_session_no_archives_keeps_zero(monkeypatch):
    """No archives on disk → archive_count is 0, no override."""
    session = _fake_session(message_count=0, commit_count=0, archive_dir_names=())
    _patch_service(monkeypatch, session=session)

    response = await sessions_router.get_session(
        session_id="sess-1", auto_create=False, _ctx=SimpleNamespace()
    )
    result = response.result
    assert result["archive_count"] == 0
    assert result["message_count"] == 0
    assert result["commit_count"] == 0


@pytest.mark.asyncio
async def test_get_session_response_shape_unchanged(monkeypatch):
    """Existing fields must remain present; only archive_count is added."""
    session = _fake_session(message_count=0, commit_count=0, archive_dir_names=())
    _patch_service(monkeypatch, session=session)

    response = await sessions_router.get_session(
        session_id="sess-1", auto_create=False, _ctx=SimpleNamespace()
    )
    result = response.result
    for required_key in (
        "session_id",
        "uri",
        "user",
        "message_count",
        "commit_count",
        "memories_extracted",
        "llm_token_usage",
        "embedding_token_usage",
        "pending_tokens",
        "archive_count",
    ):
        assert required_key in result, f"missing key {required_key!r}"


@pytest.mark.asyncio
async def test_get_session_not_found_passthrough(monkeypatch):
    """Missing session still returns NOT_FOUND error response."""
    import json as _json

    _patch_service(monkeypatch, exc=NotFoundError("missing"))

    response = await sessions_router.get_session(
        session_id="ghost", auto_create=False, _ctx=SimpleNamespace()
    )
    # error_response returns a starlette JSONResponse with the standard payload.
    body = _json.loads(response.body)
    assert body["status"] == "error"
    assert body["error"]["code"] == "NOT_FOUND"
