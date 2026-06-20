# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import openviking.service.session_auto_commit as auto_commit_module
import openviking.service.session_service as session_service_module
from openviking.server.config import SessionAutoCommitConfig
from openviking.server.identity import RequestContext
from openviking.service.session_auto_commit import SessionAutoCommitScheduler
from openviking.service.session_service import SessionService
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(
        self,
        tree_entries: list[dict[str, str]],
        metas: dict[str, object],
        *,
        users_by_account: dict[str, list[str]] | None = None,
        tree_entries_by_user: dict[tuple[str, str], list[dict[str, str]]] | None = None,
        ls_errors: dict[str, BaseException] | None = None,
        read_delay_seconds: float = 0.0,
    ) -> None:
        self._tree_entries = tree_entries
        self._metas = metas
        self._users_by_account = users_by_account or {"acct_a": ["user_b"]}
        self._tree_entries_by_user = tree_entries_by_user or {}
        self._ls_errors = ls_errors or {}
        self._read_delay_seconds = read_delay_seconds
        self.agfs = self
        self.ls_calls: list[tuple[str, str]] = []
        self.read_calls: list[str] = []
        self.active_reads = 0
        self.max_active_reads = 0
        self._read_lock = threading.Lock()

    def ls(self, path: str, ctx=None):
        account_id = ""
        if isinstance(ctx, dict):
            account_id = str(ctx.get("account_id", ""))
        elif ctx is not None:
            account_id = str(getattr(ctx, "account_id", ""))
        self.ls_calls.append((path, account_id))
        if path in self._ls_errors:
            raise self._ls_errors[path]
        if path == "/local":
            return [{"name": account, "isDir": True} for account in self._users_by_account] + [
                {"name": "_system", "isDir": True},
            ]
        parts = [part for part in path.split("/") if part]
        if len(parts) == 3 and parts[0] == "local" and parts[2] == "user":
            return [
                {"name": user, "isDir": True} for user in self._users_by_account.get(parts[1], [])
            ]
        if (
            len(parts) == 5
            and parts[0] == "local"
            and parts[2] == "user"
            and parts[4] == "sessions"
        ):
            return list(self._tree_entries_by_user.get((parts[1], parts[3]), self._tree_entries))
        raise AssertionError(path)

    def read(self, path: str):
        with self._read_lock:
            self.read_calls.append(path)
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
        try:
            if self._read_delay_seconds:
                time.sleep(self._read_delay_seconds)
            value = self._metas[path]
            if isinstance(value, BaseException):
                raise value
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode("utf-8")
            return json.dumps(value).encode("utf-8")
        finally:
            with self._read_lock:
                self.active_reads -= 1


class _FakeSessionService:
    def __init__(
        self,
        tree_entries: list[dict[str, str]],
        metas: dict[str, object],
        *,
        users_by_account: dict[str, list[str]] | None = None,
        tree_entries_by_user: dict[tuple[str, str], list[dict[str, str]]] | None = None,
        ls_errors: dict[str, BaseException] | None = None,
        read_delay_seconds: float = 0.0,
    ) -> None:
        self.viking_fs = _FakeVikingFS(
            tree_entries,
            metas,
            users_by_account=users_by_account,
            tree_entries_by_user=tree_entries_by_user,
            ls_errors=ls_errors,
            read_delay_seconds=read_delay_seconds,
        )
        self.calls: list[tuple[str, str, str]] = []
        self.schedule_results: list[bool] = []

    async def maybe_schedule_auto_commit(
        self,
        session_id: str,
        ctx: RequestContext,
        *,
        reason_hint: str,
    ):
        self.calls.append((session_id, reason_hint, ctx.user.user_id))
        if self.schedule_results:
            return self.schedule_results.pop(0)
        return True


class _FakeSessionMeta:
    def __init__(
        self,
        *,
        auto_commit_policy: dict,
        pending_tokens: int = 0,
        message_count: int = 0,
        keep_recent_count: int = 0,
        last_message_at: str = "",
    ) -> None:
        self.auto_commit_policy = auto_commit_policy
        self.pending_tokens = pending_tokens
        self.message_count = message_count
        self.keep_recent_count = keep_recent_count
        self.last_message_at = last_message_at
        self.auto_commit_last_error = ""
        self.auto_commit_last_error_at = ""

    def to_dict(self) -> dict:
        return {
            "auto_commit_policy": self.auto_commit_policy,
            "pending_tokens": self.pending_tokens,
            "message_count": self.message_count,
            "keep_recent_count": self.keep_recent_count,
            "last_message_at": self.last_message_at,
        }


