# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for opt-in session disk retention (GC).

Covers the pure planning layer (``openviking.session.disk_gc``), the
executor (``openviking.service.session_disk_gc``), and the
disabled-by-default contract.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest

try:  # Keep imports working in envs without the Ark runtime SDK.
    import volcenginesdkarkruntime  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    import sys
    from types import ModuleType

    sys.modules["volcenginesdkarkruntime"] = ModuleType("volcenginesdkarkruntime")

from openviking.server.identity import RequestContext
from openviking.service.session_disk_gc import SessionDiskGcScheduler
from openviking.session.disk_gc import (
    ArchiveInfo,
    GcAction,
    SessionDiskState,
    plan_disk_gc,
)
from openviking_cli.utils.config.memory_config import MemoryConfig, SessionDiskGcConfig

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


def _days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


# ---------------------------------------------------------------------------
# Pure policy tests
# ---------------------------------------------------------------------------


def test_plan_deletes_over_age_archives_but_keeps_newest():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1",
        session_id="s1",
        last_activity=_days_ago(1),
        archives=(
            ArchiveInfo("archive_001", _days_ago(40)),
            ArchiveInfo("archive_002", _days_ago(35)),
            ArchiveInfo("archive_003", _days_ago(3)),
        ),
    )
    actions = plan_disk_gc([session], now=NOW, archive_max_age_days=30, max_idle_days=0)
    assert [(a.uri, a.reason) for a in actions] == [
        ("viking://user/u/sessions/s1/history/archive_001", "archive_over_age"),
        ("viking://user/u/sessions/s1/history/archive_002", "archive_over_age"),
    ]


def test_plan_keeps_fresh_archives():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1",
        session_id="s1",
        last_activity=_days_ago(1),
        archives=(ArchiveInfo("archive_001", _days_ago(2)),),
    )
    assert plan_disk_gc([session], now=NOW, archive_max_age_days=30) == []


def test_plan_keeps_archive_at_exact_age_boundary():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1",
        session_id="s1",
        last_activity=_days_ago(1),
        archives=(ArchiveInfo("archive_001", _days_ago(10)),),
    )
    assert plan_disk_gc([session], now=NOW, archive_max_age_days=10) == []


def test_plan_idle_session_deleted_whole_and_archives_not_listed():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1",
        session_id="s1",
        last_activity=_days_ago(90),
        archives=(ArchiveInfo("archive_001", _days_ago(80)),),
    )
    actions = plan_disk_gc([session], now=NOW, archive_max_age_days=30, max_idle_days=30)
    assert actions == [
        GcAction(
            uri="viking://user/u/sessions/s1",
            session_id="s1",
            reason="session_idle_expired",
        )
    ]


def test_plan_skips_active_sessions_entirely():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1",
        session_id="s1",
        last_activity=_days_ago(90),
        is_active=True,
        archives=(ArchiveInfo("archive_001", _days_ago(80)),),
    )
    assert plan_disk_gc([session], now=NOW, archive_max_age_days=30, max_idle_days=30) == []


def test_plan_disabled_thresholds_is_noop():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1",
        session_id="s1",
        last_activity=_days_ago(365),
        archives=(ArchiveInfo("archive_001", _days_ago(300)),),
    )
    assert plan_disk_gc([session], now=NOW) == []


def test_plan_keeps_archives_with_unknown_mtime():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1",
        session_id="s1",
        last_activity=_days_ago(1),
        archives=(
            ArchiveInfo("archive_001", None),
            ArchiveInfo("archive_002", _days_ago(40)),
            ArchiveInfo("archive_003", None),
        ),
    )
    actions = plan_disk_gc([session], now=NOW, archive_max_age_days=30)
    assert [a.uri for a in actions] == ["viking://user/u/sessions/s1/history/archive_002"]


def test_plan_skips_session_with_unknown_last_activity_for_idle_rule():
    session = SessionDiskState(
        uri="viking://user/u/sessions/s1", session_id="s1", last_activity=None
    )
    assert plan_disk_gc([session], now=NOW, max_idle_days=1) == []


# ---------------------------------------------------------------------------
# Fake stack for executor tests
# ---------------------------------------------------------------------------


