# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
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
    viking_fs = VikingFS(agfs=agfs)
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


def _concurrent_delete_service():
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)
    viking_fs = VikingFS(agfs=Mock())
    viking_fs.stat = AsyncMock(return_value={"isDir": True})
    return SessionService(viking_fs=viking_fs), viking_fs, ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_limit", [None, 2])
async def test_concurrent_deletes_wait_before_entering_storage(configured_limit):
    service, viking_fs, ctx = _concurrent_delete_service()
    if configured_limit is not None:
        service.set_delete_concurrency(configured_limit)
    limit = 4 if configured_limit is None else configured_limit
    release = asyncio.Event()

    async def remove(*args, **kwargs):
        await release.wait()

    viking_fs.rm = AsyncMock(side_effect=remove)
    tasks = [asyncio.create_task(service.delete(f"session-{i}", ctx)) for i in range(limit)]
    try:
        await asyncio.sleep(0)
        # Reapplying startup configuration must preserve the same capacity guard.
        service.set_delete_concurrency(limit)
        tasks.extend(asyncio.create_task(service.delete(f"queued-{i}", ctx)) for i in range(2))
        # Every task runs until its first wait: the storage gate or the limiter.
        await asyncio.sleep(0)
        assert viking_fs.stat.await_count == limit
        assert viking_fs.rm.await_count == limit
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)

    assert results == [True] * len(tasks)


@pytest.mark.asyncio
async def test_cancelling_waiting_delete_does_not_leak_capacity():
    service, viking_fs, ctx = _concurrent_delete_service()
    service.set_delete_concurrency(1)
    release = asyncio.Event()

    async def remove(*args, **kwargs):
        await release.wait()

    viking_fs.rm = AsyncMock(side_effect=remove)
    first = asyncio.create_task(service.delete("first", ctx))
    waiting = asyncio.create_task(service.delete("cancelled", ctx))
    successor = asyncio.create_task(service.delete("successor", ctx))
    try:
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert viking_fs.stat.await_count == 1
        assert viking_fs.rm.await_count == 1
    finally:
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first, waiting, successor, return_exceptions=True), timeout=2
        )

    assert results[0] is True
    assert isinstance(results[1], asyncio.CancelledError)
    assert results[2] is True
    assert await asyncio.wait_for(service.delete("after-cancellation", ctx), timeout=2) is True


@pytest.mark.asyncio
async def test_failed_delete_releases_capacity_for_waiting_delete():
    service, viking_fs, ctx = _concurrent_delete_service()
    service.set_delete_concurrency(1)
    release = asyncio.Event()

    async def remove(uri, **kwargs):
        await release.wait()
        if uri.endswith("/failed"):
            raise OSError("storage delete failed")

    viking_fs.rm = AsyncMock(side_effect=remove)
    failed = asyncio.create_task(service.delete("failed", ctx))
    waiting = asyncio.create_task(service.delete("waiting", ctx))
    try:
        await asyncio.sleep(0)
        assert viking_fs.stat.await_count == 1
        assert viking_fs.rm.await_count == 1
    finally:
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(failed, waiting, return_exceptions=True), timeout=2
        )

    assert isinstance(results[0], OSError)
    assert results[1] is True


@pytest.mark.parametrize("limit", [0, -1, 1.5, True, "2"])
def test_delete_concurrency_requires_positive_integer(limit):
    service = SessionService()

    with pytest.raises(ValueError, match="positive integer"):
        service.set_delete_concurrency(limit)
