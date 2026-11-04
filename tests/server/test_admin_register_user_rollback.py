# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for compensating a failed user registration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import admin
from openviking_cli.session.user_id import UserIdentifier


def _request(deletion_service) -> SimpleNamespace:
    # require_auth_root_or_admin wraps the route and rejects requests whose
    # app state has no api_key_manager, so the fake state carries one too.
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                api_key_manager=object(),
                deletion_service=deletion_service,
            )
        )
    )


def _ctx() -> RequestContext:
    # Role subclasses str, so Role("root") is the typed Role instance.
    return RequestContext(
        user=UserIdentifier("acc1", "root_user"),
        role=Role("root"),
    )


async def _register_user(monkeypatch, *, initialize_side_effect, deletion_service):
    service = SimpleNamespace(
        initialize_user_directories=AsyncMock(side_effect=initialize_side_effect),
    )
    manager = SimpleNamespace(
        register_user=AsyncMock(return_value="ov-user-key"),
    )
    monkeypatch.setattr(admin, "get_service", lambda: service)
    monkeypatch.setattr(admin, "_get_api_key_manager", lambda request: manager)
    body = admin.RegisterUserRequest(user_id="bob")
    return await admin.register_user(body, _request(deletion_service), "acc1", _ctx())


async def test_register_user_rolls_back_committed_identity_when_directories_fail(
    monkeypatch,
):
    deletion_service = SimpleNamespace(delete=AsyncMock())

    with pytest.raises(OSError):
        await _register_user(
            monkeypatch,
            initialize_side_effect=OSError("workspace init failed"),
            deletion_service=deletion_service,
        )

    # register_user commits the identity before the workspace steps run, so a
    # failure there must revoke it instead of leaving a half-initialized user.
    deletion_service.delete.assert_awaited_once_with(
        "acc1", "bob", actor=_ctx()
    )


async def test_register_user_rollback_failure_does_not_mask_original_error(
    monkeypatch,
):
    deletion_service = SimpleNamespace(
        delete=AsyncMock(side_effect=RuntimeError("deletion queue unavailable"))
    )

    with pytest.raises(OSError, match="workspace init failed"):
        await _register_user(
            monkeypatch,
            initialize_side_effect=OSError("workspace init failed"),
            deletion_service=deletion_service,
        )