class _FakeAutoCommitSession:
    def __init__(self, meta: _FakeSessionMeta) -> None:
        self.meta = meta
        self.commit_calls: list[int] = []
        self.save_calls = 0

    async def commit_async(self, *, keep_recent_count: int = 0):
        self.commit_calls.append(keep_recent_count)
        return {"archived": True}

    async def _save_meta(self):
        self.save_calls += 1


class _FakeTaskTracker:
    async def has_running(self, *args, **kwargs) -> bool:
        return False


def _auto_commit_ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acct_a", user_id="user_b"),
        role="user",
    )


def _session_service_for_auto_commit_test(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeAutoCommitSession,
) -> SessionService:
    service = SessionService()
    service.set_session_auto_commit_config(
        SessionAutoCommitConfig(idle_enabled=True, check_interval_seconds=60.0)
    )

    async def fake_get(session_id, ctx, auto_create=False):
        return session

    monkeypatch.setattr(service, "get", fake_get)
    monkeypatch.setattr(session_service_module, "get_task_tracker", lambda: _FakeTaskTracker())
    return service


def _meta(
    *,
    account_id: str = "acct_a",
    user_id: str = "user_b",
    enabled: bool = True,
    idle_timeout_seconds: int | None = 60,
    pending_tokens: int = 1,
    message_count: int = 1,
    keep_recent_count: int = 0,
    last_message_at: str | None = None,
) -> dict:
    if last_message_at is None:
        last_message_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    policy = {"enabled": enabled, "keep_recent_count": keep_recent_count}
    if idle_timeout_seconds is not None:
        policy["idle_timeout_seconds"] = idle_timeout_seconds
    return {
        "auto_commit_policy": policy,
        "created_by_account_id": account_id,
        "created_by_user_id": user_id,
        "pending_tokens": pending_tokens,
        "message_count": message_count,
        "keep_recent_count": keep_recent_count,
        "last_message_at": last_message_at,
    }


def _session_entry(session_id: str) -> dict[str, object]:
    return {"name": session_id, "isDir": True}


@pytest.mark.asyncio
async def test_run_auto_commit_rechecks_token_threshold_before_committing(monkeypatch):
    session = _FakeAutoCommitSession(
        _FakeSessionMeta(
            auto_commit_policy={
                "enabled": True,
                "token_threshold": 100,
                "keep_recent_count": 0,
            },
            pending_tokens=10,
            message_count=1,
        )
    )
    service = _session_service_for_auto_commit_test(monkeypatch, session)

    await service.run_auto_commit("session_a", _auto_commit_ctx(), reason="token_threshold")

    assert session.commit_calls == []
    assert session.save_calls == 0


