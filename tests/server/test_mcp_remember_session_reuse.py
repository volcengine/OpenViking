# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""`remember` must reuse one resident session instead of one per call.

A session directory survives its own commit: the live ``messages.jsonl`` is
rewritten empty and the content moves to ``history/archive_NNN``. Minting a
fresh session id per call therefore left a permanently near-empty directory
behind for every remembered fact.

These tests stub the service and the identity contextvar directly so they do
not need a server configuration file.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.identity import RequestContext, Role
from openviking.server.mcp_endpoint import StoreMessage, remember
from openviking_cli.session.user_id import UserIdentifier


class _FakeSession:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def add_message_async(self, role, parts, peer_id=None, created_at=None):
        self.messages.append((role, parts[0].text))


@pytest.fixture
def fake_service(monkeypatch):
    """A service whose session store records every id it is asked for."""
    sessions: dict[str, _FakeSession] = {}
    requested: list[str] = []
    committed: list[str] = []

    async def _get(session_id, ctx, *args, **kwargs):
        requested.append(session_id)
        return sessions.setdefault(session_id, _FakeSession())

    async def _commit(session_id, ctx, *args, **kwargs):
        committed.append(session_id)
        return {}

    service = SimpleNamespace(
        sessions=SimpleNamespace(get=_get, commit_async=_commit),
    )
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)

    ctx = RequestContext(
        user=UserIdentifier("acct", "alice"),
        role=Role.USER,
    )
    token = mcp_endpoint._mcp_ctx.set(ctx)
    yield SimpleNamespace(
        sessions=sessions, requested=requested, committed=committed
    )
    mcp_endpoint._mcp_ctx.reset(token)


@pytest.mark.asyncio
async def test_every_call_reuses_the_same_session(fake_service):
    """Two remembered facts must not create two session directories."""
    await remember(messages=[StoreMessage(role="user", content="first fact")])
    await remember(messages=[StoreMessage(role="user", content="second fact")])

    assert fake_service.requested == [
        mcp_endpoint.MEMORY_STORE_SESSION_ID,
        mcp_endpoint.MEMORY_STORE_SESSION_ID,
    ]
    assert len(fake_service.sessions) == 1


@pytest.mark.asyncio
async def test_the_session_id_is_stable_and_not_random(fake_service):
    """A uuid-suffixed id is what created the directory-per-fact problem."""
    await remember(messages=[StoreMessage(role="user", content="a fact")])

    (session_id,) = fake_service.requested
    assert session_id == "mcp-store"


@pytest.mark.asyncio
async def test_commit_targets_the_session_that_was_written_to(fake_service):
    """Committing a different id would strand the messages uncommitted."""
    await remember(messages=[StoreMessage(role="user", content="a fact")])

    assert fake_service.committed == fake_service.requested


@pytest.mark.asyncio
async def test_content_still_reaches_the_session(fake_service):
    """Reusing the session must not change what gets stored."""
    await remember(
        messages=[
            StoreMessage(role="user", content="remember this"),
            StoreMessage(role="assistant", content="noted"),
            StoreMessage(role="user", content=""),
        ]
    )

    session = fake_service.sessions[mcp_endpoint.MEMORY_STORE_SESSION_ID]
    assert session.messages == [("user", "remember this"), ("assistant", "noted")]