class _FakeAgfs:
    """Sync AGFS surface; AsyncAGFSClient wraps it via to_thread."""

    def __init__(
        self,
        tree: Dict[str, List[Dict[str, Any]]],
        stats: Dict[str, Dict[str, Any]],
        errors: Optional[Dict[str, BaseException]] = None,
    ) -> None:
        self._tree = tree
        self._stats = stats
        self._errors = errors or {}
        self.ls_calls: List[str] = []

    def ls(self, path: str, ctx: Any = None) -> List[Dict[str, Any]]:
        self.ls_calls.append(path)
        if path in self._errors:
            raise self._errors[path]
        entries = self._tree.get(path)
        if entries is None:
            raise FileNotFoundError(path)
        return [dict(entry) for entry in entries]

    def stat(self, path: str, ctx: Any = None) -> Dict[str, Any]:
        if path in self._errors:
            raise self._errors[path]
        if path not in self._stats:
            raise FileNotFoundError(path)
        return dict(self._stats[path])


class _FakeVikingFS:
    def __init__(self, agfs: _FakeAgfs) -> None:
        self.agfs = agfs
        self.rm_calls: List[Tuple[str, bool, Any]] = []

    async def rm(self, uri: str, recursive: bool = False, ctx: Any = None, **kwargs: Any):
        self.rm_calls.append((uri, recursive, ctx))
        return {"estimated_deleted_count": 1}


class _FakeSessionService:
    def __init__(self, viking_fs: _FakeVikingFS) -> None:
        self.viking_fs = viking_fs


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _build_fake_stack() -> Tuple[_FakeSessionService, _FakeAgfs, _FakeVikingFS]:
    sessions_root = "/local/acct_a/user/user_b/sessions"
    idle_root = f"{sessions_root}/sess_idle"
    live_root = f"{sessions_root}/sess_live"
    tree: Dict[str, List[Dict[str, Any]]] = {
        "/local": [
            {"name": "acct_a", "isDir": True},
            {"name": "_system", "isDir": True},
        ],
        "/local/acct_a/user": [{"name": "user_b", "isDir": True}],
        sessions_root: [
            {"name": "sess_idle", "isDir": True, "modTime": _iso(_days_ago(90))},
            {"name": "sess_live", "isDir": True, "modTime": _iso(_days_ago(1))},
        ],
        f"{idle_root}/history": [
            {"name": "archive_001", "isDir": True, "modTime": _iso(_days_ago(100))},
            {"name": "archive_002", "isDir": True, "modTime": _iso(_days_ago(90))},
        ],
        f"{live_root}/history": [
            {"name": "archive_001", "isDir": True, "modTime": _iso(_days_ago(90))},
            {"name": "archive_002", "isDir": True, "modTime": _iso(_days_ago(85))},
            {"name": "not_an_archive", "isDir": True, "modTime": _iso(_days_ago(90))},
        ],
    }
    stats = {
        f"{idle_root}/.meta.json": {"modTime": _iso(_days_ago(90))},
        f"{live_root}/.meta.json": {"modTime": _iso(_days_ago(0.5))},
    }
    agfs = _FakeAgfs(tree, stats)
    viking_fs = _FakeVikingFS(agfs)
    return _FakeSessionService(viking_fs), agfs, viking_fs


def _gc_config(**overrides: Any) -> SessionDiskGcConfig:
    values: Dict[str, Any] = {
        "enabled": True,
        "max_idle_days": 30,
        "archive_max_age_days": 30,
        "dry_run": False,
    }
    values.update(overrides)
    return SessionDiskGcConfig(**values)