@pytest.mark.asyncio
async def test_run_auto_commit_rechecks_idle_timeout_before_committing(monkeypatch):
    session = _FakeAutoCommitSession(
        _FakeSessionMeta(
            auto_commit_policy={
                "enabled": True,
                "idle_timeout_seconds": 300,
                "keep_recent_count": 0,
            },
            pending_tokens=1,
            message_count=1,
            last_message_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    service = _session_service_for_auto_commit_test(monkeypatch, session)

    await service.run_auto_commit("session_a", _auto_commit_ctx(), reason="idle_timeout")

    assert session.commit_calls == []
    assert session.save_calls == 0


@pytest.mark.asyncio
async def test_scheduler_scans_agfs_paths_directly_without_account_user_indices():
    tree_entries = [
        _session_entry("session_due"),
        _session_entry("session_skip"),
    ]
    metas = {
        "/local/acct_a/user/user_b/sessions/session_due/.meta.json": _meta(),
        "/local/acct_a/user/user_b/sessions/session_skip/.meta.json": _meta(
            pending_tokens=0,
            message_count=0,
        ),
    }
    service = _FakeSessionService(tree_entries, metas)
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    await scheduler._scan_once()

    assert service.viking_fs.ls_calls == [
        ("/local", "_system"),
        ("/local/acct_a/user", "acct_a"),
        ("/local/acct_a/user/user_b/sessions", "acct_a"),
    ]
    assert service.viking_fs.read_calls == [
        "/local/acct_a/user/user_b/sessions/session_due/.meta.json",
        "/local/acct_a/user/user_b/sessions/session_skip/.meta.json",
    ]
    assert service.calls == [
        ("session_due", "idle_timeout", "user_b"),
        ("session_skip", "idle_timeout", "user_b"),
    ]


@pytest.mark.asyncio
async def test_scheduler_defers_uncommitted_content_check_to_session_service_for_stale_meta():
    service = _FakeSessionService(
        [_session_entry("session_with_stale_meta")],
        {
            "/local/acct_a/user/user_b/sessions/session_with_stale_meta/.meta.json": _meta(
                pending_tokens=0,
                message_count=0,
            ),
        },
    )
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    await scheduler._scan_once()

    assert service.calls == [("session_with_stale_meta", "idle_timeout", "user_b")]


@pytest.mark.asyncio
async def test_scheduler_logs_scheduled_count_separately_from_due_candidates(caplog):
    service = _FakeSessionService(
        [_session_entry("session_due_1"), _session_entry("session_due_2")],
        {
            "/local/acct_a/user/user_b/sessions/session_due_1/.meta.json": _meta(),
            "/local/acct_a/user/user_b/sessions/session_due_2/.meta.json": _meta(),
        },
    )
    service.schedule_results = [True, False]
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    openviking_logger = logging.getLogger("openviking")
    old_propagate = openviking_logger.propagate
    openviking_logger.propagate = True
    try:
        with caplog.at_level(logging.INFO, logger="openviking.service.session_auto_commit"):
            await scheduler._scan_once()
    finally:
        openviking_logger.propagate = old_propagate

    assert "SessionAutoCommitScheduler scanned=2 due=2 scheduled=1" in caplog.text


@pytest.mark.asyncio
async def test_scheduler_reads_session_meta_with_bounded_concurrency():
    tree_entries = [_session_entry(f"session_{index}") for index in range(6)]
    metas = {
        f"/local/acct_a/user/user_b/sessions/session_{index}/.meta.json": _meta()
        for index in range(6)
    }
    service = _FakeSessionService(tree_entries, metas, read_delay_seconds=0.01)
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(
            idle_enabled=True,
            check_interval_seconds=60.0,
            scan_batch_size=2,
            scan_batch_pause_seconds=0.0,
        ),
        check_interval=60.0,
    )

    await scheduler._scan_once()

    assert service.viking_fs.max_active_reads == 2
    assert len(service.viking_fs.read_calls) == 6
    assert len(service.calls) == 6


@pytest.mark.asyncio
async def test_scheduler_pauses_between_scan_batches():
    tree_entries = [_session_entry(f"session_{index}") for index in range(5)]
    metas = {
        f"/local/acct_a/user/user_b/sessions/session_{index}/.meta.json": _meta()
        for index in range(5)
    }
    service = _FakeSessionService(tree_entries, metas)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(
            idle_enabled=True,
            check_interval_seconds=60.0,
            scan_batch_size=2,
            scan_batch_pause_seconds=0.25,
        ),
        check_interval=60.0,
        sleep=fake_sleep,
    )

    await scheduler._scan_once()

    assert sleep_calls == [0.25, 0.25]


@pytest.mark.asyncio
async def test_scheduler_applies_batch_pause_while_enumerating_sessions():
    users_by_account = {"acct_a": ["user_1", "user_2", "user_3"]}
    tree_entries_by_user = {
        ("acct_a", "user_1"): [_session_entry("session_1")],
        ("acct_a", "user_2"): [_session_entry("session_2")],
        ("acct_a", "user_3"): [_session_entry("session_3")],
    }
    metas = {
        "/local/acct_a/user/user_1/sessions/session_1/.meta.json": _meta(user_id="user_1"),
        "/local/acct_a/user/user_2/sessions/session_2/.meta.json": _meta(user_id="user_2"),
        "/local/acct_a/user/user_3/sessions/session_3/.meta.json": _meta(user_id="user_3"),
    }
    service = _FakeSessionService(
        [],
        metas,
        users_by_account=users_by_account,
        tree_entries_by_user=tree_entries_by_user,
    )
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(
            idle_enabled=True,
            check_interval_seconds=60.0,
            scan_batch_size=2,
            scan_batch_pause_seconds=0.25,
        ),
        check_interval=60.0,
        sleep=fake_sleep,
    )

    await scheduler._scan_once()

    assert sleep_calls == [0.25]
    assert service.calls == [
        ("session_1", "idle_timeout", "user_1"),
        ("session_2", "idle_timeout", "user_2"),
        ("session_3", "idle_timeout", "user_3"),
    ]


