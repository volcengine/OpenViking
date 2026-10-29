# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.core.namespace import canonical_session_uri
from openviking.scheduling.expiry import SESSION_ITEM_TYPE, ItemIdentity
from openviking.server.identity import RequestContext, Role
from openviking.service.session_service import SessionService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture
def session_storage(tmp_path):
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)
    agfs = MockLocalAGFS(root_path=tmp_path)
    vector = SimpleNamespace(count=AsyncMock(), delete_uris=AsyncMock())
    viking_fs = VikingFS(agfs=agfs, vector_store=vector)
    service = SessionService(viking_fs=viking_fs)
    session_id = "delete-session"
    session_uri = canonical_session_uri(ctx, session_id)
    path = viking_fs._uri_to_path(session_uri, ctx=ctx)
    agfs.write(f"{path}/messages.jsonl", "")
    agfs.write(f"{path}/.meta.json", "{}")
    sibling_path = viking_fs._uri_to_path(canonical_session_uri(ctx, "keep-session"), ctx=ctx)
    memory_path = viking_fs._uri_to_path(
        "viking://user/alice/memories/preferences/keep.md", ctx=ctx
    )
    agfs.write(f"{sibling_path}/messages.jsonl", "sibling messages")
    agfs.write(memory_path, "extracted memory")
    agfs.read = Mock(wraps=agfs.read)
    agfs.rm = Mock(wraps=agfs.rm)
    expiry = SimpleNamespace(unregister_after_delete=AsyncMock())
    service._session_expiry_adapter = expiry
    return SimpleNamespace(
        service=service,
        ctx=ctx,
        session_id=session_id,
        path=path,
        sibling_path=sibling_path,
        memory_path=memory_path,
        agfs=agfs,
        vector=vector,
        expiry=expiry,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["messages.jsonl", ".meta.json"])
async def test_delete_does_not_read_or_parse_session_contents(session_storage, filename):
    state = session_storage
    state.agfs.write(f"{state.path}/{filename}", "malformed JSON")

    assert await state.service.delete(state.session_id, state.ctx) is True

    assert not state.agfs.exists(state.path)
    state.agfs.read.assert_not_called()
    state.vector.count.assert_not_awaited()
    state.vector.delete_uris.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_unregisters_expiry_only_after_removing_session(session_storage):
    state = session_storage

    async def unregister(identity):
        assert not state.agfs.exists(state.path)

    state.expiry.unregister_after_delete.side_effect = unregister

    assert await state.service.delete(state.session_id, state.ctx) is True

    state.agfs.rm.assert_called_once()
    state.expiry.unregister_after_delete.assert_awaited_once_with(
        ItemIdentity(SESSION_ITEM_TYPE, "acme", "alice", state.session_id)
    )
    assert state.agfs.read(f"{state.sibling_path}/messages.jsonl") == b"sibling messages"
    assert state.agfs.read(state.memory_path) == b"extracted memory"


@pytest.mark.asyncio
async def test_delete_missing_session_keeps_not_found_semantics(session_storage):
    state = session_storage

    with pytest.raises(NotFoundError, match="missing-session"):
        await state.service.delete("missing-session", state.ctx)

    state.agfs.rm.assert_not_called()
    state.expiry.unregister_after_delete.assert_not_awaited()
    assert state.agfs.exists(state.path)


@pytest.mark.asyncio
async def test_delete_failure_does_not_unregister_expiry(session_storage):
    state = session_storage
    state.agfs.rm.side_effect = OSError("storage delete failed")

    with pytest.raises(OSError, match="storage delete failed"):
        await state.service.delete(state.session_id, state.ctx)

    assert state.agfs.exists(state.path)
    state.expiry.unregister_after_delete.assert_not_awaited()
