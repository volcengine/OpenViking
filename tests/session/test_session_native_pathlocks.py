# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Session file writes must respect native RAGFS lease coverage."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openviking.message import TextPart
from openviking.server.auth import get_session_request_context
from openviking.server.routers import sessions as sessions_router
from openviking.service.session_service import SessionService
from openviking.service.task_work_index import bind_task_context
from openviking.session.session import Session
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.viking_fs import VikingFS
from openviking.ttl import wiring
from openviking.ttl.runtime import DirectoryTTL
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.ttl_config import TTLConfig


@pytest.fixture(params=["unconfigured", "account_disabled", "enabled"])
def native_session(request, tmp_path, monkeypatch, request_context):
    fs = VikingFS(
        agfs=create_agfs_client(
            RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
        )
    )
    monkeypatch.setattr(wiring, "_adapter", None)
    if request.param != "unconfigured":
        adapter = DirectoryTTL(resource_id="session-lease-test")
        adapter.fs = fs
        adapter.policies = AsyncMock(
            return_value=(
                TTLConfig(sessions=7),
                TTLConfig(sessions="disabled") if request.param == "account_disabled" else None,
            )
        )
        adapter.client = AsyncMock(side_effect=AssertionError("request path accessed Redis"))
        wiring.set_ttl_adapter(adapter)
        wiring.install_ttl_hook(fs, adapter)
    return Session(viking_fs=fs, ctx=request_context, session_id="native-lease-test")


@pytest.fixture
def commit_queue(monkeypatch):
    queue = SimpleNamespace(enqueue=AsyncMock())
    tracker = SimpleNamespace(
        create=AsyncMock(), fail=AsyncMock(), has_work=AsyncMock(return_value=True)
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: queue)
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    return queue


async def test_create_and_commit_session_http_with_native_file_locks(
    native_session, commit_queue, monkeypatch
):
    session = native_session
    service = SimpleNamespace(
        sessions=SessionService(viking_fs=session._viking_fs),
        initialize_user_directories=AsyncMock(),
    )
    monkeypatch.setattr(sessions_router, "get_service", lambda: service)
    app = FastAPI()
    app.include_router(sessions_router.router)
    app.dependency_overrides[get_session_request_context] = lambda: session.ctx
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/v1/sessions", json={"session_id": session.session_id})
        assert response.status_code == 200, response.text
        response = await client.post(
            f"/api/v1/sessions/{session.session_id}/messages",
            json={"role": "user", "content": "commit through HTTP"},
        )
        assert response.status_code == 200, response.text
        response = await client.post(f"/api/v1/sessions/{session.session_id}/commit", json={})
        assert response.status_code == 200, response.text
        assert response.json()["result"]["status"] == "accepted"
    commit_queue.enqueue.assert_awaited_once()
    await session.load()
    assert session.meta.session_id == session.session_id
    assert (
        await session._viking_fs.read_file(f"{session.uri}/messages.jsonl", ctx=session.ctx) == ""
    )


async def test_commit_metadata_with_native_file_lock(native_session):
    session = native_session
    fs = session._viking_fs
    # Seed directly so this test isolates the Phase 2 write from session creation.
    await fs.write_file(f"{session.uri}/messages.jsonl", "", ctx=session.ctx)
    await fs.write_file(
        f"{session.uri}/.meta.json", json.dumps(session.meta.to_dict()), ctx=session.ctx
    )
    await fs.mkdir(f"{session.uri}/history/archive_001", ctx=session.ctx)

    saved = await session._merge_and_save_commit_meta(1, {"events": 1}, None)

    assert saved is not None
    meta = json.loads(await fs.read_file(f"{session.uri}/.meta.json", ctx=session.ctx))
    assert meta["commit_count"] == 1
    assert meta["memories_extracted"]["events"] == 1
    assert meta["memories_extracted"]["total"] == 1


@pytest.fixture
async def commit_session(native_session, commit_queue):
    session = native_session
    session.meta.memory_policy = {
        "working_memory": {"enabled": False},
        "self": {"enabled": False},
        "peer": {"enabled": False},
    }
    await session.ensure_exists()
    return session, commit_queue


