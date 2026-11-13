# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import sessions as sessions_module
from openviking.server.routers.sessions import AddMessageRequest, BatchAddMessageRequest
from openviking_cli.session.user_id import UserIdentifier


class _Sessions:
    def __init__(self):
        self.open_calls = []

    async def open(self, session_id, _ctx, auto_create=False):
        self.open_calls.append((session_id, auto_create))
        return SimpleNamespace(messages=[])


class _Service:
    def __init__(self):
        self.sessions = _Sessions()


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acme", "alice"), role=Role(Role.USER))


def _invalid_message() -> dict:
    return {"role": "user", "parts": [{"type": "image_url", "image_url": {"url": ""}}]}


async def test_add_message_validates_parts_before_opening_the_session(monkeypatch):
    service = _Service()
    monkeypatch.setattr(sessions_module, "get_service", lambda: service)

    with pytest.raises(HTTPException) as exc_info:
        await sessions_module.add_message(
            AddMessageRequest(**_invalid_message()),
            "missing-session",
            _ctx(),
        )

    assert exc_info.value.status_code == 400
    assert service.sessions.open_calls == []


async def test_batch_add_messages_validates_all_parts_before_opening_the_session(monkeypatch):
    service = _Service()
    monkeypatch.setattr(sessions_module, "get_service", lambda: service)
    request = BatchAddMessageRequest(
        messages=[
            {"role": "user", "content": "valid"},
            _invalid_message(),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        await sessions_module.batch_add_messages(request, "missing-session", _ctx())

    assert exc_info.value.status_code == 400
    assert service.sessions.open_calls == []
