# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.session_auto_commit import SessionAutoCommitScheduler
from openviking.service.session_idle_sqlite import SQLiteSessionIdleIndex
from openviking.service.session_service import SessionService
from openviking.session.session import SessionMeta
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.memory_config import SessionAutoCommitConfig
from tests.unit.session.test_event_tag_concurrency import _MetaVikingFS


@pytest_asyncio.fixture
async def index(tmp_path):
    store = SQLiteSessionIdleIndex(str(tmp_path / "idle.db"))
    await store.start()
    try:
        yield store
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_persistence_tenant_identity_and_due_boundary(tmp_path):
    path = str(tmp_path / "idle.db")
    first = SQLiteSessionIdleIndex(path)
    try:
        for account, user in [("a", "u"), ("a", "v"), ("b", "u")]:
            await first.register(account, user, "same-session", 100)
        assert await first.due(99) == []
    finally:
        await first.close()
    second = SQLiteSessionIdleIndex(path)
    try:
        rows = await second.due(100)
        assert [row.key for row in rows] == [
            ("a", "u", "same-session"),
            ("a", "v", "same-session"),
            ("b", "u", "same-session"),
        ]
        await second.remove(rows[0])
        assert len(await second.due(100)) == 2
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_stale_scan_cannot_delete_or_defer_new_registration(index, tmp_path):
    other = SQLiteSessionIdleIndex(str(tmp_path / "idle.db"))
    try:
        await index.register("a", "u", "s", 100)
        old = (await index.due(100))[0]
        # Even a repeated registration at the same time gets a new UUID.
        await other.register("a", "u", "s", 100)
        await index.remove(old)
        await index.defer(old, 900)
        fresh = (await other.due(100))[0]
        assert fresh.registration_version != old.registration_version
        await index.defer(fresh, 200)
        assert await other.due(199) == []
        await index.remove((await other.due(200))[0])
        assert await other.due(1000) == []
    finally:
        await other.close()


def _meta(*, timeout=10, messages=1, last_message_at=None):
    return SessionMeta(
        session_id="s",
        auto_commit_policy=None if timeout is None else {"idle_timeout_seconds": timeout},
        message_count=messages,
        last_message_at=last_message_at
        or (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
    )


@pytest.mark.asyncio
async def test_busy_candidates_do_not_starve_later_pages(index):
    get = AsyncMock(return_value=SimpleNamespace(meta=_meta()))
    schedule = AsyncMock(return_value=False)
    service = SimpleNamespace(get=get, maybe_schedule_auto_commit=schedule)
    for i in range(105):
        await index.register("a", "u", f"s{i:03}", 0)
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(enabled=True, scan_rate_limit_files_per_second=0),
        idle_index=index,
    )
    await scheduler._scan_once()
    assert get.await_count == schedule.await_count == 105
    assert await index.due(int(datetime.now(timezone.utc).timestamp() * 1000)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["deleted", "empty", "disabled", "error", "scheduled", "active"])
async def test_candidate_cleanup_and_retry_use_authoritative_state(index, state):
    meta = _meta(
        timeout=None if state == "disabled" else 10,
        messages=0 if state == "empty" else 1,
        last_message_at=datetime.now(timezone.utc).isoformat() if state == "active" else None,
    )
    session = SimpleNamespace(meta=meta)
    get = AsyncMock(return_value=session)
    if state == "deleted":
        get.side_effect = NotFoundError("s", "session")
    elif state == "error":
        get.side_effect = RuntimeError("storage unavailable")
    schedule = AsyncMock(return_value=True)
    service = SimpleNamespace(get=get, maybe_schedule_auto_commit=schedule)
    await index.register("a", "u", "s", 0)
    old = (await index.due(0))[0]
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(enabled=True, scan_rate_limit_files_per_second=0),
        idle_index=index,
    )
    await scheduler._scan_once()
    rows = await index.due(2**62)
    if state in {"deleted", "empty", "disabled"}:
        assert rows == []
        schedule.assert_not_awaited()
    else:
        assert len(rows) == 1
        assert rows[0].registration_version == old.registration_version
        assert rows[0].next_check_at_ms > 0
        if state != "error":
            schedule.assert_awaited_once()
            assert schedule.call_args.kwargs["session"] is session
    ctx = get.call_args.args[1]
    assert (ctx.account_id, ctx.user.user_id) == ("a", "u")