@pytest.mark.parametrize("mode", ["archive", "empty", "retain", "reset"])
async def test_commit_phase1_with_native_file_locks(commit_session, mode):
    session, queue = commit_session
    if mode != "empty":
        await session.add_message_async("user", [TextPart(text="commit under TTL")])

    result = await session.commit_async(
        keep_recent_count=1 if mode == "retain" else 0,
        reset_context=mode == "reset",
        record_idle_commit_success=True,
    )

    assert result["status"] == ("accepted" if mode in ("archive", "reset") else "skipped")
    if result["archived"]:
        assert (await session._read_phase1_meta(result["archive_uri"]))["status"] == "ready"
        archived = await session._archives.read_messages(result["archive_uri"])
        assert [message.content for message in archived] == ["commit under TTL"]
        queue.enqueue.assert_awaited_once()
    else:
        queue.enqueue.assert_not_awaited()
    if mode == "reset":
        assert await session._archives.is_context_reset_archive(
            f"{session.uri}/history/archive_002"
        )
    live = await session._read_live_messages_strict()
    assert [message.content for message in live] == (
        ["commit under TTL"] if mode == "retain" else []
    )
    # Commit must release its owner before a later request acquires the same lock.
    await session.add_message_async("user", [TextPart(text="after commit")])


async def test_commit_failure_preserves_marker_and_releases_lock(commit_session, monkeypatch):
    session, queue = commit_session
    await session.add_message_async("user", [TextPart(text="still live")])
    monkeypatch.setattr(
        session, "_write_to_agfs_async", AsyncMock(side_effect=OSError("root write failed"))
    )
    with pytest.raises(OSError, match="root write failed"):
        await session.commit_async()
    failed = json.loads(
        await session._viking_fs.read_file(
            f"{session.uri}/history/archive_001/.failed.json", ctx=session.ctx
        )
    )
    assert failed["error"] == "root write failed"
    queue.enqueue.assert_not_awaited()
    await session.add_message_async("user", [TextPart(text="after failure")])
    assert [message.content for message in session.messages] == ["still live", "after failure"]


@pytest.mark.parametrize("root_applied", [False, True])
async def test_phase1_recovery_with_native_locks(commit_session, root_applied):
    session, _ = commit_session
    fs = session._viking_fs
    await session.add_message_async("user", [TextPart(text="recover commit")])
    result = await session.commit_async()
    archive = result["archive_uri"]
    meta = json.loads(await fs.read_file(archive + "/.meta.json", ctx=session.ctx))
    meta["phase1"]["status"] = "preparing"
    await fs.write_file(archive + "/.meta.json", json.dumps(meta), ctx=session.ctx)
    if not root_applied:
        raw = await fs.read_file(archive + "/messages.jsonl", ctx=session.ctx)
        await fs.write_file(session.uri + "/messages.jsonl", raw, ctx=session.ctx)

    recovering = Session(viking_fs=fs, ctx=session.ctx, session_id=session.session_id)
    with bind_task_context(
        result["task_id"], session.ctx.account_id, session.ctx.user.user_id, work_id="recovery"
    ):
        assert await recovering._ensure_phase1_ready(archive) is root_applied
    if root_applied:
        assert (await recovering._read_phase1_meta(archive))["status"] == "ready"
        assert recovering.meta.commit_count == 1
    else:
        failed = json.loads(await fs.read_file(archive + "/.failed.json", ctx=session.ctx))
        assert failed["stage"] == "phase1_recovery"
    await recovering.add_message_async("user", [TextPart(text="after recovery")])


@pytest.mark.parametrize("native_session", ["enabled"], indirect=True)
async def test_commit_owner_does_not_leak_to_other_requests_or_late_children(native_session):
    session = native_session
    fs = session._viking_fs
    await session.ensure_exists()

    async def write_after(gate):
        await gate.wait()
        await fs.write_file(session.uri + "/.meta.json", "{}", ctx=session.ctx)

    while_held, after_exit = asyncio.Event(), asyncio.Event()
    independent = asyncio.create_task(write_after(while_held))
    async with session._state_lock():
        while_held.set()
        with pytest.raises(LockAcquisitionError):
            await independent
        inherited = asyncio.create_task(write_after(after_exit))

    # A child retains its copied Context, but cannot borrow a released lease.
    async with session._state_lock():
        after_exit.set()
        with pytest.raises(LockAcquisitionError):
            await inherited
