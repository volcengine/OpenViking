# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, Mock

import pytest

import openviking.service.session_service as session_service_module
from openviking.metrics.datasources.session import SessionLifecycleDataSource
from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


def _make_ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acme", "alice"),
        role=Role.ADMIN,
    )


@pytest.mark.asyncio
async def test_create_keeps_working_when_lifecycle_metrics_fail(monkeypatch: pytest.MonkeyPatch):
    service = SessionService()
    ctx = _make_ctx()
    session = Mock()
    session.exists = AsyncMock(return_value=False)
    session.ensure_exists = AsyncMock()
    debug = Mock()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("metrics failed")

    monkeypatch.setattr(service, "session", Mock(return_value=session))
    monkeypatch.setattr(SessionLifecycleDataSource, "record_lifecycle", staticmethod(_boom))
    monkeypatch.setattr(session_service_module.logger, "debug", debug)

    result = await service.create(ctx, "sess-1")

    assert result is session
    session.exists.assert_awaited_once()
    session.ensure_exists.assert_awaited_once()
    assert debug.call_count == 2


@pytest.mark.asyncio
async def test_commit_async_keeps_working_when_session_metrics_fail(
    monkeypatch: pytest.MonkeyPatch,
):
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()
    session = Mock()
    session.commit_async = AsyncMock(return_value={"status": "queued", "archived": False})
    debug = Mock()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("metrics failed")

    monkeypatch.setattr(service, "get", AsyncMock(return_value=session))
    monkeypatch.setattr(SessionLifecycleDataSource, "record_lifecycle", staticmethod(_boom))
    monkeypatch.setattr(SessionLifecycleDataSource, "record_archive", staticmethod(_boom))
    monkeypatch.setattr(session_service_module.logger, "debug", debug)

    result = await service.commit_async("sess-1", ctx)

    assert result == {"status": "queued", "archived": False}
    session.commit_async.assert_awaited_once()
    assert debug.call_count == 2


@pytest.mark.asyncio
async def test_sessions_returns_empty_and_logs_when_storage_listing_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()
    debug = Mock()

    service._viking_fs.ls = AsyncMock(side_effect=RuntimeError("ls failed"))
    monkeypatch.setattr(session_service_module.logger, "debug", debug)

    result = await service.sessions(ctx)

    assert result == []
    assert debug.call_count == 2


@pytest.mark.asyncio
async def test_sessions_merges_canonical_and_legacy_session_scope():
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()

    async def _ls(uri, ctx):
        if uri == "viking://user/alice/sessions":
            return [
                {"name": "duplicate", "isDir": True},
                {"name": "new-session", "isDir": True},
            ]
        if uri == "viking://session":
            return [
                {"name": "duplicate", "uri": "viking://session/duplicate", "isDir": True},
                {"name": "legacy-session", "uri": "viking://session/legacy-session", "isDir": True},
            ]
        raise AssertionError(uri)

    service._viking_fs.ls = AsyncMock(side_effect=_ls)

    result = await service.sessions(ctx)

    assert result == [
        {
            "session_id": "duplicate",
            "uri": "viking://user/alice/sessions/duplicate",
            "is_dir": True,
        },
        {
            "session_id": "new-session",
            "uri": "viking://user/alice/sessions/new-session",
            "is_dir": True,
        },
        {
            "session_id": "legacy-session",
            "uri": "viking://session/legacy-session",
            "is_dir": True,
        },
    ]


@pytest.mark.asyncio
async def test_get_falls_back_to_legacy_session_scope(monkeypatch: pytest.MonkeyPatch):
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()
    canonical = Mock()
    canonical.exists = AsyncMock(return_value=False)
    canonical.load = AsyncMock()
    legacy = Mock()
    legacy.exists = AsyncMock(return_value=True)
    legacy.load = AsyncMock()

    def _session(_ctx, session_id, *, session_uri=None):
        assert session_id == "legacy-session"
        return legacy if session_uri == "viking://session/legacy-session" else canonical

    monkeypatch.setattr(service, "session", Mock(side_effect=_session))

    result = await service.get("legacy-session", ctx)

    assert result is legacy
    canonical.load.assert_not_awaited()
    legacy.load.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_auto_create_ignores_legacy_session_scope(monkeypatch: pytest.MonkeyPatch):
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()
    canonical = Mock()
    canonical.exists = AsyncMock(return_value=False)
    canonical.ensure_exists = AsyncMock()
    canonical.load = AsyncMock()
    legacy = Mock()
    legacy.exists = AsyncMock(return_value=True)
    legacy.load = AsyncMock()

    def _session(_ctx, session_id, *, session_uri=None):
        assert session_id == "legacy-session"
        return legacy if session_uri == "viking://session/legacy-session" else canonical

    monkeypatch.setattr(service, "session", Mock(side_effect=_session))

    result = await service.get("legacy-session", ctx, auto_create=True)

    assert result is canonical
    canonical.ensure_exists.assert_awaited_once()
    canonical.load.assert_awaited_once()
    legacy.exists.assert_not_awaited()
    legacy.load.assert_not_awaited()


@pytest.mark.asyncio
async def test_writable_session_does_not_fall_back_to_legacy_scope(
    monkeypatch: pytest.MonkeyPatch,
):
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()
    canonical = Mock()
    canonical.exists = AsyncMock(return_value=False)
    canonical.load = AsyncMock()
    legacy = Mock()
    legacy.exists = AsyncMock(return_value=True)
    legacy.load = AsyncMock()

    def _session(_ctx, session_id, *, session_uri=None):
        assert session_id == "legacy-session"
        return legacy if session_uri == "viking://session/legacy-session" else canonical

    monkeypatch.setattr(service, "session", Mock(side_effect=_session))

    with pytest.raises(NotFoundError):
        await service.get_writable("legacy-session", ctx)

    canonical.load.assert_not_awaited()
    legacy.exists.assert_not_awaited()
    legacy.load.assert_not_awaited()
