# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock, Mock

import pytest

import openviking.service.session_service as session_service_module
from openviking.metrics.datasources.session import SessionLifecycleDataSource
from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
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
    assert debug.call_count == 1


@pytest.mark.asyncio
async def test_sessions_uses_canonical_scope_and_relies_on_storage_compatibility():
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()

    async def _ls(uri, ctx, **kwargs):
        assert uri == "viking://user/alice/sessions"
        assert kwargs == {"sort_by": "mtime", "sort_order": "desc"}
        return [
            {"name": "duplicate", "isDir": True, "modTime": "2026-07-13T01:00:00Z"},
            {"name": "new-session", "isDir": True, "modTime": "2026-07-13T02:00:00Z"},
            {
                "name": "legacy-session",
                "isDir": True,
                "modTime": "2026-07-12T01:00:00Z",
            },
        ]

    service._viking_fs.ls = AsyncMock(side_effect=_ls)

    result = await service.sessions(ctx)

    assert result == [
        {
            "session_id": "duplicate",
            "uri": "viking://user/alice/sessions/duplicate",
            "is_dir": True,
            "mod_time": "2026-07-13T01:00:00Z",
        },
        {
            "session_id": "new-session",
            "uri": "viking://user/alice/sessions/new-session",
            "is_dir": True,
            "mod_time": "2026-07-13T02:00:00Z",
        },
        {
            "session_id": "legacy-session",
            "uri": "viking://user/alice/sessions/legacy-session",
            "is_dir": True,
            "mod_time": "2026-07-12T01:00:00Z",
        },
    ]
    service._viking_fs.ls.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_memory_policy_prefers_user_setting_and_falls_back_to_server_default():
    viking_fs = Mock()
    service = SessionService(viking_fs=viking_fs)
    ctx = _make_ctx()
    service.set_default_user_memory_policy({"memory_types": ["profile"]})

    viking_fs.read_file = AsyncMock(side_effect=FileNotFoundError)
    assert await service._get_user_memory_policy(ctx) == {
        "self": {"enabled": True},
        "peer": {"enabled": True},
        "memory_types": ["profile"],
    }

    viking_fs.read_file = AsyncMock(
        return_value=json.dumps({"memory_policy": {"memory_types": ["events"]}})
    )
    assert await service._get_user_memory_policy(ctx) == {
        "self": {"enabled": True},
        "peer": {"enabled": True},
        "memory_types": ["events"],
    }