@pytest.mark.asyncio
async def test_scheduler_does_not_warn_for_missing_meta(caplog):
    service = _FakeSessionService(
        [_session_entry("deleted_session")],
        {
            "/local/acct_a/user/user_b/sessions/deleted_session/.meta.json": FileNotFoundError(
                "missing"
            )
        },
    )
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    await scheduler._scan_once()

    assert not [record for record in caplog.records if record.levelname == "WARNING"]
    assert service.calls == []


@pytest.mark.asyncio
async def test_scheduler_warns_for_invalid_meta_json(caplog):
    service = _FakeSessionService(
        [_session_entry("bad_session")],
        {
            "/local/acct_a/user/user_b/sessions/bad_session/.meta.json": "{not-json",
        },
    )
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    openviking_logger = logging.getLogger("openviking")
    old_propagate = openviking_logger.propagate
    openviking_logger.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="openviking.service.session_auto_commit"):
            await scheduler._scan_once()
    finally:
        openviking_logger.propagate = old_propagate

    assert "Invalid session meta JSON for idle auto-commit" in caplog.text
    assert service.calls == []


@pytest.mark.asyncio
async def test_scheduler_skips_non_object_meta_without_aborting_batch(caplog):
    service = _FakeSessionService(
        [_session_entry("bad_session"), _session_entry("good_session")],
        {
            "/local/acct_a/user/user_b/sessions/bad_session/.meta.json": [],
            "/local/acct_a/user/user_b/sessions/good_session/.meta.json": _meta(),
        },
    )
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    with caplog.at_level(logging.WARNING, logger="openviking.service.session_auto_commit"):
        await scheduler._scan_once()

    assert "Session auto-commit scheduler loop failed" not in caplog.text
    assert service.calls == [("good_session", "idle_timeout", "user_b")]


@pytest.mark.asyncio
async def test_scheduler_skips_malformed_due_fields_without_aborting_batch(caplog):
    service = _FakeSessionService(
        [_session_entry("bad_session"), _session_entry("good_session")],
        {
            "/local/acct_a/user/user_b/sessions/bad_session/.meta.json": _meta()
            | {"last_message_at": []},
            "/local/acct_a/user/user_b/sessions/good_session/.meta.json": _meta(),
        },
    )
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    with caplog.at_level(logging.WARNING, logger="openviking.service.session_auto_commit"):
        await scheduler._scan_once()

    assert "Session auto-commit scheduler loop failed" not in caplog.text
    assert service.calls == [("good_session", "idle_timeout", "user_b")]


@pytest.mark.asyncio
async def test_scheduler_warns_for_non_missing_session_scan_errors(caplog):
    service = _FakeSessionService(
        [],
        {},
        ls_errors={"/local/acct_a/user/user_b/sessions": RuntimeError("backend down")},
    )
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    openviking_logger = logging.getLogger("openviking")
    old_propagate = openviking_logger.propagate
    openviking_logger.propagate = True
    try:
        with caplog.at_level(logging.WARNING, logger="openviking.service.session_auto_commit"):
            await scheduler._scan_once()
    finally:
        openviking_logger.propagate = old_propagate

    assert "Failed to scan sessions for idle auto-commit" in caplog.text
    assert service.calls == []


@pytest.mark.asyncio
async def test_scheduler_reuses_async_agfs_client_between_scans(monkeypatch):
    service = _FakeSessionService(
        [_session_entry("session_due")],
        {
            "/local/acct_a/user/user_b/sessions/session_due/.meta.json": _meta(),
        },
    )
    created_clients = 0
    real_client = auto_commit_module.AsyncAGFSClient

    class _CountingAsyncAGFSClient(real_client):
        def __init__(self, client):
            nonlocal created_clients
            created_clients += 1
            super().__init__(client)

    monkeypatch.setattr(auto_commit_module, "AsyncAGFSClient", _CountingAsyncAGFSClient)
    scheduler = SessionAutoCommitScheduler(
        service,
        SimpleNamespace(idle_enabled=True, check_interval_seconds=60.0),
        check_interval=60.0,
    )

    await scheduler._scan_once()
    await scheduler._scan_once()

    assert created_clients == 1
