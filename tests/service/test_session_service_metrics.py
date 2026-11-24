# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
from unittest.mock import AsyncMock, Mock

import pytest

import openviking.service.session_service as session_service_module
from openviking.message import TextPart
from openviking.metrics.datasources.session import SessionLifecycleDataSource
from openviking.server.config import UserSettingsPatch
from openviking.server.identity import RequestContext, Role
from openviking.server.user_config import patch_user_config
from openviking.service.session_service import SessionService
from openviking.storage.queuefs import get_queue_manager
from openviking_cli.session.user_id import UserIdentifier


def _make_ctx(user_id: str = "alice") -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", user_id), role=Role.ADMIN)


@pytest.mark.asyncio
async def test_commit_async_keeps_working_when_session_metrics_fail(service, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("metrics failed")

    monkeypatch.setattr(SessionLifecycleDataSource, "record_lifecycle", staticmethod(unavailable))
    monkeypatch.setattr(SessionLifecycleDataSource, "record_archive", staticmethod(unavailable))
    monkeypatch.setattr(get_queue_manager(), "enqueue", AsyncMock(return_value="accepted"))

    ctx = _make_ctx()
    session = await service.sessions.create(ctx, "sess-1")
    await session.add_message_async("user", [TextPart("remember this message")])
    result = await service.sessions.commit_async("sess-1", ctx)

    assert result["archived"] is True
    assert result["session_id"] == "sess-1"
    archived = await service.viking_fs.read_file(f"{result['archive_uri']}/messages.jsonl", ctx=ctx)
    assert json.loads(archived)["parts"][0]["text"] == "remember this message"
    assert await service.viking_fs.read_file(f"{session.uri}/messages.jsonl", ctx=ctx) == ""


@pytest.mark.asyncio
async def test_sessions_returns_empty_and_logs_when_storage_listing_fails(monkeypatch):
    service = SessionService(viking_fs=Mock())
    ctx = _make_ctx()
    debug = Mock()

    service._viking_fs.ls = AsyncMock(side_effect=RuntimeError("ls failed"))
    monkeypatch.setattr(session_service_module.logger, "debug", debug)

    assert await service.sessions(ctx) == []
    debug.assert_called_once_with("Failed to list sessions", exc_info=True)


@pytest.mark.asyncio
async def test_sessions_uses_canonical_scope(service):
    ctx = _make_ctx()
    owned = await service.sessions.create(ctx, "owned")
    await service.sessions.create(_make_ctx("bob"), "foreign")

    result = await service.sessions.sessions(ctx)

    assert [(session["session_id"], session["uri"], session["is_dir"]) for session in result] == [
        ("owned", owned.uri, True)
    ]


@pytest.mark.asyncio
async def test_user_memory_policy_prefers_user_setting_and_falls_back_to_server_default(
    service, monkeypatch
):
    monkeypatch.setattr(get_queue_manager(), "enqueue", AsyncMock(return_value="accepted"))
    sessions = service.sessions
    sessions.set_default_user_memory_policy({"memory_types": ["profile"]})
    ctx = _make_ctx()
    session = await sessions.create(ctx, "policy")

    async def commit_reading_policy():
        await session.add_message_async("user", [TextPart("remember this message")])
        committed = await sessions.commit_async(session.session_id, ctx)
        archive_meta = json.loads(
            await service.viking_fs.read_file(f"{committed['archive_uri']}/.meta.json", ctx=ctx)
        )
        return archive_meta["phase1"]["queue_message"]["memory_policy"]

    assert (await commit_reading_policy())["memory_types"] == ["profile"]
    await patch_user_config(
        service.viking_fs,
        ctx,
        UserSettingsPatch.model_validate({"memory_policy": {"memory_types": ["events"]}}),
    )
    assert (await commit_reading_policy())["memory_types"] == ["events"]