async def _idle_checker(account_id: str, user_id: str, session_id: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# Executor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_once_executes_plan_via_viking_fs_rm():
    service, agfs, viking_fs = _build_fake_stack()
    scheduler = SessionDiskGcScheduler(service, _gc_config(), activity_checker=_idle_checker)
    result = await scheduler.scan_once()

    # sess_idle is idle beyond max_idle_days -> whole-session delete only.
    # sess_live stays, but its over-age archive_001 goes; archive_002 is the
    # newest archive so it is always kept.
    assert sorted(call[0] for call in viking_fs.rm_calls) == [
        "viking://user/user_b/sessions/sess_idle",
        "viking://user/user_b/sessions/sess_live/history/archive_001",
    ]
    assert all(call[1] is True for call in viking_fs.rm_calls)
    ctxs = [call[2] for call in viking_fs.rm_calls]
    assert all(isinstance(ctx, RequestContext) for ctx in ctxs)
    assert all(ctx.user.account_id == "acct_a" for ctx in ctxs)
    assert all(ctx.user.user_id == "user_b" for ctx in ctxs)
    assert result == {
        "sessions_scanned": 2,
        "archives_planned": 1,
        "sessions_planned": 1,
        "deleted": 2,
        "failed": 0,
        "dry_run": 0,
    }


@pytest.mark.asyncio
async def test_scan_once_dry_run_does_not_delete():
    service, agfs, viking_fs = _build_fake_stack()
    scheduler = SessionDiskGcScheduler(
        service, _gc_config(dry_run=True), activity_checker=_idle_checker
    )
    result = await scheduler.scan_once()
    assert viking_fs.rm_calls == []
    assert result["dry_run"] == 1
    assert result["deleted"] == 0
    assert result["archives_planned"] == 1
    assert result["sessions_planned"] == 1


@pytest.mark.asyncio
async def test_scan_once_skips_active_sessions():
    service, agfs, viking_fs = _build_fake_stack()

    async def always_active(account_id: str, user_id: str, session_id: str) -> bool:
        return True

    scheduler = SessionDiskGcScheduler(service, _gc_config(), activity_checker=always_active)
    result = await scheduler.scan_once()
    assert viking_fs.rm_calls == []
    assert result["deleted"] == 0
    assert result["archives_planned"] == 0
    assert result["sessions_planned"] == 0


@pytest.mark.asyncio
async def test_scan_once_continues_when_single_delete_fails():
    service, agfs, viking_fs = _build_fake_stack()
    original_rm = viking_fs.rm

    async def flaky_rm(uri: str, recursive: bool = False, ctx: Any = None, **kw: Any):
        if uri.endswith("sess_idle"):
            raise RuntimeError("locked")
        return await original_rm(uri, recursive=recursive, ctx=ctx, **kw)

    viking_fs.rm = flaky_rm
    scheduler = SessionDiskGcScheduler(service, _gc_config(), activity_checker=_idle_checker)
    result = await scheduler.scan_once()
    assert result["failed"] == 1
    assert result["deleted"] == 1
    assert sorted(call[0] for call in viking_fs.rm_calls) == [
        "viking://user/user_b/sessions/sess_live/history/archive_001"
    ]


@pytest.mark.asyncio
async def test_scan_once_handles_missing_history_and_meta():
    sessions_root = "/local/acct_a/user/user_b/sessions"
    tree: Dict[str, List[Dict[str, Any]]] = {
        "/local": [{"name": "acct_a", "isDir": True}],
        "/local/acct_a/user": [{"name": "user_b", "isDir": True}],
        sessions_root: [{"name": "sess_bare", "isDir": True, "modTime": _iso(_days_ago(200))}],
    }
    agfs = _FakeAgfs(tree, stats={})
    viking_fs = _FakeVikingFS(agfs)
    scheduler = SessionDiskGcScheduler(
        _FakeSessionService(viking_fs),
        _gc_config(),
        activity_checker=_idle_checker,
    )
    result = await scheduler.scan_once()
    # Session dir mtime alone (200d) still proves idleness.
    assert result["sessions_scanned"] == 1
    assert result["sessions_planned"] == 1
    assert result["deleted"] == 1
    assert viking_fs.rm_calls[0][0] == "viking://user/user_b/sessions/sess_bare"


# ---------------------------------------------------------------------------
# Disabled-by-default contract
# ---------------------------------------------------------------------------


def test_default_config_is_disabled():
    assert SessionDiskGcConfig().enabled is False
    assert MemoryConfig().session_gc.enabled is False
    assert SessionDiskGcConfig().interval_secs == 3600.0
    assert SessionDiskGcConfig().max_idle_days == 0.0
    assert SessionDiskGcConfig().archive_max_age_days == 0.0
    assert SessionDiskGcConfig().dry_run is False


@pytest.mark.asyncio
async def test_run_loop_skips_scan_when_disabled():
    service, agfs, viking_fs = _build_fake_stack()
    scheduler = SessionDiskGcScheduler(service, SessionDiskGcConfig(), interval_secs=0.01)
    calls: List[int] = []
    original_scan = scheduler.scan_once

    async def counting_scan() -> Dict[str, int]:
        calls.append(1)
        return await original_scan()

    scheduler.scan_once = counting_scan  # type: ignore[method-assign]
    await scheduler.start()
    await asyncio.sleep(0.05)
    await scheduler.stop()
    assert calls == []
    assert viking_fs.rm_calls == []