def _session_with_index(index, monkeypatch, meta):
    uri = "viking://user/u/sessions/s"
    fs = _MetaVikingFS(uri, meta)
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    service = SessionService(viking_fs=fs)
    service.set_session_idle_index(index)
    session = service.session(ctx, "s", session_uri=uri)
    assert session._expiry_reporter is index
    monkeypatch.setattr(session, "_rebuild_pending_tokens", AsyncMock())
    original_read = fs.read_file

    async def read_text(uri, ctx=None):
        content = await original_read(uri, ctx=ctx)
        return content.decode("utf-8") if isinstance(content, bytes) else content

    monkeypatch.setattr(fs, "read_file", read_text)
    return session, fs


@pytest.mark.asyncio
async def test_policy_shortening_and_enabling_register_saved_state(index, monkeypatch):
    meta = _meta(timeout=None)
    session, fs = _session_with_index(index, monkeypatch, meta)
    await session.update_config(auto_commit_policy={"idle_timeout_seconds": 1000})
    later = (await index.due(2**62))[0]
    await session.update_config(auto_commit_policy={"idle_timeout_seconds": 5})
    earlier = (await index.due(2**62))[0]
    assert earlier.next_check_at_ms < later.next_check_at_ms
    assert earlier.registration_version != later.registration_version
    assert json.loads(fs.files[fs.meta_uri])["auto_commit_policy"]["idle_timeout_seconds"] == 5
    await session.update_config(auto_commit_policy=None, update_auto_commit_policy=True)
    assert json.loads(fs.files[fs.meta_uri])["auto_commit_policy"] is None
    # Disabling does not prematurely delete a concurrent registration.
    assert (await index.due(2**62))[0] == earlier


@pytest.mark.asyncio
async def test_append_registration_is_ordered_inside_session_lock(index, monkeypatch):
    session, fs = _session_with_index(index, monkeypatch, _meta())
    second, _ = _session_with_index(index, monkeypatch, _meta())
    second._viking_fs = fs
    lock = asyncio.Lock()
    entered = asyncio.Event()
    release = asyncio.Event()
    original_report = index.report_after_append
    snapshots = []

    @asynccontextmanager
    async def state_lock(*args):
        async with lock:
            yield "lease"

    async def delayed_report(snapshot):
        assert lock.locked()
        snapshots.append(snapshot)
        # .meta.json and messages are already persisted when registration begins.
        assert json.loads(fs.files[fs.meta_uri])["last_message_at"] == snapshot.last_message_at
        assert fs.files[f"{session._session_uri}/messages.jsonl"]
        if len(snapshots) == 1:
            entered.set()
            await release.wait()
        await original_report(snapshot)

    monkeypatch.setattr(session, "_state_lock", state_lock)
    monkeypatch.setattr(second, "_state_lock", state_lock)
    monkeypatch.setattr(index, "report_after_append", delayed_report)
    msg = Message(id="m", role="user", parts=[TextPart("hello")])
    first_task = asyncio.create_task(session._append_messages_authoritatively([msg]))
    await asyncio.wait_for(entered.wait(), timeout=5)
    second_task = asyncio.create_task(second._append_messages_authoritatively([msg]))
    release.set()
    await asyncio.gather(first_task, second_task)
    assert len(snapshots) == 2
    registration = (await index.due(2**62))[0]
    expected = int(datetime.fromisoformat(snapshots[-1].last_message_at).timestamp() * 1000) + 10000
    assert registration.next_check_at_ms == expected


@pytest.mark.asyncio
async def test_registration_failure_does_not_fail_saved_append(index, monkeypatch):
    session, fs = _session_with_index(index, monkeypatch, _meta())
    monkeypatch.setattr(index, "register", AsyncMock(side_effect=RuntimeError("database busy")))
    await session._append_messages_authoritatively(
        [Message(id="m", role="user", parts=[TextPart("hello")])]
    )
    assert json.loads(fs.files[fs.meta_uri])["message_count"] == 1
    assert fs.files[f"{session._session_uri}/messages.jsonl"]


@pytest.mark.asyncio
async def test_failed_save_does_not_register(index, monkeypatch):
    session, fs = _session_with_index(index, monkeypatch, _meta())
    monkeypatch.setattr(fs, "write_file", AsyncMock(side_effect=RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        await session._append_messages_authoritatively(
            [Message(id="m", role="user", parts=[TextPart("hello")])]
        )
    assert await index.due(2**62) == []


@pytest.mark.asyncio
async def test_existing_reporter_remains_outside_lock_and_policy_updates(monkeypatch):
    reporter = SimpleNamespace(report_after_append=AsyncMock(), record_report_failure=AsyncMock())
    session, fs = _session_with_index(reporter, monkeypatch, _meta())

    async def report(snapshot):
        assert fs._async_agfs.acquired == fs._async_agfs.released

    reporter.report_after_append.side_effect = report
    await session._append_messages_authoritatively(
        [Message(id="m", role="user", parts=[TextPart("hello")])]
    )
    reporter.report_after_append.assert_awaited_once()
    await session.update_config(auto_commit_policy={"idle_timeout_seconds": 5})
    reporter.report_after_append.assert_awaited_once()


def test_sqlite_config_is_opt_in():
    assert SessionAutoCommitConfig().idle_backend == "local"
    assert SessionAutoCommitConfig(idle_backend="sqlite").idle_backend == "sqlite"


@pytest.mark.asyncio
async def test_database_busy_failure_preserves_messages_and_next_report_recovers(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "busy.db")
    index = SQLiteSessionIdleIndex(path, busy_timeout_ms=10)
    await index.start()
    blocker = sqlite3.connect(path, isolation_level=None)
    session, fs = _session_with_index(index, monkeypatch, _meta())
    try:
        blocker.execute("BEGIN IMMEDIATE")
        await session._append_messages_authoritatively(
            [Message(id="m1", role="user", parts=[TextPart("one")])]
        )
        assert json.loads(fs.files[fs.meta_uri])["message_count"] == 1
        assert await index.due(2**62) == []
        blocker.execute("ROLLBACK")
        await session._append_messages_authoritatively(
            [Message(id="m2", role="user", parts=[TextPart("two")])]
        )
        assert json.loads(fs.files[fs.meta_uri])["message_count"] == 2
        assert len(await index.due(2**62)) == 1
    finally:
        blocker.close()
        await index.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["due", "active", "cooldown"])
async def test_sqlite_candidates_use_existing_auto_commit_decision_and_execution(
    index, monkeypatch, state
):
    service = SessionService()
    service.set_session_auto_commit_config(
        SessionAutoCommitConfig(enabled=True, idle_backend="sqlite")
    )
    meta = _meta()
    if state == "active":
        meta.last_message_at = datetime.now(timezone.utc).isoformat()
    elif state == "cooldown":
        meta.last_auto_commit_at = datetime.now(timezone.utc).isoformat()
        meta.auto_commit_policy["min_commit_interval_seconds"] = 300

    async def commit(**kwargs):
        assert kwargs == {
            "keep_recent_count": 0,
            "persist_keep_recent_count": False,
            "record_auto_commit_success": True,
        }
        meta.message_count = 0

    session = SimpleNamespace(meta=meta, commit_async=AsyncMock(side_effect=commit))
    get = AsyncMock(return_value=session)
    monkeypatch.setattr(service, "get", get)
    monkeypatch.setattr(
        "openviking.service.session_service.get_task_tracker",
        lambda: SimpleNamespace(has_running=AsyncMock(return_value=False)),
    )
    await index.register("a", "u", "s", 0)
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(enabled=True, scan_rate_limit_files_per_second=0),
        idle_index=index,
    )
    await scheduler._scan_once()
    await asyncio.gather(*service._auto_commit_tasks)
    assert session.commit_async.await_count == (1 if state == "due" else 0)
    assert service._auto_commit_inflight == set()
    if state == "due":
        row = (await index.due(2**62))[0]
        await index.defer(row, 0)
        await scheduler._scan_once()
        assert await index.due(2**62) == []


@pytest.mark.asyncio
async def test_prepare_background_initializes_sqlite_without_redis_or_scan(index, monkeypatch):
    # A writer-only process must register even when its scanner role is disabled.
    service = OpenVikingService.__new__(OpenVikingService)
    service._background_prepared = False
    service._shared_initialized = True
    service._session_auto_commit_scheduler = SessionAutoCommitScheduler(
        None, SessionAutoCommitConfig(), idle_index=index
    )
    service._compile_scheduler = None
    service._ttl_adapter = None
    service._config = SimpleNamespace(pdf=SimpleNamespace(strategy="default", mineru_endpoint=None))
    config = SimpleNamespace(
        expiry=SimpleNamespace(validate_for_runtime=lambda: pytest.fail("Redis runtime invoked")),
        memory=SimpleNamespace(
            session_auto_commit=SessionAutoCommitConfig(enabled=True, idle_backend="sqlite")
        ),
    )
    monkeypatch.setattr("openviking.service.core.get_openviking_config", lambda: config)
    monkeypatch.setattr(index, "start", AsyncMock(wraps=index.start))
    await service.prepare_background(queue_worker=False, scheduler=False)
    index.start.assert_awaited_once()
    assert service._background_prepared
